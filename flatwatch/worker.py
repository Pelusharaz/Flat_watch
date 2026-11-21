"""Background scheduler: keeps searching after the user closes the browser.

Run standalone (recommended for production):   python worker.py
Or inside the Streamlit process (default, for demos): WORKER_IN_APP=1

Every cycle:
  1. ingest   – (demo) release the next held-back sample listing; partner feeds plug in here
  2. match    – score new listings against every active saved search -> instant emails / digest
  3. digests  – send due daily / weekly summaries
  4. freshness– remind landlords after REVERIFY_DAYS, expire after EXPIRE_DAYS
  5. deliver  – send the email queue (SMTP or data/outbox/)
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from . import emails, listings, mailer, searches
from .config import settings


def freshness(db, now: Optional[float] = None) -> dict:
    now = now or time.time()
    out = {"reminded": 0, "expired": 0}
    remind_before = now - settings.reverify_days * 86_400
    expire_before = now - settings.expire_days * 86_400
    for r in db.query("SELECT l.*, u.email, u.name FROM listings l JOIN users u ON u.id=l.owner_id "
                      "WHERE l.status='active' AND l.last_verified_at < ?", (remind_before,)):
        if r["last_verified_at"] < expire_before:
            listings.set_status(db, r["id"], "expired")
            out["expired"] += 1
        elif not r["reminder_sent_at"]:
            import json
            title = json.loads(r["payload"]).get("title", r["id"])
            subject, html, text = emails.reconfirm_email(r["name"], title, r["reconfirm_token"])
            mailer.enqueue(db, r["email"], subject, html, text, dedupe_key=f"reconfirm:{r['id']}:{int(r['last_verified_at'])}")
            db.execute("UPDATE listings SET reminder_sent_at=? WHERE id=?", (now, r["id"]))
            out["reminded"] += 1
    # site listings (no owner) can't be reconfirmed: expire them after EXPIRE_DAYS
    cur = db.execute("UPDATE listings SET status='expired' WHERE owner_id IS NULL AND sample=0 AND status='active' "
                     "AND last_verified_at < ?", (expire_before,))
    out["expired"] += cur.rowcount
    return out


def run_cycle(db) -> dict:
    report = {"released": None}
    if settings.demo_feed:
        report["released"] = listings.release_next_queued(db)
    report["match"] = searches.run_new_listings(db)
    report["digests"] = searches.send_digests(db)
    report["freshness"] = freshness(db)
    report["emails_sent"] = mailer.process_queue(db)
    db.kv_set("worker_last_run", {"at": time.time(), "report": report})
    return report


class Worker:
    def __init__(self, db, interval_s: Optional[int] = None):
        self.db = db
        self.interval_s = interval_s or settings.worker_interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if not self.running:
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="flatwatch-worker")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                run_cycle(self.db)
            except Exception as exc:  # noqa: BLE001 - never die
                print(f"[worker] cycle failed: {exc}")
            self._stop.wait(self.interval_s)
