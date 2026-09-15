"""RenewalTracker – keep on top of bills, subscriptions, insurance and passports."""
from __future__ import annotations

import logging
import os

import click
from flask import Flask, jsonify, send_from_directory

from .config import Config
from .models import db

__version__ = "0.1.0"


def create_app(config_object: type | object | None = None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static", instance_relative_config=True)
    app.config.from_object(config_object or Config)
    os.makedirs(app.instance_path, exist_ok=True)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db.init_app(app)

    from . import api, auth, imports

    app.register_blueprint(auth.bp)
    app.register_blueprint(api.bp)
    app.register_blueprint(imports.bp)

    with app.app_context():
        db.create_all()

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "version": __version__})

    @app.errorhandler(404)
    def not_found(_err):
        from flask import request

        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found."}), 404
        return send_from_directory(app.static_folder, "index.html")

    @app.errorhandler(413)
    def too_large(_err):
        return jsonify({"error": "That file is too large (5 MB limit)."}), 413

    @app.cli.command("check-renewals")
    def check_renewals_command():
        """Generate alerts for upcoming renewals (suitable for cron)."""
        from .alerts import check_renewals

        created = check_renewals()
        click.echo(f"Created {len(created)} new alert(s).")
        for alert in created:
            click.echo(f"  - {alert.message}")

    return app
