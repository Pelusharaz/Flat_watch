"""Settings loaded from environment variables / a local .env file (no python-dotenv needed)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
STATIC_DIR = ROOT / "static"          # served by Streamlit at /app/static/ (photos)


def _load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _b(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # LLM: "anthropic", "openai" (any OpenAI-compatible API), or "mock"
    llm_provider: str = os.getenv("LLM_PROVIDER", "mock").lower()
    llm_model: str = os.getenv("LLM_MODEL", "")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "")

    # Optional Telegram push for instant alerts (in addition to email)
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Email (SMTP). Default = the built-in test mail server on localhost:1025 (see LOCAL_MAILSERVER).
    # Empty SMTP_HOST = no SMTP at all: emails are written to data/outbox/
    smtp_host: str = os.getenv("SMTP_HOST", "localhost")
    smtp_port: int = int(os.getenv("SMTP_PORT", "1025"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from: str = os.getenv("SMTP_FROM", "Flat-Watch <no-reply@flatwatch.local>")
    smtp_starttls: bool = _b("SMTP_STARTTLS", "0")
    # Built-in test mail server (tools/mailserver.py): catches all mail on SMTP_HOST:SMTP_PORT
    local_mailserver: bool = _b("LOCAL_MAILSERVER", "1")
    app_base_url: str = os.getenv("APP_BASE_URL", "http://localhost:8501").rstrip("/")
    # Show verification / reset codes on screen when no SMTP is configured. NEVER enable in production.
    dev_show_codes: bool = _b("DEV_SHOW_CODES", "1")

    # Security
    session_days: int = int(os.getenv("SESSION_DAYS", "7"))
    max_failed_logins: int = int(os.getenv("MAX_FAILED_LOGINS", "5"))
    lockout_minutes: int = int(os.getenv("LOCKOUT_MINUTES", "15"))

    # Background worker
    worker_in_app: bool = _b("WORKER_IN_APP", "1")     # run the scheduler inside the Streamlit process
    worker_interval_s: int = int(os.getenv("WORKER_INTERVAL_S", "30"))
    demo_feed: bool = _b("DEMO_FEED", "1")             # release held-back sample listings over time
    reverify_days: int = int(os.getenv("REVERIFY_DAYS", "14"))
    expire_days: int = int(os.getenv("EXPIRE_DAYS", "30"))

    db_path: str = os.getenv("DB_PATH", str(ROOT / "flatwatch.db"))
    listings_file: str = os.getenv("LISTINGS_FILE", str(DATA_DIR / "listings.json"))
    districts_file: str = os.getenv("DISTRICTS_FILE", str(DATA_DIR / "districts.json"))
    geocode: bool = _b("GEOCODE", "1")
    include_samples: bool = _b("INCLUDE_SAMPLES", "1")
    my_listings_file: str = os.getenv("MY_LISTINGS_FILE", str(DATA_DIR / "my_listings.json"))
    feed_interval_s: float = float(os.getenv("FEED_INTERVAL_S", "20"))
    underprice_ratio: float = float(os.getenv("UNDERPRICE_RATIO", "0.6"))

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host)


settings = Settings()
