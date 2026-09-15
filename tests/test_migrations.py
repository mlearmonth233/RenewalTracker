"""Migrations must match the models and must upgrade legacy databases."""
import os

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from flask_migrate import upgrade
from sqlalchemy import inspect, text

from renewaltracker import create_app
from renewaltracker.config import TestConfig
from renewaltracker.migrate import INITIAL_REVISION, apply_migrations, database_state
from renewaltracker.models import TrackedItem, User, db


def _file_db_config(tmp_path, auto_migrate: bool):
    class FileDB(TestConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
        AUTO_MIGRATE = auto_migrate
        PUSH_ENABLED = False

    return FileDB


def _schema_diff(app):
    with app.app_context():
        with db.engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True, "render_as_batch": True})
            diff = compare_metadata(ctx, db.metadata)
    # SQLite reports no server defaults, so ignore pure default-only differences.
    return [d for d in diff if not (isinstance(d, tuple) and d[0] == "modify_default")]


def test_migrations_produce_the_model_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("RENEWALTRACKER_DATA_DIR", str(tmp_path))
    app = create_app(_file_db_config(tmp_path, auto_migrate=True))
    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        assert {"users", "tracked_items", "alerts", "email_imports", "push_subscriptions", "alembic_version"} <= tables
        version = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version is not None
    assert _schema_diff(app) == [], "models and migrations have drifted – run `flask db migrate`"


def test_apply_migrations_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("RENEWALTRACKER_DATA_DIR", str(tmp_path))
    app = create_app(_file_db_config(tmp_path, auto_migrate=True))
    with app.app_context():
        assert database_state() == "managed"
        assert apply_migrations() == "managed"


def test_legacy_create_all_database_is_stamped_and_upgraded(tmp_path, monkeypatch):
    monkeypatch.setenv("RENEWALTRACKER_DATA_DIR", str(tmp_path))
    cfg = _file_db_config(tmp_path, auto_migrate=False)

    # 1. Simulate a database made by the pre-migration app: create_all, no alembic_version.
    legacy = create_app(cfg)
    with legacy.app_context():
        db.create_all()
        user = User(username="legacy", email=None)
        user.set_password("password123")
        db.session.add(user)
        db.session.commit()
        db.session.add(TrackedItem(user_id=user.id, name="Old bill", category="bill", renewal_date=__import__("datetime").date(2026, 12, 1)))
        db.session.commit()
        assert database_state() == "legacy"
        assert "alembic_version" not in inspect(db.engine).get_table_names()

    # 2. Start the new app against it: it must stamp, upgrade and keep the data.
    upgraded = create_app(_file_db_config(tmp_path, auto_migrate=True))
    with upgraded.app_context():
        assert database_state() == "managed"
        version = db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version is not None
        assert User.query.filter_by(username="legacy").one().items[0].name == "Old bill"
    assert _schema_diff(upgraded) == []


def test_initial_revision_exists_in_migrations_folder():
    from renewaltracker.paths import migrations_dir

    versions = os.listdir(os.path.join(migrations_dir(), "versions"))
    assert any(f.startswith(INITIAL_REVISION) for f in versions), versions


def test_fresh_empty_database_reports_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("RENEWALTRACKER_DATA_DIR", str(tmp_path))
    app = create_app(_file_db_config(tmp_path, auto_migrate=False))
    with app.app_context():
        assert database_state() == "empty"
        upgrade()
        assert database_state() == "managed"
