"""JSON API for tracked items, the dashboard, alerts and calendar export."""
from __future__ import annotations

from datetime import date, datetime

from flask import Blueprint, Response, jsonify, request

from .alerts import check_renewals
from .auth import current_user, login_required
from .models import (
    CATEGORIES,
    RECURRENCES,
    Alert,
    TrackedItem,
    db,
    monthly_equivalent,
    parse_reminder_days,
    utcnow,
)

bp = Blueprint("api", __name__, url_prefix="/api")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    pass


def _parse_date(value, field: str) -> date:
    if isinstance(value, date):
        return value
    if not value or not isinstance(value, str):
        raise ValidationError(f"{field} is required.")
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        raise ValidationError(f"{field} must be a date in YYYY-MM-DD format.")


def _parse_amount(value):
    if value in (None, ""):
        return None
    try:
        amount = float(str(value).replace(",", "").replace("£", "").replace("$", "").replace("€", "").strip())
    except ValueError:
        raise ValidationError("Amount must be a number.")
    if amount < 0:
        raise ValidationError("Amount cannot be negative.")
    return round(amount, 2)


def apply_item_payload(item: TrackedItem, data: dict, *, partial: bool = False) -> None:
    """Validate ``data`` and copy it onto ``item``. Raises ValidationError."""

    def has(key):
        return key in data

    if has("name") or not partial:
        name = (data.get("name") or "").strip()
        if not name:
            raise ValidationError("Name is required.")
        item.name = name[:200]

    if has("category") or not partial:
        category = (data.get("category") or "other").strip().lower()
        if category not in CATEGORIES:
            raise ValidationError(f"Category must be one of: {', '.join(CATEGORIES)}.")
        item.category = category

    if has("provider"):
        item.provider = (data.get("provider") or "").strip()[:200] or None
    if has("reference"):
        item.reference = (data.get("reference") or "").strip()[:120] or None
    if has("notes"):
        item.notes = (data.get("notes") or "").strip() or None

    if has("amount"):
        item.amount = _parse_amount(data.get("amount"))
    if has("currency"):
        currency = (data.get("currency") or "").strip().upper()
        item.currency = currency[:3] if currency else None
    elif not partial and not item.currency:
        item.currency = "GBP"

    if has("renewal_date") or not partial:
        item.renewal_date = _parse_date(data.get("renewal_date"), "Renewal date")

    if has("recurrence") or not partial:
        recurrence = (data.get("recurrence") or CATEGORIES[item.category]["default_recurrence"]).strip().lower()
        if recurrence not in RECURRENCES:
            raise ValidationError(f"Recurrence must be one of: {', '.join(RECURRENCES)}.")
        item.recurrence = recurrence

    if has("interval_days") or item.recurrence == "custom":
        raw = data.get("interval_days")
        if item.recurrence == "custom":
            try:
                interval = int(raw)
            except (TypeError, ValueError):
                raise ValidationError("Custom recurrence needs an interval in days.")
            if interval < 1:
                raise ValidationError("Interval must be at least 1 day.")
            item.interval_days = interval
        else:
            item.interval_days = None

    if has("auto_renews"):
        item.auto_renews = bool(data.get("auto_renews"))

    if has("reminder_days") or not partial:
        raw = data.get("reminder_days")
        if raw in (None, ""):
            raw = CATEGORIES[item.category]["default_reminder_days"]
        if isinstance(raw, list):
            raw = ",".join(str(x) for x in raw)
        days = parse_reminder_days(raw)
        if not days:
            raise ValidationError("Reminder days must be a comma-separated list of numbers, e.g. 30,7,1.")
        item.reminder_days = ",".join(str(d) for d in days)

    if has("archived"):
        item.archived = bool(data.get("archived"))


def _owned_item(item_id: int) -> TrackedItem | None:
    user = current_user()
    return TrackedItem.query.filter_by(id=item_id, user_id=user.id).first()


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@bp.get("/categories")
def categories():
    return jsonify(
        {
            "categories": [{"key": key, **meta} for key, meta in CATEGORIES.items()],
            "recurrences": list(RECURRENCES),
        }
    )


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


@bp.get("/items")
@login_required
def list_items():
    user = current_user()
    query = TrackedItem.query.filter_by(user_id=user.id)

    category = request.args.get("category")
    if category:
        query = query.filter_by(category=category)
    include_archived = request.args.get("archived") in {"1", "true", "all"}
    if request.args.get("archived") == "only":
        query = query.filter_by(archived=True)
    elif not include_archived:
        query = query.filter_by(archived=False)
    search = (request.args.get("q") or "").strip()
    if search:
        like = f"%{search}%"
        query = query.filter(
            db.or_(TrackedItem.name.ilike(like), TrackedItem.provider.ilike(like), TrackedItem.notes.ilike(like))
        )

    items = query.order_by(TrackedItem.renewal_date.asc(), TrackedItem.name.asc()).all()
    today = date.today()
    return jsonify({"items": [i.to_dict(today) for i in items]})


@bp.post("/items")
@login_required
def create_item():
    data = request.get_json(silent=True) or {}
    item = TrackedItem(user_id=current_user().id, source=data.get("source") or "manual")
    try:
        apply_item_payload(item, data)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    db.session.add(item)
    db.session.commit()
    # Generate any alerts that already apply (e.g. an item added inside its window).
    check_renewals(send_email=False, send_push=False)
    return jsonify(item.to_dict()), 201


@bp.get("/items/<int:item_id>")
@login_required
def get_item(item_id):
    item = _owned_item(item_id)
    if not item:
        return jsonify({"error": "Not found."}), 404
    payload = item.to_dict()
    payload["alerts"] = [a.to_dict() for a in sorted(item.alerts, key=lambda a: a.created_at, reverse=True)]
    return jsonify(payload)


@bp.route("/items/<int:item_id>", methods=["PUT", "PATCH"])
@login_required
def update_item(item_id):
    item = _owned_item(item_id)
    if not item:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    try:
        apply_item_payload(item, data, partial=True)
    except ValidationError as exc:
        db.session.rollback()
        return jsonify({"error": str(exc)}), 400
    item.updated_at = utcnow()
    db.session.commit()
    check_renewals(send_email=False, send_push=False)
    return jsonify(item.to_dict())


@bp.delete("/items/<int:item_id>")
@login_required
def delete_item(item_id):
    item = _owned_item(item_id)
    if not item:
        return jsonify({"error": "Not found."}), 404
    db.session.delete(item)
    db.session.commit()
    return jsonify({"ok": True})


@bp.post("/items/<int:item_id>/renew")
@login_required
def renew_item(item_id):
    """Mark an item as renewed/paid and roll its date forward."""
    item = _owned_item(item_id)
    if not item:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    new_date = None
    if data.get("new_date"):
        try:
            new_date = _parse_date(data["new_date"], "New date")
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400
    try:
        item.mark_renewed(new_date)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if "amount" in data:
        try:
            item.amount = _parse_amount(data.get("amount"))
        except ValidationError as exc:
            return jsonify({"error": str(exc)}), 400
    if data.get("reference"):
        item.reference = str(data["reference"]).strip()[:120]
    # Acknowledge outstanding alerts for the old cycle.
    for alert in item.alerts:
        if alert.acknowledged_at is None:
            alert.acknowledged_at = utcnow()
    item.updated_at = utcnow()
    db.session.commit()
    return jsonify(item.to_dict())


@bp.post("/items/<int:item_id>/archive")
@login_required
def archive_item(item_id):
    item = _owned_item(item_id)
    if not item:
        return jsonify({"error": "Not found."}), 404
    data = request.get_json(silent=True) or {}
    item.archived = bool(data.get("archived", True))
    db.session.commit()
    return jsonify(item.to_dict())


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@bp.get("/dashboard")
@login_required
def dashboard():
    user = current_user()
    today = date.today()
    items = TrackedItem.query.filter_by(user_id=user.id, archived=False).order_by(TrackedItem.renewal_date).all()

    buckets = {"overdue": [], "due_today": [], "next_7_days": [], "next_30_days": [], "next_90_days": [], "later": []}
    by_category: dict[str, dict] = {k: {"count": 0, "monthly_equivalent": 0.0} for k in CATEGORIES}
    monthly_total = 0.0
    for item in items:
        days = item.days_until_renewal(today)
        d = item.to_dict(today)
        if days < 0:
            buckets["overdue"].append(d)
        elif days == 0:
            buckets["due_today"].append(d)
        elif days <= 7:
            buckets["next_7_days"].append(d)
        elif days <= 30:
            buckets["next_30_days"].append(d)
        elif days <= 90:
            buckets["next_90_days"].append(d)
        else:
            buckets["later"].append(d)
        me = monthly_equivalent(item.amount, item.recurrence, item.interval_days)
        monthly_total += me
        cat = by_category.setdefault(item.category, {"count": 0, "monthly_equivalent": 0.0})
        cat["count"] += 1
        cat["monthly_equivalent"] += me

    for cat in by_category.values():
        cat["monthly_equivalent"] = round(cat["monthly_equivalent"], 2)

    unread = Alert.query.filter_by(user_id=user.id, acknowledged_at=None).count()
    upcoming_spend = sum(i.amount or 0 for i in items if 0 <= i.days_until_renewal(today) <= 30)

    return jsonify(
        {
            "today": today.isoformat(),
            "totals": {
                "items": len(items),
                "overdue": len(buckets["overdue"]),
                "due_within_30_days": len(buckets["due_today"]) + len(buckets["next_7_days"]) + len(buckets["next_30_days"]),
                "estimated_monthly_spend": round(monthly_total, 2),
                "spend_next_30_days": round(upcoming_spend, 2),
                "unread_alerts": unread,
            },
            "by_category": by_category,
            "buckets": buckets,
        }
    )


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@bp.get("/alerts")
@login_required
def list_alerts():
    user = current_user()
    query = Alert.query.filter_by(user_id=user.id)
    if request.args.get("unread") in {"1", "true"}:
        query = query.filter_by(acknowledged_at=None)
    alerts = query.order_by(Alert.acknowledged_at.isnot(None), Alert.days_until.asc(), Alert.created_at.desc()).limit(200).all()
    return jsonify({"alerts": [a.to_dict() for a in alerts]})


@bp.post("/alerts/check")
@login_required
def run_check():
    created = check_renewals()
    mine = [a.to_dict() for a in created if a.user_id == current_user().id]
    return jsonify({"created": len(mine), "alerts": mine})


@bp.post("/alerts/<int:alert_id>/ack")
@login_required
def ack_alert(alert_id):
    alert = Alert.query.filter_by(id=alert_id, user_id=current_user().id).first()
    if not alert:
        return jsonify({"error": "Not found."}), 404
    alert.acknowledged_at = utcnow()
    db.session.commit()
    return jsonify(alert.to_dict())


@bp.post("/alerts/ack-all")
@login_required
def ack_all():
    now = utcnow()
    updated = Alert.query.filter_by(user_id=current_user().id, acknowledged_at=None).update({"acknowledged_at": now})
    db.session.commit()
    return jsonify({"acknowledged": updated})


# ---------------------------------------------------------------------------
# Calendar export
# ---------------------------------------------------------------------------


def _ics_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@bp.get("/export.ics")
@login_required
def export_ics():
    user = current_user()
    items = TrackedItem.query.filter_by(user_id=user.id, archived=False).all()
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//RenewalTracker//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Renewals",
    ]
    for item in items:
        start = item.renewal_date.strftime("%Y%m%d")
        summary = f"{CATEGORIES.get(item.category, CATEGORIES['other'])['label']}: {item.name}"
        desc_parts = []
        if item.provider:
            desc_parts.append(f"Provider: {item.provider}")
        if item.amount is not None:
            desc_parts.append(f"Amount: {item.amount:.2f} {item.currency or ''}".strip())
        if item.reference:
            desc_parts.append(f"Reference: {item.reference}")
        if item.notes:
            desc_parts.append(item.notes)
        lines += [
            "BEGIN:VEVENT",
            f"UID:renewaltracker-{item.id}@renewaltracker",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(' | '.join(desc_parts))}",
        ]
        rrule = {"weekly": "FREQ=WEEKLY", "monthly": "FREQ=MONTHLY", "quarterly": "FREQ=MONTHLY;INTERVAL=3", "yearly": "FREQ=YEARLY"}.get(item.recurrence)
        if item.recurrence == "custom" and item.interval_days:
            rrule = f"FREQ=DAILY;INTERVAL={item.interval_days}"
        if rrule:
            lines.append(f"RRULE:{rrule}")
        for days in item.reminder_day_list():
            if days > 0:
                lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape(item.name)} renews in {days} days", f"TRIGGER:-P{days}D", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines) + "\r\n"
    return Response(body, mimetype="text/calendar", headers={"Content-Disposition": "attachment; filename=renewals.ics"})
