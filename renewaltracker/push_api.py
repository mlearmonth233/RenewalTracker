"""Browser push notification subscriptions."""
from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from .auth import current_user, login_required
from .models import PushSubscription, db, utcnow
from .webpush import PushError, PushGone, send_web_push

bp = Blueprint("push", __name__, url_prefix="/api/push")
log = logging.getLogger(__name__)


def vapid_keys():
    """The app-wide VAPID key pair, or None when push is disabled."""
    return current_app.extensions.get("vapid")


@bp.get("/vapid-public-key")
def public_key():
    keys = vapid_keys()
    if keys is None:
        return jsonify({"enabled": False, "public_key": None})
    return jsonify({"enabled": True, "public_key": keys.public_key_b64})


@bp.get("/subscriptions")
@login_required
def list_subscriptions():
    subs = PushSubscription.query.filter_by(user_id=current_user().id).order_by(PushSubscription.created_at).all()
    current_endpoint = (request.args.get("endpoint") or "").strip()
    payload = []
    for sub in subs:
        d = sub.to_dict()
        d["current"] = bool(current_endpoint) and sub.endpoint == current_endpoint
        payload.append(d)
    return jsonify({"enabled": vapid_keys() is not None, "subscriptions": payload})


@bp.post("/subscribe")
@login_required
def subscribe():
    """Store the PushSubscription JSON the browser produced (``subscription.toJSON()``)."""
    if vapid_keys() is None:
        return jsonify({"error": "Push notifications are disabled on this server."}), 503
    data = request.get_json(silent=True) or {}
    sub = data.get("subscription") or data
    endpoint = (sub.get("endpoint") or "").strip()
    keys = sub.get("keys") or {}
    p256dh = (keys.get("p256dh") or "").strip()
    auth = (keys.get("auth") or "").strip()
    if not endpoint.startswith("https://") or not p256dh or not auth:
        return jsonify({"error": "Invalid push subscription."}), 400

    user = current_user()
    record = PushSubscription.query.filter_by(endpoint=endpoint).first()
    if record and record.user_id != user.id:
        # The browser profile switched accounts; the subscription now belongs to the new user.
        record.user_id = user.id
    if record is None:
        record = PushSubscription(user_id=user.id, endpoint=endpoint)
        db.session.add(record)
    record.p256dh = p256dh
    record.auth = auth
    record.failures = 0
    record.user_agent = (request.headers.get("User-Agent") or "")[:300] or None
    db.session.commit()
    return jsonify(record.to_dict()), 201


@bp.post("/unsubscribe")
@login_required
def unsubscribe():
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    sub_id = data.get("id")
    query = PushSubscription.query.filter_by(user_id=current_user().id)
    if endpoint:
        query = query.filter_by(endpoint=endpoint)
    elif sub_id:
        query = query.filter_by(id=int(sub_id))
    else:
        return jsonify({"error": "Provide the subscription endpoint or id."}), 400
    removed = query.delete()
    db.session.commit()
    return jsonify({"removed": removed})


@bp.post("/test")
@login_required
def send_test():
    """Send a test notification to the calling browser (or all of the user's devices)."""
    keys = vapid_keys()
    if keys is None:
        return jsonify({"error": "Push notifications are disabled on this server."}), 503
    data = request.get_json(silent=True) or {}
    endpoint = (data.get("endpoint") or "").strip()
    query = PushSubscription.query.filter_by(user_id=current_user().id)
    if endpoint:
        query = query.filter_by(endpoint=endpoint)
    subs = query.all()
    if not subs:
        return jsonify({"error": "No push subscription found for this browser. Enable notifications first."}), 404

    payload = {
        "title": "RenewalTracker is set up",
        "body": "You'll be notified here when a bill, subscription, policy or passport is due for renewal.",
        "tag": "renewaltracker-test",
        "url": "/",
    }
    sent, removed, errors = deliver_to_subscriptions(subs, payload, keys)
    db.session.commit()
    if sent == 0 and errors:
        return jsonify({"error": f"Push service rejected the notification: {errors[0]}"}), 502
    return jsonify({"sent": sent, "removed": removed})


def deliver_to_subscriptions(subs, payload: dict, keys, *, urgency: str = "normal") -> tuple[int, int, list[str]]:
    """Send ``payload`` to each subscription, pruning ones the push service says are gone."""
    sent = removed = 0
    errors: list[str] = []
    for sub in list(subs):
        try:
            send_web_push(endpoint=sub.endpoint, p256dh=sub.p256dh, auth=sub.auth, payload=payload, vapid=keys, urgency=urgency)
            sub.last_used_at = utcnow()
            sub.failures = 0
            sent += 1
        except PushGone:
            db.session.delete(sub)
            removed += 1
        except (PushError, ValueError, OSError) as exc:
            sub.failures = (sub.failures or 0) + 1
            errors.append(str(exc))
            log.warning("Push to %s failed (%s failures): %s", sub.endpoint[:60], sub.failures, exc)
            if sub.failures >= 5:
                db.session.delete(sub)
                removed += 1
    return sent, removed, errors
