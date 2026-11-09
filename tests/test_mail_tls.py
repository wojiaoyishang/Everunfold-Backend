"""Real local TLS sockets protect SMTP host identity and prevent plaintext fallback."""

import shutil
import socketserver
import ssl
import subprocess
import threading
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.settings import mail_transport
from app.modules.settings.mail_schemas import MailPreferences


@pytest.fixture(scope="module")
def tls_material(tmp_path_factory):
    if not shutil.which("openssl"):
        pytest.skip("The local TLS regression needs the openssl test utility")
    directory = tmp_path_factory.mktemp("mail-tls")
    ca, ca_key = directory / "ca.pem", directory / "ca.key"
    certificate, key = directory / "server.pem", directory / "server.key"
    request = directory / "server.csr"
    extensions = directory / "server.ext"
    extensions.write_text(
        "subjectAltName=DNS:localhost\n"
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
    )
    commands = [
        [
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=Everunfold isolated SMTP test CA",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca),
            "-addext",
            "basicConstraints=critical,CA:TRUE",
        ],
        [
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            "/CN=localhost",
            "-keyout",
            str(key),
            "-out",
            str(request),
        ],
        [
            "x509",
            "-req",
            "-in",
            str(request),
            "-CA",
            str(ca),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            str(extensions),
            "-out",
            str(certificate),
        ],
    ]
    for command in commands:
        subprocess.run(["openssl", *command], check=True, capture_output=True, timeout=20)
    return ca, certificate, key


@contextmanager
def tls_smtp(certificate: Path, key: Path, *, implicit: bool):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certificate, key)
    captured = {"sni": [], "commands": [], "messages": [], "tls_errors": []}
    context.set_servername_callback(lambda _socket, name, _context: captured["sni"].append(name))

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            connection = self.request
            stream = None
            encrypted = implicit
            connection.settimeout(5)
            try:
                if implicit:
                    connection = context.wrap_socket(connection, server_side=True)
                stream = connection.makefile("rb", buffering=0)
                connection.sendall(b"220 local SMTP TLS regression\r\n")
                message = None
                while line := stream.readline():
                    if message is not None:
                        if line == b".\r\n":
                            captured["messages"].append(bytes(message))
                            connection.sendall(b"250 test message accepted\r\n")
                            message = None
                        else:
                            message.extend(line)
                        continue
                    command = line.split(b" ", 1)[0].strip().upper()
                    captured["commands"].append((command, encrypted))
                    if command in {b"EHLO", b"HELO"}:
                        connection.sendall(b"250-localhost\r\n250-STARTTLS\r\n250 AUTH PLAIN\r\n")
                    elif command == b"STARTTLS":
                        connection.sendall(b"220 Ready to start TLS\r\n")
                        stream.close()
                        connection = context.wrap_socket(connection, server_side=True)
                        stream = connection.makefile("rb", buffering=0)
                        encrypted = True
                    elif command == b"AUTH":
                        connection.sendall(b"235 authenticated\r\n")
                    elif command == b"DATA":
                        connection.sendall(b"354 go ahead\r\n")
                        message = bytearray()
                    else:
                        connection.sendall(b"250 OK\r\n")
            except (ssl.SSLError, OSError) as error:
                # Certificate-rejection tests deliberately end the server handshake.
                captured["tls_errors"].append(type(error).__name__)
            finally:
                if stream is not None:
                    with suppress(OSError):
                        stream.close()
                with suppress(OSError):
                    connection.close()

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = False

    with Server(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], captured
        finally:
            server.shutdown()
            thread.join(timeout=3)


@pytest.mark.parametrize("encryption", ["starttls", "tls"])
def test_tls_smtp_verifies_hostname_and_delivers_message(tls_material, monkeypatch, encryption):
    ca, certificate, key = tls_material
    original_context = ssl.create_default_context
    contexts = []

    def trusted_context():
        context = original_context(cafile=str(ca))
        contexts.append(context)
        return context

    monkeypatch.setattr(mail_transport.ssl, "create_default_context", trusted_context)
    events = []
    with tls_smtp(certificate, key, implicit=encryption == "tls") as (port, captured):
        value = MailPreferences(
            host="localhost",
            port=port,
            encryption=encryption,
            authenticate=True,
            username="tls-regression-user",
            from_address="sender@example.test",
        )
        outcome = mail_transport.send_test(
            value, "tls-regression-password", "reader@example.test", uuid4(), events.append
        )
    assert outcome == ("sent", None)
    assert captured["sni"] == ["localhost"]
    assert contexts and all(context.check_hostname for context in contexts)
    assert all(context.verify_mode == ssl.CERT_REQUIRED for context in contexts)
    assert len(captured["messages"]) == 1
    assert b"This test email was explicitly requested" in captured["messages"][0]
    assert all(
        encrypted for command, encrypted in captured["commands"] if command in {b"AUTH", b"DATA"}
    )
    assert captured["commands"].count((b"EHLO", True)) == 1
    if encryption == "starttls":
        assert (b"EHLO", False) in captured["commands"]
        assert (b"STARTTLS", False) in captured["commands"]
    assert not captured["tls_errors"]
    transcript = "\n".join(event.message for event in events)
    assert "certificate verification enabled" in transcript
    assert "tls-regression-password" not in transcript
    assert "AUTH [REDACTED]" in transcript


@pytest.mark.parametrize("encryption", ["starttls", "tls"])
@pytest.mark.parametrize("failure", ["hostname", "untrusted"])
def test_tls_smtp_rejects_invalid_identity_without_auth_or_data(
    tls_material, monkeypatch, encryption, failure
):
    ca, certificate, key = tls_material
    original_context = ssl.create_default_context

    def verification_context():
        return original_context(cafile=str(ca)) if failure == "hostname" else original_context()

    monkeypatch.setattr(mail_transport.ssl, "create_default_context", verification_context)
    events = []
    with tls_smtp(certificate, key, implicit=encryption == "tls") as (port, captured):
        value = MailPreferences(
            host="127.0.0.1" if failure == "hostname" else "localhost",
            port=port,
            encryption=encryption,
            authenticate=True,
            username="tls-regression-user",
            from_address="sender@example.test",
        )
        outcome = mail_transport.send_test(
            value, "tls-regression-password", "reader@example.test", uuid4(), events.append
        )
    assert outcome == ("failed", "tls")
    assert not captured["messages"]
    assert not any(
        command in {b"AUTH", b"DATA", b"MAIL", b"RCPT"} for command, _ in captured["commands"]
    )
    assert captured["tls_errors"]
    assert "tls-regression-password" not in "\n".join(event.message for event in events)


@pytest.mark.parametrize(
    ("exception", "error", "diagnostic"),
    [
        (TimeoutError("secret-exception-text"), "timeout", "SMTP operation timed out."),
        (ValueError("secret-exception-text"), "transport", "Transport exception: ValueError"),
    ],
)
def test_local_transport_failures_expose_only_safe_diagnostics(
    monkeypatch, exception, error, diagnostic
):
    def fail_connection(*_args, **_kwargs):
        raise exception

    monkeypatch.setattr(mail_transport, "LoggedSMTP", fail_connection)
    events = []
    value = MailPreferences(
        host="smtp.example.test", encryption="starttls", from_address="sender@example.test"
    )
    assert mail_transport.send_test(
        value, "private-password", "reader@example.test", uuid4(), events.append
    ) == ("failed", error)
    transcript = "\n".join(event.message for event in events)
    assert diagnostic in transcript
    assert "secret-exception-text" not in transcript
    assert "private-password" not in transcript
