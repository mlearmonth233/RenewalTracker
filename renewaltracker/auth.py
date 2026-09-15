"""Session-based authentication."""
from __future__ import annotations

import re
from functools import wraps

from flask import Blueprint, jsonify, request, session

from .models import User, db

bp = Blueprint("auth", __name__, url_prefix="/api/auth")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def current_user() -> User | None:
    # SQLAlchemy's identity map makes repeated lookups within a request cheap,
    # so no extra caching is needed.
    user_id = session.get("user_id")
    return db.session.get(User, user_id) if user_id else None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return jsonify({"error": "Authentication required."}), 401
        return view(*args, **kwargs)

    return wrapped


def _json() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


@bp.post("/register")
def register():
    data = _json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    email = (data.get("email") or "").strip() or None

    if len(username) < 3 or len(username) > 80:
        return jsonify({"error": "Username must be between 3 and 80 characters."}), 400
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", username):
        return jsonify({"error": "Username may only contain letters, numbers, dots, dashes and underscores."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400
    if email and not EMAIL_RE.match(email):
        return jsonify({"error": "That e-mail address does not look valid."}), 400
    if User.query.filter(db.func.lower(User.username) == username.lower()).first():
        return jsonify({"error": "That username is already taken."}), 409

    user = User(username=username, email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    session.clear()
    session["user_id"] = user.id
    return jsonify(user.to_dict()), 201


@bp.post("/login")
def login():
    data = _json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = User.query.filter(db.func.lower(User.username) == username.lower()).first()
    if not user or not user.check_password(password):
        return jsonify({"error": "Invalid username or password."}), 401
    session.clear()
    session["user_id"] = user.id
    return jsonify(user.to_dict())


@bp.post("/logout")
def logout():
    session.clear()
    return jsonify({"ok": True})


@bp.get("/me")
def me():
    user = current_user()
    if user is None:
        return jsonify({"user": None})
    return jsonify({"user": user.to_dict()})


@bp.put("/me")
@login_required
def update_me():
    user = current_user()
    data = _json()
    if "email" in data:
        email = (data.get("email") or "").strip() or None
        if email and not EMAIL_RE.match(email):
            return jsonify({"error": "That e-mail address does not look valid."}), 400
        user.email = email
    if "notify_by_email" in data:
        user.notify_by_email = bool(data.get("notify_by_email"))
    if data.get("new_password"):
        if not user.check_password(data.get("current_password") or ""):
            return jsonify({"error": "Current password is incorrect."}), 400
        if len(data["new_password"]) < 8:
            return jsonify({"error": "New password must be at least 8 characters."}), 400
        user.set_password(data["new_password"])
    db.session.commit()
    return jsonify(user.to_dict())
