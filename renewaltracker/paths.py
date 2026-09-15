"""Where the app keeps its data (database, keys) and finds its static files.

Running from a source checkout keeps everything in ``instance/`` next to the
code, which is convenient for development. A packaged executable (PyInstaller)
unpacks itself into a temporary folder on every start, so there the data must
live in the user's profile instead. ``RENEWALTRACKER_DATA_DIR`` overrides both.
"""
from __future__ import annotations

import os
import sys

APP_NAME = "RenewalTracker"
DATA_DIR_ENV = "RENEWALTRACKER_DATA_DIR"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False)) and hasattr(sys, "_MEIPASS")


def user_data_dir(app_name: str = APP_NAME) -> str:
    """Platform-appropriate per-user data directory."""
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or home
        return os.path.join(base, app_name)
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", app_name)
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    return os.path.join(base, app_name.lower())


def default_instance_path() -> str | None:
    """Instance folder for Flask, or None to keep Flask's default (``instance/``)."""
    if os.environ.get(DATA_DIR_ENV) or is_frozen():
        return user_data_dir()
    return None


def static_dir() -> str:
    """Absolute path of the bundled front-end files."""
    if is_frozen():
        return os.path.join(sys._MEIPASS, "renewaltracker", "static")  # type: ignore[attr-defined]
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
