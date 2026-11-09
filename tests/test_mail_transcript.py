"""The actual SMTP conversation is useful without retaining AUTH secrets."""

import base64
import socketserver
import threading
from contextlib import contextmanager
from uuid import uuid4

from app.modules.settings.mail_schemas import MailPreferences
from app.modules.settings.mail_transport import MailEvent, Transcript, send_test


@contextmanager
def authenticated_server(username, password):
    wire_commands = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(5)
            self.wfile.write(b"220 local test server\r\n")
            authenticating = False
            data = False
            for line in self.rfile:
                wire_commands.append(line)
                if data:
                    if line == b".\r\n":
                        self.wfile.write(b"250 test message accepted\r\n")
                        data = False
                    continue
                if authenticating:
                    assert line.strip() == base64.b64encode(password.encode())
                    self.wfile.write(f"235 {username} {password}\r\n".encode())
                    authenticating = False
                elif line.upper().startswith(b"EHLO"):
                    self.wfile.write(b"250-localhost\r\n250 AUTH LOGIN\r\n")
                elif line.upper().startswith(b"AUTH LOGIN"):
                    self.wfile.write(b"334 UGFzc3dvcmQ6\r\n")
                    authenticating = True
                elif line.upper().startswith(b"MAIL"):
                    # An untrusted server may reflect credentials outside the AUTH exchange.
                    encoded = base64.b64encode(f"\0{username}\0{password}".encode()).decode()
                    self.wfile.write(f"250 {password} {encoded} \x1b[31mOK\r\n".encode())
                elif line.upper().startswith(b"DATA"):
                    self.wfile.write(b"354 send message\r\n")
                    data = True
                else:
                    self.wfile.write(b"250 OK\r\n")

    with socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address[1], wire_commands
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_authenticated_transcript_redacts_credentials_and_preserves_mime():
    username, password = "transcript-user", "transcript-password-456"
    events: list[MailEvent] = []
    with authenticated_server(username, password) as (port, wire):
        value = MailPreferences(
            host="127.0.0.1",
            port=port,
            encryption="none",
            authenticate=True,
            username=username,
            from_address="sender@example.test",
        )
        assert send_test(value, password, "reader@example.test", uuid4(), events.append) == (
            "sent",
            None,
        )
    transcript = "\n".join(event.message for event in events)
    for secret in (
        username,
        password,
        base64.b64encode(username.encode()).decode(),
        base64.b64encode(password.encode()).decode(),
        base64.b64encode(f"\0{username}\0{password}".encode()).decode(),
        "UGFzc3dvcmQ6",
    ):
        assert secret not in transcript
    assert "AUTH [REDACTED]" in transcript
    assert "235 [authentication response redacted]" in transcript
    assert "MAIL FROM:<sender@example.test>" in transcript
    assert "RCPT TO:<reader@example.test>" in transcript
    assert 'Content-Type: text/plain; charset="utf-8"' in transcript
    assert "This test email was explicitly requested by an administrator." in transcript
    assert "SMTP server accepted the test message." in transcript
    assert "\x1b" not in transcript
    assert any(line.strip() == base64.b64encode(password.encode()) for line in wire)
    kinds = [event.kind for event in events]
    assert "request" in kinds and "response" in kinds and "stage" in kinds


def test_transcript_limits_and_control_characters():
    events: list[MailEvent] = []
    transcript = Transcript("", "sensitive-token", events.append)
    for _ in range(110):
        transcript.emit("response", "sensitive-token\x00\u202e" + "x" * 5000)
    assert len(events) == 100
    assert all(len(event.message) < 4120 and "[truncated]" in event.message for event in events)
    assert all("sensitive-token" not in event.message for event in events)
    assert all("\x00" not in event.message and "\u202e" not in event.message for event in events)
