"""Built-in TEST mail server (a "mail catcher").

A tiny, standards-compliant SMTP server (RFC 5321 subset: EHLO/HELO, MAIL, RCPT, DATA, RSET,
NOOP, QUIT) that accepts every email and stores it as .eml in data/mailbox/. The app sends
through it over real SMTP, so the whole delivery path is exercised – and the 📬 Test inbox page
shows the messages exactly as a mail client would.

It never relays mail to the internet. For production set SMTP_HOST to a real provider.
Alternative: Mailpit (https://mailpit.axllent.org) – `docker run -p 8025:8025 -p 1025:1025 axllent/mailpit`.
"""
from __future__ import annotations

import email
import socketserver
import threading
import time
from email import policy
from pathlib import Path
from typing import Optional

from .config import DATA_DIR

MAILBOX = DATA_DIR / "mailbox"
MAX_SIZE = 10 * 1024 * 1024


class _Handler(socketserver.StreamRequestHandler):
    timeout = 60

    def _send(self, line: str) -> None:
        self.wfile.write((line + "\r\n").encode())

    def handle(self) -> None:
        self._send("220 flatwatch-test-mailserver ESMTP ready")
        sender, rcpts = None, []
        while True:
            raw = self.rfile.readline(4096)
            if not raw:
                return
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            cmd = line[:4].upper()
            if cmd == "EHLO":
                self._send("250-flatwatch-test-mailserver")
                self._send(f"250-SIZE {MAX_SIZE}")
                self._send("250-8BITMIME")
                self._send("250 SMTPUTF8")
            elif cmd == "HELO":
                self._send("250 flatwatch-test-mailserver")
            elif cmd == "MAIL":
                sender, rcpts = line.split(":", 1)[1].strip().split(" ")[0].strip("<>"), []
                self._send("250 OK")
            elif cmd == "RCPT":
                rcpts.append(line.split(":", 1)[1].strip().split(" ")[0].strip("<>"))
                self._send("250 OK")
            elif cmd == "DATA":
                if not rcpts:
                    self._send("503 Need RCPT first")
                    continue
                self._send("354 End data with <CR><LF>.<CR><LF>")
                chunks, size = [], 0
                while True:
                    l = self.rfile.readline(65536)
                    if not l or l in (b".\r\n", b".\n"):
                        break
                    if l.startswith(b".."):
                        l = l[1:]                       # dot-unstuffing
                    size += len(l)
                    if size > MAX_SIZE:
                        self._send("552 Message too large")
                        return
                    chunks.append(l)
                _store(b"".join(chunks), sender, rcpts)
                self._send("250 OK: queued")
                sender, rcpts = None, []
            elif cmd == "RSET":
                sender, rcpts = None, []
                self._send("250 OK")
            elif cmd == "NOOP":
                self._send("250 OK")
            elif cmd == "QUIT":
                self._send("221 Bye")
                return
            else:
                self._send("502 Command not implemented")


def _store(data: bytes, sender: Optional[str], rcpts: list[str]) -> Path:
    MAILBOX.mkdir(parents=True, exist_ok=True)
    name = f"{time.time():.6f}".replace(".", "_") + ".eml"
    path = MAILBOX / name
    header = f"X-Envelope-From: {sender}\r\nX-Envelope-To: {', '.join(rcpts)}\r\n".encode()
    path.write_bytes(header + data)
    return path


class _Server(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


_server: Optional[_Server] = None


def start(host: str = "127.0.0.1", port: int = 1025) -> bool:
    """Start in a background thread. Returns False if the port is taken (e.g. already running)."""
    global _server
    if _server:
        return True
    try:
        _server = _Server((host, port), _Handler)
    except OSError:
        return False
    threading.Thread(target=_server.serve_forever, daemon=True, name="test-mailserver").start()
    return True


def stop() -> None:
    global _server
    if _server:
        _server.shutdown()
        _server.server_close()
        _server = None


# ------------------------------------------------------------------- reading --
def inbox(to: Optional[str] = None, limit: int = 200) -> list[dict]:
    out = []
    files = sorted(MAILBOX.glob("*.eml"), reverse=True) if MAILBOX.exists() else []
    for f in files[: limit * 3]:
        msg = email.message_from_bytes(f.read_bytes(), policy=policy.default)
        rcpt = str(msg.get("To", ""))
        if to and to.lower() not in rcpt.lower():
            continue
        html = msg.get_body(preferencelist=("html",))
        text = msg.get_body(preferencelist=("plain",))
        out.append({"id": f.stem, "to": rcpt, "from": str(msg.get("From", "")), "subject": str(msg.get("Subject", "")),
                    "date": f.stat().st_mtime, "html": html.get_content() if html else "",
                    "text": text.get_content() if text else ""})
        if len(out) >= limit:
            break
    return out


def clear() -> int:
    n = 0
    for f in MAILBOX.glob("*.eml") if MAILBOX.exists() else []:
        f.unlink()
        n += 1
    return n
