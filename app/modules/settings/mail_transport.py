"""Bounded SMTP test transport. Raw server replies and credentials never become public errors."""

import smtplib
import ssl
import time
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from uuid import UUID

from app.modules.settings.mail_schemas import MailPreferences


def send_test(
    value: MailPreferences, password: str, recipient: str, identity: UUID
) -> tuple[str, str | None]:
    client: smtplib.SMTP | None = None
    in_data = False
    deadline = time.monotonic() + min(120, max(15, value.timeout_seconds * 4))

    def remaining() -> float:
        seconds = min(value.timeout_seconds, deadline - time.monotonic())
        if seconds <= 0:
            raise TimeoutError
        if client and client.sock:
            client.sock.settimeout(seconds)
        return seconds

    try:
        context = ssl.create_default_context()
        if value.encryption == "tls":
            client = smtplib.SMTP_SSL(timeout=remaining(), context=context)
        else:
            client = smtplib.SMTP(timeout=remaining())
        client.connect(value.host, value.port)
        remaining()
        client.ehlo_or_helo_if_needed()
        if value.encryption == "starttls":
            remaining()
            client.starttls(context=context)
            remaining()
            client.ehlo()
        if value.authenticate:
            remaining()
            client.login(value.username, password)
        message = EmailMessage()
        message["Subject"] = "Everunfold 邮件发送测试 / Email delivery test"
        message["From"] = formataddr((value.from_name, value.from_address))
        message["To"] = recipient
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = f"<{identity}@everunfold.test>"
        if value.reply_to:
            message["Reply-To"] = value.reply_to
        message.set_content(
            "这是一封由管理员主动发起的 Everunfold 测试邮件。\n\n"
            "This test email was explicitly requested by an administrator.\n"
        )
        remaining()
        # Perform envelope commands separately so a lost DATA reply is never blindly retried.
        code, _ = client.mail(value.from_address)
        if code != 250:
            return "failed", "sender_rejected"
        remaining()
        code, _ = client.rcpt(recipient)
        if code not in (250, 251):
            return "failed", "recipient_rejected"
        remaining()
        in_data = True
        code, _ = client.data(message.as_bytes(policy=policy.SMTP))
        return ("sent", None) if code == 250 else ("failed", "message_rejected")
    except smtplib.SMTPAuthenticationError:
        return "failed", "authentication"
    except ssl.SSLError:
        return "failed", "tls"
    except smtplib.SMTPNotSupportedError:
        return "failed", "unsupported"
    except smtplib.SMTPDataError:
        return "failed", "message_rejected"
    except (OSError, smtplib.SMTPException):
        return ("unknown", "delivery_unknown") if in_data else ("failed", "connection")
    except Exception:
        return ("unknown", "delivery_unknown") if in_data else ("failed", "transport")
    finally:
        if client:
            client.close()
