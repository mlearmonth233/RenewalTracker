"""RenewalTracker launcher.

    python run.py                    # start the app and open it in your browser
    python run.py --no-browser       # start without opening a browser
    python run.py --port 8080        # use another port
    python run.py --data-dir ~/rt    # keep database & keys somewhere specific
    flask --app run check-renewals   # one-off renewal check (for cron)

The same options are available as environment variables: HOST, PORT,
OPEN_BROWSER=0, RENEWALTRACKER_DATA_DIR, FLASK_DEBUG=1.
"""
import argparse
import logging
import os
import socket
import sys
import threading
import time
import webbrowser

DATA_DIR_ENV = "RENEWALTRACKER_DATA_DIR"


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="RenewalTracker", description="Track bills, subscriptions, insurance and passports.")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"), help="interface to listen on (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")), help="port to listen on (default 5000)")
    parser.add_argument("--no-browser", action="store_true", help="do not open the app in a browser")
    parser.add_argument("--data-dir", default=None, help="folder for the database and keys")
    parser.add_argument("--debug", action="store_true", help="Flask debug mode with auto-reload")
    return parser.parse_args(argv)


# --data-dir must be known before the app is created, so parse early when run
# as a script. When imported by the Flask CLI (``flask --app run``) argv
# belongs to Flask and is left alone.
if __name__ == "__main__":
    _args = _parse_args(sys.argv[1:])
    if _args.data_dir:
        os.environ[DATA_DIR_ENV] = _args.data_dir
else:
    _args = None

from renewaltracker import create_app  # noqa: E402  (after data-dir handling)
from renewaltracker.scheduler import RenewalScheduler  # noqa: E402

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


def _port_in_use(host: str, port: int) -> bool:
    connect_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    try:
        with socket.create_connection((connect_host, port), timeout=0.5):
            return True
    except OSError:
        return False


def main(args: argparse.Namespace) -> None:
    debug = args.debug or os.environ.get("FLASK_DEBUG", "0") == "1"
    host, port = args.host, args.port
    url = _browser_url(host, port)
    open_browser = not args.no_browser and os.environ.get("OPEN_BROWSER", "1").lower() not in ("0", "false", "no")
    # Under the debug reloader the script runs twice; do work only in the serving child.
    is_serving_process = not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"

    if is_serving_process:
        if _port_in_use(host, port):
            print(f"\nRenewalTracker already seems to be running at {url}", file=sys.stderr)
            if open_browser:
                print("Opening it in your browser. Use --port to start a second copy on another port.\n", file=sys.stderr)
                webbrowser.open(url)
            else:
                print("Use --port to start a second copy on another port.\n", file=sys.stderr)
            sys.exit(1)
        RenewalScheduler(app).start()
        print("=" * 60)
        print(f"  RenewalTracker is running at {url}")
        print(f"  Data folder: {app.instance_path}")
        print("  Press Ctrl+C to stop.")
        print("=" * 60, flush=True)
        if open_browser:
            threading.Thread(target=_open_browser_when_ready, args=(host, port, url), daemon=True).start()

    try:
        app.run(host=host, port=port, debug=debug, use_reloader=debug)
    except KeyboardInterrupt:  # pragma: no cover
        pass


if __name__ == "__main__":
    main(_args)
