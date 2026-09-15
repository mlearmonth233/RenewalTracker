# RenewalTracker

Never miss a renewal again. RenewalTracker keeps track of your **bills,
subscriptions, insurance policies and passports**, and alerts you before they
come up for renewal or expiry.

Add items by hand, or **upload an e-mail confirmation** (`.eml`) or paste its
text and let the app pull out the provider, amount, renewal date, reference
number and billing frequency for you to review and confirm.

## Features

- **Track anything with a date** – bills, subscriptions, insurance, passports/ID, and anything else.
- **E-mail import** – upload a saved `.eml` message or paste the e-mail body. A heuristic parser extracts:
  provider, category, amount & currency, renewal/expiry date (with alternative dates offered), billing
  frequency, policy/account reference and whether it auto-renews. Nothing is saved until you review it.
- **Staged alerts** – each item has reminder stages (e.g. `30,7,1` days before). Alerts are raised once per
  stage, on the day, and when overdue. Category defaults: bills & subscriptions `7,1`, insurance `30,7`,
  passports `270,180,90` (many countries need six months' validity).
- **Notifications** – alerts appear in-app with an unread badge, are **pushed to your browser or phone** on any
  device where you allowed push notifications (Web Push, works with the tab closed), and are e-mailed as a digest if
  SMTP is configured.
- **Dashboard** – overdue / due today / next 7 / 30 / 90 days, spend due in the next 30 days, and estimated
  monthly cost (annual premiums etc. normalised per month).
- **Mark as renewed** – rolls recurring items forward to the next cycle; non-recurring items (passports) take the new expiry date.
- **Calendar export** – download an `.ics` file with recurring events and reminders for your calendar app.
- **Multi-user** – simple username/password accounts; every user sees only their own data.
- **Scheduler** – a background thread checks renewals periodically, or run `flask --app run check-renewals` from cron.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open <http://localhost:5000>, create an account and start adding items.

### Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `SECRET_KEY` | `dev-change-me` | Flask session signing key. **Set this in production.** |
| `DATABASE_URL` | `sqlite:///renewaltracker.db` | Any SQLAlchemy URL. The SQLite file lives in `instance/`. |
| `DATE_DAY_FIRST` | `true` | How to read ambiguous dates like `03/04/2026` in e-mails (`true` = day/month, `false` = month/day). |
| `CHECK_INTERVAL_HOURS` | `6` | How often the background scheduler checks for renewals. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_USE_TLS`, `MAIL_FROM` | unset | Enable e-mail digests. Leave `SMTP_HOST` unset to keep alerts in-app and push only. |
| `PUSH_ENABLED` | `true` | Browser push notifications on/off. |
| `VAPID_PRIVATE_KEY` | auto-generated | P-256 key that signs push messages, as base64url raw key or PEM. If unset, one is generated on first run and stored in `instance/vapid.json`. Keep it stable: changing it invalidates every device's subscription. |
| `VAPID_SUBJECT` | `mailto:renewaltracker@localhost` | Contact for push services, a `mailto:` or `https://` URL. |
| `HOST`, `PORT`, `FLASK_DEBUG` | `127.0.0.1`, `5000`, `0` | Server binding and debug mode for `run.py`. |

### Running the renewal check from cron

```bash
0 8 * * * cd /path/to/RenewalTracker && .venv/bin/flask --app run check-renewals
```

## Push notifications

Open **Settings → Push notifications** and click **Allow push notifications**. The browser asks for permission
once, then registers a service worker and a Web Push subscription that is stored for your account. When the
renewal check raises new alerts, one notification per user is pushed to every device they enabled. Clicking it
opens the Alerts page. **Send test notification** confirms the whole chain end to end.

Notes:

- Push requires a secure context: `https://` or `http://localhost`. Behind a reverse proxy, terminate TLS there.
- Works in Chrome, Edge, Firefox and Safari 16.4+. On iOS the app must be added to the Home Screen first.
- Subscriptions the push service reports as gone (HTTP 404/410) are removed automatically, as are ones that fail five times in a row.
- Web Push encryption (RFC 8291) and VAPID signing (RFC 8292) are implemented in `renewaltracker/webpush.py`
  on top of `cryptography`, so no extra push library is needed.

## Importing e-mails

1. In your mail client, save the confirmation e-mail as a file (usually **Save as… → .eml**), or copy its text.
2. Go to **Import e-mail**, upload the file or paste the text, and click **Parse**.
3. Review the extracted fields. If several dates were found they are listed – click one to use it.
4. Click **Add to tracker**. The item is created with `source = email` and alerts are generated straight away if it is already inside its reminder window.

The parser is heuristic and language-agnostic in structure but tuned to English wording such as
*renews on*, *expiry date*, *payment due*, *valid until*, *annual premium*, *per month*. It deliberately
down-weights dates near words like *issued*, *order date* or *date of birth*.

## API overview

All endpoints are JSON and live under `/api`. Authentication is cookie-session based.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/auth/register` · `/login` · `/logout` | Account management |
| `GET/PUT` | `/api/auth/me` | Current user & notification settings |
| `GET/POST` | `/api/items` | List (filters: `category`, `q`, `archived=all|only`) / create |
| `GET/PUT/DELETE` | `/api/items/<id>` | Read / update / delete |
| `POST` | `/api/items/<id>/renew` | Mark renewed (`new_date`, `amount` optional) |
| `POST` | `/api/items/<id>/archive` | Archive / restore |
| `GET` | `/api/dashboard` | Buckets & totals |
| `GET` | `/api/alerts?unread=1` | Alerts |
| `POST` | `/api/alerts/check` · `/api/alerts/<id>/ack` · `/api/alerts/ack-all` | Run check / acknowledge |
| `POST` | `/api/imports/parse` | Upload `file` (multipart) or `{"text": ...}` → parsed proposal |
| `POST` | `/api/imports/<id>/confirm` · `/discard` | Create item from proposal (body overrides fields) / discard |
| `GET` | `/api/export.ics` | Calendar export |
| `GET` | `/api/push/vapid-public-key` | Public key the browser needs to subscribe |
| `GET/POST` | `/api/push/subscriptions` · `/api/push/subscribe` · `/api/push/unsubscribe` | Manage this account's push devices |
| `POST` | `/api/push/test` | Send a test notification |

## Development

```bash
pip install -r requirements.txt
pytest
```

Project layout:

```
renewaltracker/
  __init__.py      app factory, CLI command
  config.py        environment-driven settings
  models.py        User, TrackedItem, Alert, EmailImport
  email_parser.py  .eml / text → renewal proposal
  alerts.py        staged alert engine + e-mail / push dispatch
  webpush.py       Web Push encryption (RFC 8291) + VAPID (RFC 8292)
  scheduler.py     background renewal checker
  auth.py, api.py, imports.py, push_api.py   JSON blueprints
  static/          single-page front end (no build step) + sw.js service worker
tests/             pytest suite
run.py             dev server entry point
```
