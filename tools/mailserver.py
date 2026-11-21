"""Run the built-in test mail server on its own:   python tools/mailserver.py [port]

The app starts it automatically when LOCAL_MAILSERVER=1 (default). Every email is stored in
data/mailbox/ and shown on the app's 📬 Test inbox page. Nothing is sent to the internet."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from flatwatch import mailserver  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 1025
    if not mailserver.start("127.0.0.1", port):
        sys.exit(f"Port {port} is in use (is the app already running its own test mail server?)")
    print(f"Test mail server listening on 127.0.0.1:{port} – mail is saved to {mailserver.MAILBOX}")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        mailserver.stop()
