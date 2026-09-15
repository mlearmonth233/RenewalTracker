"""Application configuration, driven by environment variables."""
import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///renewaltracker.db"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024  # 5 MB upload limit for .eml files

    # Apply pending database migrations automatically on start-up so that
    # launcher/executable users never need to run `flask db upgrade`.
    AUTO_MIGRATE = _env_bool("AUTO_MIGRATE", True)

    # Dates such as 03/04/2026 are ambiguous. True reads them as day/month
    # (UK, EU, AU), False as month/day (US).
    DATE_DAY_FIRST = _env_bool("DATE_DAY_FIRST", True)

    # How often the background scheduler checks for upcoming renewals.
    CHECK_INTERVAL_HOURS = float(os.environ.get("CHECK_INTERVAL_HOURS", "6"))

    # Outgoing e-mail for alert notifications. Leave SMTP_HOST unset to keep
    # alerts in-app only.
    SMTP_HOST = os.environ.get("SMTP_HOST")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
    SMTP_USE_TLS = _env_bool("SMTP_USE_TLS", True)
    MAIL_FROM = os.environ.get("MAIL_FROM", "renewaltracker@localhost")

    # Browser push notifications (Web Push / VAPID). If VAPID_PRIVATE_KEY is
    # unset a key pair is generated once and stored in instance/vapid.json.
    PUSH_ENABLED = _env_bool("PUSH_ENABLED", True)
    VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY")  # base64url raw key or PEM
    VAPID_SUBJECT = os.environ.get("VAPID_SUBJECT", "mailto:renewaltracker@localhost")


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    DATE_DAY_FIRST = True
    SMTP_HOST = None
    # Tests build the schema with create_all for speed; test_migrations.py
    # separately proves the migrations produce the same schema.
    AUTO_MIGRATE = False
