"""Apply Alembic migrations at start-up.

Two situations are handled:

* **Fresh database** – no tables yet. ``upgrade()`` creates everything.
* **Legacy database** – created by ``db.create_all()`` before migrations
  existed, so tables are present but there is no ``alembic_version`` table.
  Running ``upgrade()`` would try to re-create the tables and fail. We
  ``stamp`` such databases at the initial revision first, then upgrade.
"""
from __future__ import annotations

import logging

from flask import current_app
from flask_migrate import stamp, upgrade
from sqlalchemy import inspect

from .models import db

log = logging.getLogger(__name__)

# Revision id of the first migration, whose schema equals the pre-migration
# ``create_all`` schema. Update only if that migration is ever regenerated.
INITIAL_REVISION = "0001_initial"


def database_state() -> str:
    """'empty', 'legacy' (tables but no alembic_version) or 'managed'."""
    inspector = inspect(db.engine)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables:
        return "managed"
    if "users" in tables or "tracked_items" in tables:
        return "legacy"
    return "empty"


def apply_migrations() -> str:
    """Bring the database to the latest revision. Returns the state found."""
    # Alembic's plugin bootstrap logs a dozen INFO lines that mean nothing to users.
    logging.getLogger("alembic.runtime.plugins").setLevel(logging.WARNING)
    state = database_state()
    if state == "legacy":
        log.info("Existing database without migration history found; stamping it at %s.", INITIAL_REVISION)
        stamp(revision=INITIAL_REVISION)
    # Alembic logs "Running upgrade ..." lines; keep them at INFO like the rest.
    upgrade()
    log.info("Database schema is up to date (%s).", current_app.config.get("SQLALCHEMY_DATABASE_URI", "").split("///")[-1] or "db")
    return state
