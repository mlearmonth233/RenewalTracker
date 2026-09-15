"""Development entry point.

    python run.py                 # start the web app on http://localhost:5000
    flask --app run check-renewals  # one-off renewal check (for cron)
"""
import os

from renewaltracker import create_app
from renewaltracker.scheduler import RenewalScheduler

app = create_app()

if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    # With the reloader, only start the scheduler in the child process.
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        RenewalScheduler(app).start()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "5000")), debug=debug)
