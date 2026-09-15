"""Renewal alert engine.

``check_renewals`` walks every active tracked item, works out how many days
remain until its renewal date, and creates an ``Alert`` row for each reminder
stage that has been reached. Alerts are de-duplicated per (item, renewal date,
stage) so the checker can run as often as you like.

If SMTP is configured, newly created alerts are also e-mailed to the user.
"""
from __future__ import annotations

import logging
import smtplib
from datetime import date
from email.message import EmailMessage

from flask import current_app
from sqlalchemy.exc import IntegrityError

from .models import Alert, TrackedItem, User, db, utcnow

log = logging.getLogger(__name__)


def _money(item: TrackedItem) -> str:
    if item.amount is None:
        return ""
    symbol = {"GBP": "£", "USD": "$", "EUR": "€"}.get(item.currency or "", "")
    if symbol:
        return f" ({symbol}{item.amount:,.2f})"
    return f" ({item.amount:,.2f} {item.currency or ''})".rstrip()


def _stage_message(item: TrackedItem, days: int) -> str:
    when = item.renewal_date.strftime("%d %b %Y")
    label = {
        "passport": "expires",
        "insurance": "renews",
        "subscription": "renews",
        "bill": "is due",
        "other": "is due",
    }.get(item.category, "is due")
    if days < 0:
        overdue = -days
        return f"{item.name} was due on {when} – {overdue} day{'s' if overdue != 1 else ''} overdue{_money(item)}."
    if days == 0:
        return f"{item.name} {label} today ({when}){_money(item)}."
    return f"{item.name} {label} in {days} day{'s' if days != 1 else ''} on {when}{_money(item)}."


def pending_stages(item: TrackedItem, today: date) -> list[tuple[str, str, int]]:
    """Return (kind, dedupe_key, days) tuples for every stage that applies today."""
    days = item.days_until_renewal(today)
    iso = item.renewal_date.isoformat()
    stages: list[tuple[str, str, int]] = []
    if days < 0:
        stages.append(("overdue", f"{iso}:overdue", days))
        return stages
    if days == 0:
        stages.append(("due_today", f"{iso}:due", days))
    for threshold in item.reminder_day_list():
        if threshold > 0 and days <= threshold:
            stages.append(("upcoming", f"{iso}:upcoming:{threshold}", days))
    return stages


def check_renewals(today: date | None = None, *, send_email: bool = True) -> list[Alert]:
    """Create alerts for all items whose reminder window has been reached."""
    today = today or date.today()
    created: list[Alert] = []

    items = TrackedItem.query.filter_by(archived=False).all()
    for item in items:
        existing = {a.dedupe_key for a in item.alerts}
        for kind, key, days in pending_stages(item, today):
            if key in existing:
                continue
            alert = Alert(
                user_id=item.user_id,
                item_id=item.id,
                kind=kind,
                dedupe_key=key,
                renewal_date=item.renewal_date,
                days_until=days,
                message=_stage_message(item, days),
            )
            db.session.add(alert)
            try:
                db.session.flush()
            except IntegrityError:
                db.session.rollback()
                continue
            created.append(alert)
            existing.add(key)

    db.session.commit()

    if send_email and created:
        _email_alerts(created)
        db.session.commit()

    return created


def _email_alerts(alerts: list[Alert]) -> None:
    host = current_app.config.get("SMTP_HOST")
    if not host:
        log.info("SMTP not configured; %d alert(s) kept in-app only.", len(alerts))
        return

    by_user: dict[int, list[Alert]] = {}
    for alert in alerts:
        by_user.setdefault(alert.user_id, []).append(alert)

    for user_id, user_alerts in by_user.items():
        user = db.session.get(User, user_id)
        if not user or not user.email or not user.notify_by_email:
            continue
        try:
            _send_digest(user, user_alerts)
        except Exception:  # pragma: no cover - network failure path
            log.exception("Failed to e-mail alerts to %s", user.email)
            continue
        now = utcnow()
        for alert in user_alerts:
            alert.emailed_at = now


def _send_digest(user: User, alerts: list[Alert]) -> None:
    cfg = current_app.config
    msg = EmailMessage()
    count = len(alerts)
    msg["Subject"] = f"RenewalTracker: {count} renewal reminder{'s' if count != 1 else ''}"
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = user.email
    lines = [f"Hi {user.username},", "", "The following items need your attention:", ""]
    for alert in sorted(alerts, key=lambda a: a.days_until):
        lines.append(f"  • {alert.message}")
    lines += ["", "Open RenewalTracker to review, renew or snooze these items."]
    msg.set_content("\n".join(lines))

    with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20) as smtp:
        if cfg.get("SMTP_USE_TLS"):
            smtp.starttls()
        if cfg.get("SMTP_USERNAME"):
            smtp.login(cfg["SMTP_USERNAME"], cfg["SMTP_PASSWORD"] or "")
        smtp.send_message(msg)
