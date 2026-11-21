"""Email queue + delivery.

Everything goes through the `email_queue` table (dedupe_key prevents duplicates), and the
background worker delivers it: via SMTP when SMTP_HOST is set, otherwise into data/outbox/
as .html files (development mode) so the whole flow can be demoed without an email account."""
from __future__ import annotations

import smtplib
import ssl
import time
from email.message import EmailMessage
from typing import Optional

from .config import DATA_DIR, settings

OUTBOX = DATA_DIR / "outbox"


def enqueue(db, to: str, subject: str, html: str, text: str, dedupe_key: Optional[str] = None) -> bool:
    """Queue an email. Returns False if an email with the same dedupe_key was already queued."""
    cur = db.execute(
        "INSERT OR IGNORE INTO email_queue(to_email, subject, html, text, dedupe_key, created_at) VALUES(?,?,?,?,?,?)",
        (to, subject, html, text, dedupe_key, time.time()))
    return cur.rowcount == 1


def _send_smtp(to: str, subject: str, html: str, text: str) -> None:
    msg = EmailMessage()
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
        if settings.smtp_starttls:
            s.starttls(context=ssl.create_default_context())
        if settings.smtp_user:
            s.login(settings.smtp_user, settings.smtp_password)
        s.send_message(msg)


def _write_outbox(row) -> None:
    OUTBOX.mkdir(parents=True, exist_ok=True)
    safe_to = "".join(c if c.isalnum() else "_" for c in row["to_email"])[:40]
    (OUTBOX / f"{row['id']:05d}_{safe_to}.html").write_text(
        f"<!-- To: {row['to_email']} | Subject: {row['subject']} -->\n{row['html']}", encoding="utf-8")


def process_queue(db, limit: int = 50) -> int:
    """Deliver pending emails (max 3 attempts each). Returns how many were sent."""
    sent = 0
    for row in db.query("SELECT * FROM email_queue WHERE status='pending' ORDER BY id LIMIT ?", (limit,)):
        try:
            if settings.smtp_enabled:
                _send_smtp(row["to_email"], row["subject"], row["html"], row["text"])
            else:
                _write_outbox(row)
            db.execute("UPDATE email_queue SET status='sent', sent_at=?, attempts=attempts+1 WHERE id=?",
                       (time.time(), row["id"]))
            sent += 1
        except Exception as exc:  # noqa: BLE001
            status = "failed" if row["attempts"] + 1 >= 3 else "pending"
            db.execute("UPDATE email_queue SET status=?, attempts=attempts+1, error=? WHERE id=?",
                       (status, str(exc)[:300], row["id"]))
    return sent
