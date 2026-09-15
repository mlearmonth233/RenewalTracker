"""Database models."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

from dateutil.relativedelta import relativedelta
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


CATEGORIES = {
    "bill": {
        "label": "Bill",
        "default_reminder_days": "7,1",
        "default_recurrence": "monthly",
    },
    "subscription": {
        "label": "Subscription",
        "default_reminder_days": "7,1",
        "default_recurrence": "monthly",
    },
    "insurance": {
        "label": "Insurance",
        "default_reminder_days": "30,7",
        "default_recurrence": "yearly",
    },
    "passport": {
        "label": "Passport / ID",
        # Many countries require six months of remaining validity.
        "default_reminder_days": "270,180,90",
        "default_recurrence": "none",
    },
    "other": {
        "label": "Other",
        "default_reminder_days": "14,3",
        "default_recurrence": "none",
    },
}

RECURRENCES = ("none", "weekly", "monthly", "quarterly", "yearly", "custom")


def parse_reminder_days(value: str | None) -> list[int]:
    """Turn '30, 7,1' into a sorted, de-duplicated list of positive ints."""
    if not value:
        return []
    days = set()
    for part in str(value).replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if n >= 0:
            days.add(n)
    return sorted(days, reverse=True)


def advance_date(current: date, recurrence: str, interval_days: int | None) -> date | None:
    """Return the next occurrence after ``current`` for the given recurrence."""
    if recurrence == "weekly":
        return current + relativedelta(weeks=1)
    if recurrence == "monthly":
        return current + relativedelta(months=1)
    if recurrence == "quarterly":
        return current + relativedelta(months=3)
    if recurrence == "yearly":
        return current + relativedelta(years=1)
    if recurrence == "custom" and interval_days:
        return current + relativedelta(days=int(interval_days))
    return None


def monthly_equivalent(amount: float | None, recurrence: str, interval_days: int | None) -> float:
    """Normalise an amount to a per-month figure for spend summaries."""
    if amount is None:
        return 0.0
    if recurrence == "weekly":
        return amount * 52 / 12
    if recurrence == "monthly":
        return amount
    if recurrence == "quarterly":
        return amount / 3
    if recurrence == "yearly":
        return amount / 12
    if recurrence == "custom" and interval_days:
        return amount * (365.25 / int(interval_days)) / 12
    return 0.0


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    notify_by_email = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    items = db.relationship("TrackedItem", backref="user", lazy=True, cascade="all, delete-orphan")
    alerts = db.relationship("Alert", backref="user", lazy=True, cascade="all, delete-orphan")
    imports = db.relationship("EmailImport", backref="user", lazy=True, cascade="all, delete-orphan")
    push_subscriptions = db.relationship("PushSubscription", backref="user", lazy=True, cascade="all, delete-orphan")

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "notify_by_email": self.notify_by_email,
        }


class TrackedItem(db.Model):
    __tablename__ = "tracked_items"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)

    name = db.Column(db.String(200), nullable=False)
    category = db.Column(db.String(30), nullable=False, default="other")
    provider = db.Column(db.String(200), nullable=True)
    reference = db.Column(db.String(120), nullable=True)

    amount = db.Column(db.Float, nullable=True)
    currency = db.Column(db.String(3), nullable=True, default="GBP")

    renewal_date = db.Column(db.Date, nullable=False, index=True)
    recurrence = db.Column(db.String(20), nullable=False, default="none")
    interval_days = db.Column(db.Integer, nullable=True)
    auto_renews = db.Column(db.Boolean, default=False, nullable=False)
    reminder_days = db.Column(db.String(60), nullable=False, default="7,1")

    notes = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(20), nullable=False, default="manual")
    archived = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    alerts = db.relationship("Alert", backref="item", lazy=True, cascade="all, delete-orphan")

    # -- helpers ---------------------------------------------------------

    def days_until_renewal(self, today: date | None = None) -> int:
        today = today or date.today()
        return (self.renewal_date - today).days

    def reminder_day_list(self) -> list[int]:
        return parse_reminder_days(self.reminder_days)

    def next_renewal_date(self) -> date | None:
        return advance_date(self.renewal_date, self.recurrence, self.interval_days)

    def mark_renewed(self, new_date: date | None = None) -> date:
        """Advance the renewal date after the item has been renewed/paid.

        For recurring items the next date is computed; if the computed date is
        still in the past (e.g. several cycles were missed) keep advancing until
        it is in the future. Non-recurring items require an explicit new date.
        """
        if new_date is not None:
            self.renewal_date = new_date
            return new_date
        nxt = self.next_renewal_date()
        if nxt is None:
            raise ValueError("A new date is required for items that do not recur.")
        today = date.today()
        while nxt <= today:
            following = advance_date(nxt, self.recurrence, self.interval_days)
            if following is None or following <= nxt:
                break
            nxt = following
        self.renewal_date = nxt
        return nxt

    def status(self, today: date | None = None) -> str:
        if self.archived:
            return "archived"
        days = self.days_until_renewal(today)
        if days < 0:
            return "overdue"
        if days == 0:
            return "due_today"
        reminders = self.reminder_day_list()
        if reminders and days <= reminders[0]:
            return "upcoming"
        return "ok"

    def to_dict(self, today: date | None = None) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "category_label": CATEGORIES.get(self.category, CATEGORIES["other"])["label"],
            "provider": self.provider,
            "reference": self.reference,
            "amount": self.amount,
            "currency": self.currency,
            "renewal_date": self.renewal_date.isoformat(),
            "recurrence": self.recurrence,
            "interval_days": self.interval_days,
            "auto_renews": self.auto_renews,
            "reminder_days": self.reminder_days,
            "notes": self.notes,
            "source": self.source,
            "archived": self.archived,
            "days_until_renewal": self.days_until_renewal(today),
            "status": self.status(today),
            "monthly_equivalent": round(
                monthly_equivalent(self.amount, self.recurrence, self.interval_days), 2
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Alert(db.Model):
    """A reminder generated for a tracked item.

    ``dedupe_key`` guarantees each (item, renewal date, stage) produces exactly
    one alert, so re-running the checker is idempotent.
    """

    __tablename__ = "alerts"
    __table_args__ = (db.UniqueConstraint("item_id", "dedupe_key", name="uq_alert_item_key"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey("tracked_items.id"), nullable=False, index=True)

    kind = db.Column(db.String(20), nullable=False)  # upcoming | due_today | overdue
    dedupe_key = db.Column(db.String(60), nullable=False)
    renewal_date = db.Column(db.Date, nullable=False)
    days_until = db.Column(db.Integer, nullable=False)
    message = db.Column(db.String(500), nullable=False)

    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    emailed_at = db.Column(db.DateTime, nullable=True)
    pushed_at = db.Column(db.DateTime, nullable=True)
    acknowledged_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "item_id": self.item_id,
            "item_name": self.item.name if self.item else None,
            "category": self.item.category if self.item else None,
            "kind": self.kind,
            "renewal_date": self.renewal_date.isoformat(),
            "days_until": self.days_until,
            "message": self.message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "emailed_at": self.emailed_at.isoformat() if self.emailed_at else None,
            "pushed_at": self.pushed_at.isoformat() if self.pushed_at else None,
            "acknowledged": self.acknowledged_at is not None,
        }


class PushSubscription(db.Model):
    """A browser's Web Push subscription (one per device/browser profile)."""

    __tablename__ = "push_subscriptions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    endpoint = db.Column(db.String(1000), nullable=False, unique=True)
    p256dh = db.Column(db.String(200), nullable=False)
    auth = db.Column(db.String(100), nullable=False)
    user_agent = db.Column(db.String(300), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    last_used_at = db.Column(db.DateTime, nullable=True)
    failures = db.Column(db.Integer, default=0, nullable=False)

    def to_dict(self) -> dict:
        # Never expose the keys or the full endpoint – the endpoint is a
        # capability URL that lets anyone holding it send pushes.
        host = self.endpoint.split("/")[2] if "//" in self.endpoint else self.endpoint[:40]
        return {
            "id": self.id,
            "service": host,
            "user_agent": self.user_agent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
        }


class EmailImport(db.Model):
    """An uploaded or pasted e-mail confirmation awaiting review."""

    __tablename__ = "email_imports"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=True)
    subject = db.Column(db.String(500), nullable=True)
    sender = db.Column(db.String(255), nullable=True)
    raw_excerpt = db.Column(db.Text, nullable=True)
    parsed_json = db.Column(db.Text, nullable=False, default="{}")
    status = db.Column(db.String(20), nullable=False, default="pending")  # pending|confirmed|discarded
    created_item_id = db.Column(db.Integer, db.ForeignKey("tracked_items.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)

    @property
    def parsed(self) -> dict:
        try:
            return json.loads(self.parsed_json or "{}")
        except ValueError:
            return {}

    @parsed.setter
    def parsed(self, value: dict) -> None:
        self.parsed_json = json.dumps(value)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "filename": self.filename,
            "subject": self.subject,
            "sender": self.sender,
            "raw_excerpt": self.raw_excerpt,
            "parsed": self.parsed,
            "status": self.status,
            "created_item_id": self.created_item_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
