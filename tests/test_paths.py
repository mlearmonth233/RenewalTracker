"""Data-folder resolution and persisted secret key for packaged/casual use."""
import os
import sys

from renewaltracker import create_app
from renewaltracker.config import Config
from renewaltracker.models import db
from renewaltracker.paths import DATA_DIR_ENV, default_instance_path, static_dir, user_data_dir


def test_user_data_dir_honours_override(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "custom"))
    assert user_data_dir() == str(tmp_path / "custom")
    assert default_instance_path() == str(tmp_path / "custom")


def test_user_data_dir_is_per_platform(monkeypatch, tmp_path):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)

    monkeypatch.setattr(sys, "platform", "linux")
    assert user_data_dir() == os.path.join(str(tmp_path), ".local", "share", "renewaltracker")

    monkeypatch.setattr(sys, "platform", "darwin")
    assert user_data_dir() == os.path.join(str(tmp_path), "Library", "Application Support", "RenewalTracker")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "AppData" / "Local"))
    assert user_data_dir() == os.path.join(str(tmp_path / "AppData" / "Local"), "RenewalTracker")


def test_source_checkout_uses_flask_default_instance(monkeypatch):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert default_instance_path() is None
    assert static_dir().endswith(os.path.join("renewaltracker", "static"))
    assert os.path.exists(os.path.join(static_dir(), "index.html"))


def test_frozen_bundle_uses_user_profile_and_meipass(monkeypatch, tmp_path):
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_instance_path() == user_data_dir()
    assert static_dir() == os.path.join(str(tmp_path / "bundle"), "renewaltracker", "static")


def test_secret_key_is_generated_once_and_persisted(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))

    class Casual(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        SECRET_KEY = Config.SECRET_KEY  # the insecure default a casual user would leave
        PUSH_ENABLED = False

    app1 = create_app(Casual)
    assert app1.instance_path == str(tmp_path)
    assert app1.config["SECRET_KEY"] != Config.SECRET_KEY
    assert len(app1.config["SECRET_KEY"]) >= 64
    assert (tmp_path / "secret_key").read_text().strip() == app1.config["SECRET_KEY"]

    app2 = create_app(Casual)
    assert app2.config["SECRET_KEY"] == app1.config["SECRET_KEY"]

    class Explicit(Casual):
        SECRET_KEY = "user-provided-key"

    assert create_app(Explicit).config["SECRET_KEY"] == "user-provided-key"

    for app in (app1, app2):
        with app.app_context():
            db.drop_all()
