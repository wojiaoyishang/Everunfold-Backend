"""SMTP test transport with bounded, credential-free protocol diagnostics."""

import base64
import smtplib
import ssl
import time
import unicodedata
from collections.abc import Buffer, Callable
from contextlib import suppress
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from typing import Literal
from uuid import UUID

from app.modules.settings.mail_schemas import MailPreferences


@dataclass(frozen=True)
class MailEvent:
    stage: str
    kind: Literal["stage", "request", "response", "message"]
    message: str
    level: Literal["info", "warning", "error"] = "info"


class Transcript:
    """Do not enable smtplib debug output: AUTH would disclose reusable credentials."""

    def __init__(
        self, username: str, password: str, callback: Callable[[MailEvent], None] | None
    ) -> None:
        self.callback = callback
        self.stage = "connecting"
        self.authenticating = False
        self.data_ready = False
        self.count = 0
        candidates = {username, password}
        if username or password:
            for value in (
                username,
                password,
                f"\0{username}\0{password}",
                f"{username}\0{username}\0{password}",
                f"{username}:{password}",
            ):
                candidates.add(base64.b64encode(value.encode()).decode())
                candidates.add(base64.urlsafe_b64encode(value.encode()).decode())
        self.secrets = sorted((value for value in candidates if value), key=len, reverse=True)

    def clean(self, value: str | bytes) -> str:
        result = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        for secret in self.secrets:
            result = result.replace(secret, "[REDACTED]")
        result = "".join(
            character
            for character in result
            if character in "\n\t" or not unicodedata.category(character).startswith("C")
        )
        return result[:4096] + ("\n[truncated]" if len(result) > 4096 else "")

    def emit(
        self,
        kind: Literal["stage", "request", "response", "message"],
        message: str | bytes,
        level: Literal["info", "warning", "error"] = "info",
    ) -> None:
        if not self.callback or self.count >= 100:
            return
        self.count += 1
        # Diagnostics must never change whether an already accepted SMTP message is sent again.
        with suppress(Exception):
            self.callback(MailEvent(self.stage, kind, self.clean(message), level))

    def phase(self, stage: str, message: str) -> None:
        self.stage = stage
        self.emit("stage", message)

    def command(self, command: str, arguments: str) -> None:
        if self.authenticating:
            self.emit("request", "AUTH [REDACTED]")
        else:
            self.emit("request", command.upper() + (" " + arguments if arguments else ""))

    def reply(self, code: int, message: bytes) -> None:
        if self.authenticating:
            self.emit("response", f"{code} [authentication response redacted]")
        else:
            self.emit("response", str(code) + " " + self.clean(message))
        self.data_ready = not self.authenticating and code == 354

    def outgoing(self, data: bytes | str) -> None:
        if self.data_ready and not self.authenticating:
            self.data_ready = False
            self.emit("request", data)


class LoggedSMTP(smtplib.SMTP):
    trace: Transcript

    def __init__(self, host: str, port: int, *, timeout: float, trace: Transcript) -> None:
        # smtplib retains this host for STARTTLS SNI and certificate hostname checks.
        # Install tracing before the constructor reads the server greeting.
        self.trace = trace
        super().__init__(host, port, timeout=timeout)

    def putcmd(self, cmd: str, args: str = "") -> None:
        self.trace.command(cmd, args)
        super().putcmd(cmd, args)

    def getreply(self) -> tuple[int, bytes]:
        code, message = super().getreply()
        self.trace.reply(code, message)
        return code, message

    def send(self, s: Buffer | str) -> None:
        self.trace.outgoing(s if isinstance(s, str) else bytes(s))
        super().send(s)


class LoggedSMTPSSL(smtplib.SMTP_SSL):
    trace: Transcript

    def __init__(
        self, host: str, port: int, *, timeout: float, context: ssl.SSLContext, trace: Transcript
    ) -> None:
        self.trace = trace
        super().__init__(host, port, timeout=timeout, context=context)

    def putcmd(self, cmd: str, args: str = "") -> None:
        self.trace.command(cmd, args)
        super().putcmd(cmd, args)

    def getreply(self) -> tuple[int, bytes]:
        code, message = super().getreply()
        self.trace.reply(code, message)
        return code, message

    def send(self, s: Buffer | str) -> None:
        self.trace.outgoing(s if isinstance(s, str) else bytes(s))
        super().send(s)


def send_message(
    value: MailPreferences,
    password: str,
    recipient: str,
    identity: UUID,
    subject: str,
    content: str,
    on_event: Callable[[MailEvent], None] | None = None,
) -> tuple[str, str | None]:
    client: LoggedSMTP | LoggedSMTPSSL | None = None
    trace = Transcript(value.username, password, on_event)
    in_data = False
    deadline = time.monotonic() + min(120, max(15, value.timeout_seconds * 4))

    def remaining() -> float:
        seconds = min(value.timeout_seconds, deadline - time.monotonic())
        if seconds <= 0:
            raise TimeoutError
        if client and client.sock:
            client.sock.settimeout(seconds)
        return seconds

    def result(status: str, error: str | None) -> tuple[str, str | None]:
        trace.phase(
            status,
            {
                "sent": "SMTP server accepted the test message.",
                "failed": "SMTP test failed.",
                "unknown": "SMTP delivery result is unknown; do not retry automatically.",
            }[status],
        )
        if error:
            trace.emit("message", error, "warning" if status == "unknown" else "error")
        return status, error

    try:
        trace.phase("connecting", f"Connecting to {value.host}:{value.port} ({value.encryption}).")
        context = ssl.create_default_context()
        if value.encryption == "tls":
            client = LoggedSMTPSSL(
                value.host, value.port, timeout=remaining(), context=context, trace=trace
            )
        else:
            client = LoggedSMTP(value.host, value.port, timeout=remaining(), trace=trace)
        if value.encryption == "tls":
            trace.emit("message", "TLS connection established; certificate verification enabled.")
        remaining()
        trace.phase("greeting", "Querying SMTP capabilities.")
        client.ehlo_or_helo_if_needed()
        if value.encryption == "starttls":
            remaining()
            trace.phase("encryption", "Negotiating STARTTLS with certificate verification.")
            client.starttls(context=context)
            trace.emit("message", "TLS connection established; certificate verification enabled.")
            remaining()
            client.ehlo()
        if value.authenticate:
            remaining()
            trace.phase(
                "authentication", "Authenticating; credentials and challenges are redacted."
            )
            trace.authenticating = True
            try:
                client.login(value.username, password)
            finally:
                trace.authenticating = False
            trace.emit("message", "SMTP authentication succeeded.")
        else:
            trace.emit("message", "SMTP authentication is disabled in the saved configuration.")
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr((value.from_name, value.from_address))
        message["To"] = recipient
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = f"<{identity}@everunfold.test>"
        if value.reply_to:
            message["Reply-To"] = value.reply_to
        message.set_content(content)
        remaining()
        trace.phase("envelope", "Submitting the sender and recipient envelope.")
        code, _ = client.mail(value.from_address)
        if code != 250:
            return result("failed", "sender_rejected")
        remaining()
        code, _ = client.rcpt(recipient)
        if code not in (250, 251):
            return result("failed", "recipient_rejected")
        remaining()
        trace.phase("message", "Submitting the MIME message and awaiting SMTP acceptance.")
        in_data = True
        code, _ = client.data(message.as_bytes(policy=policy.SMTP))
        return result("sent", None) if code == 250 else result("failed", "message_rejected")
    except smtplib.SMTPAuthenticationError:
        return result("failed", "authentication")
    except ssl.SSLCertVerificationError as error:
        trace.emit(
            "message", f"TLS certificate verification failed (code {error.verify_code}).", "error"
        )
        return result("failed", "tls")
    except ssl.SSLError as error:
        trace.emit("message", f"TLS handshake exception: {type(error).__name__}", "error")
        return result("failed", "tls")
    except smtplib.SMTPNotSupportedError:
        return result("failed", "unsupported")
    except smtplib.SMTPDataError:
        return result("failed", "message_rejected")
    except TimeoutError:
        trace.emit("message", "SMTP operation timed out.", "warning" if in_data else "error")
        return result("unknown", "delivery_unknown") if in_data else result("failed", "timeout")
    except (OSError, smtplib.SMTPException):
        return result("unknown", "delivery_unknown") if in_data else result("failed", "connection")
    except Exception as error:
        # The type identifies local transport bugs without exposing exception text or secrets.
        trace.emit("message", f"Transport exception: {type(error).__name__}", "error")
        return result("unknown", "delivery_unknown") if in_data else result("failed", "transport")
    finally:
        if client:
            with suppress(OSError):
                client.close()


def send_test(
    value: MailPreferences,
    password: str,
    recipient: str,
    identity: UUID,
    on_event: Callable[[MailEvent], None] | None = None,
) -> tuple[str, str | None]:
    return send_message(
        value,
        password,
        recipient,
        identity,
        "Everunfold 邮件发送测试 / Email delivery test",
        (
            "这是一封由管理员主动发起的 Everunfold 测试邮件。\n\n"
            "This test email was explicitly requested by an administrator.\n"
        ),
        on_event,
    )


def send_notification(
    value: MailPreferences,
    password: str,
    recipient: str,
    identity: UUID,
    subject: str,
    content: str,
) -> tuple[str, str | None]:
    return send_message(value, password, recipient, identity, subject, content)
