"""A tiny background scheduler that runs the renewal check periodically.

It is deliberately dependency-free: a daemon thread that sleeps for the
configured interval and then calls :func:`check_renewals` inside an app
context. For production deployments you may prefer to run
``flask --app run check-renewals`` from cron instead.
"""
from __future__ import annotations

import logging
import threading
import time

from .alerts import check_renewals

log = logging.getLogger(__name__)


class RenewalScheduler:
    def __init__(self, app, interval_hours: float | None = None, run_immediately: bool = True):
        self.app = app
        hours = interval_hours if interval_hours is not None else app.config.get("CHECK_INTERVAL_HOURS", 6)
        self.interval = max(60.0, float(hours) * 3600)
        self.run_immediately = run_immediately
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _run_once(self) -> None:
        with self.app.app_context():
            try:
                created = check_renewals()
                log.info("Renewal check complete: %d new alert(s).", len(created))
            except Exception:  # pragma: no cover
                log.exception("Renewal check failed")

    def _loop(self) -> None:
        if self.run_immediately:
            self._run_once()
        while not self._stop.wait(self.interval):
            self._run_once()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="renewal-scheduler", daemon=True)
        self._thread.start()
        log.info("Renewal scheduler started (every %.1f h).", self.interval / 3600)

    def stop(self) -> None:
        self._stop.set()
