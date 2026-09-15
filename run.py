"""Development entry point.

    python run.py                   # start the web app and open it in your browser
    python run.py --no-browser      # start without opening a browser
    flask --app run check-renewals  # one-off renewal check (for cron)

Environment: HOST, PORT, FLASK_DEBUG=1, OPEN_BROWSER=0 (same as --no-browser).
"""
import logging
import os
import socket
import sys
import threading
import time
import webbrowser

from renewaltracker import create_app
from renewaltracker.scheduler import RenewalScheduler

app = create_app()
log = logging.getLogger("renewaltracker.run")


def _browser_url(host: str, port: int) -> str:
    # 0.0.0.0 / :: listen on every interface but are not browsable addresses.
    browse_host = "localhost" if host in ("0.0.0.0", "::", "") else host
    return f"http://{browse_host}:{port}/"


def _open_browser_when_ready(host: str, port: int, url: str, timeout: float = 20.0) -> None:
    """Wait until the server accepts connections, then open the browser once."""
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((connect_host, port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.2)
    else:
        log.warning("Server did not start within %.0fs; open %s manually.", timeout, url)
        return
    if webbrowser.open(url):
        log.info("Opened %s in your browser.", url)
    else:
        log.info("Could not open a browser automatically. Visit %s", url)


def main() -> None:
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    url = _browser_url(host, port)

    open_browser = "--no-browser" not in sys.argv and os.environ.get("OPEN_BROWSER", "1").lower() not in ("0", "false", "no")
    # Under the debug reloader the script runs twice; do work only in the serving child.
    is_serving_process = not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"

    if is_serving_process:
        RenewalScheduler(app).start()
        log.info("RenewalTracker is starting on %s", url)
        if open_browser:
            threading.Thread(target=_open_browser_when_ready, args=(host, port, url), daemon=True).start()

    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
