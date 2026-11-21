import os

# Tests must not hit the network
os.environ["GEOCODE"] = "0"
os.environ["LLM_PROVIDER"] = "mock"
os.environ["TELEGRAM_BOT_TOKEN"] = ""
os.environ["SMTP_HOST"] = ""            # tests write emails to a temp outbox, no SMTP
os.environ["LOCAL_MAILSERVER"] = "0"
