"""Run the background scheduler on its own (production setup):   python worker.py

Keeps matching new listings against saved searches, sends instant alerts and digests,
reminds landlords to reconfirm availability and delivers the email queue."""
from flatwatch.bootstrap import bootstrap
from flatwatch.config import settings
from flatwatch.db import get_db
from flatwatch.worker import run_cycle
import time

if __name__ == "__main__":
    db = get_db()
    bootstrap(db)
    print(f"Flat-Watch worker running every {settings.worker_interval_s}s (Ctrl+C to stop)")
    while True:
        report = run_cycle(db)
        print(time.strftime("%H:%M:%S"), report)
        time.sleep(settings.worker_interval_s)
