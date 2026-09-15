"""Upload or paste an e-mail confirmation, review the extracted details, confirm."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request

from .alerts import check_renewals
from .api import ValidationError, apply_item_payload
from .auth import current_user, login_required
from .email_parser import parse_email
from .models import EmailImport, TrackedItem, db

bp = Blueprint("imports", __name__, url_prefix="/api/imports")

ALLOWED_EXTENSIONS = {".eml", ".txt", ".msg", ".html", ".htm"}


@bp.get("")
@login_required
def list_imports():
    status = request.args.get("status")
    query = EmailImport.query.filter_by(user_id=current_user().id)
    if status:
        query = query.filter_by(status=status)
    imports = query.order_by(EmailImport.created_at.desc()).limit(100).all()
    return jsonify({"imports": [i.to_dict() for i in imports]})


@bp.post("/parse")
@login_required
def parse_import():
    """Accept a multipart file upload (``file``) or JSON ``{"text": "..."}``."""
    raw: bytes | str | None = None
    filename = None

    if "file" in request.files:
        upload = request.files["file"]
        filename = upload.filename or "upload.eml"
        lowered = filename.lower()
        if not any(lowered.endswith(ext) for ext in ALLOWED_EXTENSIONS):
            return jsonify({"error": "Please upload an .eml, .txt or .html file."}), 400
        raw = upload.read()
        if not raw:
            return jsonify({"error": "The uploaded file is empty."}), 400
    else:
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or request.form.get("text") or "").strip()
        if not text:
            return jsonify({"error": "Provide an e-mail file or paste the e-mail text."}), 400
        raw = text

    parsed = parse_email(raw, dayfirst=current_app.config.get("DATE_DAY_FIRST", True))

    record = EmailImport(
        user_id=current_user().id,
        filename=filename,
        subject=parsed.subject,
        sender=parsed.sender,
        raw_excerpt=parsed.body_excerpt,
    )
    record.parsed = parsed.to_dict()
    db.session.add(record)
    db.session.commit()
    return jsonify(record.to_dict()), 201


@bp.get("/<int:import_id>")
@login_required
def get_import(import_id):
    record = EmailImport.query.filter_by(id=import_id, user_id=current_user().id).first()
    if not record:
        return jsonify({"error": "Not found."}), 404
    return jsonify(record.to_dict())


@bp.post("/<int:import_id>/confirm")
@login_required
def confirm_import(import_id):
    """Create a tracked item from the (possibly user-edited) parsed fields."""
    record = EmailImport.query.filter_by(id=import_id, user_id=current_user().id).first()
    if not record:
        return jsonify({"error": "Not found."}), 404
    if record.status == "confirmed":
        return jsonify({"error": "This import has already been confirmed.", "item_id": record.created_item_id}), 409

    overrides = request.get_json(silent=True) or {}
    parsed = record.parsed
    payload = {
        "name": parsed.get("name"),
        "category": parsed.get("category"),
        "provider": parsed.get("provider"),
        "reference": parsed.get("reference"),
        "amount": parsed.get("amount"),
        "currency": parsed.get("currency") or "GBP",
        "renewal_date": parsed.get("renewal_date"),
        "recurrence": parsed.get("recurrence"),
        "auto_renews": parsed.get("auto_renews", False),
        "reminder_days": parsed.get("reminder_days"),
        "notes": f"Imported from e-mail: {record.subject}" if record.subject else "Imported from e-mail",
    }
    payload.update({k: v for k, v in overrides.items() if k in payload or k in {"interval_days", "notes"}})

    item = TrackedItem(user_id=current_user().id, source="email")
    try:
        apply_item_payload(item, payload)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400

    db.session.add(item)
    db.session.flush()
    record.status = "confirmed"
    record.created_item_id = item.id
    db.session.commit()
    check_renewals(send_email=False)
    return jsonify({"import": record.to_dict(), "item": item.to_dict()}), 201


@bp.post("/<int:import_id>/discard")
@login_required
def discard_import(import_id):
    record = EmailImport.query.filter_by(id=import_id, user_id=current_user().id).first()
    if not record:
        return jsonify({"error": "Not found."}), 404
    record.status = "discarded"
    db.session.commit()
    return jsonify(record.to_dict())
