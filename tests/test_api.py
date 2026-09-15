from datetime import date, timedelta

import pytest


def _future(days):
    return (date.today() + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------- auth


def test_register_login_logout_flow(client):
    assert client.get("/api/auth/me").get_json() == {"user": None}

    res = client.post("/api/auth/register", json={"username": "sam", "password": "supersecret"})
    assert res.status_code == 201
    assert client.get("/api/auth/me").get_json()["user"]["username"] == "sam"

    res = client.post("/api/auth/register", json={"username": "SAM", "password": "supersecret"})
    assert res.status_code == 409

    client.post("/api/auth/logout")
    assert client.get("/api/auth/me").get_json() == {"user": None}

    res = client.post("/api/auth/login", json={"username": "sam", "password": "wrong"})
    assert res.status_code == 401
    res = client.post("/api/auth/login", json={"username": "sam", "password": "supersecret"})
    assert res.status_code == 200


def test_register_validation(client):
    assert client.post("/api/auth/register", json={"username": "ab", "password": "supersecret"}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "abc", "password": "short"}).status_code == 400
    assert client.post("/api/auth/register", json={"username": "abc", "password": "supersecret", "email": "nope"}).status_code == 400


def test_protected_routes_require_login(client):
    assert client.get("/api/items").status_code == 401
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/imports/parse", json={"text": "x"}).status_code == 401


def test_update_settings(auth_client):
    res = auth_client.put("/api/auth/me", json={"email": "new@example.com", "notify_by_email": False})
    assert res.status_code == 200
    assert res.get_json()["email"] == "new@example.com"
    assert res.get_json()["notify_by_email"] is False

    res = auth_client.put("/api/auth/me", json={"current_password": "wrong", "new_password": "another-one"})
    assert res.status_code == 400
    res = auth_client.put("/api/auth/me", json={"current_password": "correct-horse", "new_password": "another-one"})
    assert res.status_code == 200
    auth_client.post("/api/auth/logout")
    assert auth_client.post("/api/auth/login", json={"username": "alice", "password": "another-one"}).status_code == 200


# ---------------------------------------------------------------- items


def test_item_crud(auth_client):
    payload = {
        "name": "Netflix",
        "category": "subscription",
        "provider": "Netflix",
        "amount": "15.99",
        "currency": "gbp",
        "renewal_date": _future(20),
        "recurrence": "monthly",
        "reminder_days": "7, 1",
        "auto_renews": True,
    }
    res = auth_client.post("/api/items", json=payload)
    assert res.status_code == 201, res.get_json()
    item = res.get_json()
    assert item["currency"] == "GBP"
    assert item["amount"] == 15.99
    assert item["reminder_days"] == "7,1"
    assert item["status"] == "ok"
    assert item["monthly_equivalent"] == 15.99

    res = auth_client.get("/api/items")
    assert [i["name"] for i in res.get_json()["items"]] == ["Netflix"]

    res = auth_client.put(f"/api/items/{item['id']}", json={"amount": 17.99, "notes": "Price rise"})
    assert res.status_code == 200
    assert res.get_json()["amount"] == 17.99
    assert res.get_json()["notes"] == "Price rise"
    assert res.get_json()["name"] == "Netflix"  # partial update keeps other fields

    res = auth_client.get(f"/api/items/{item['id']}")
    assert res.status_code == 200
    assert "alerts" in res.get_json()

    assert auth_client.delete(f"/api/items/{item['id']}").status_code == 200
    assert auth_client.get(f"/api/items/{item['id']}").status_code == 404


def test_item_validation(auth_client):
    res = auth_client.post("/api/items", json={"name": "", "renewal_date": _future(1)})
    assert res.status_code == 400
    res = auth_client.post("/api/items", json={"name": "X", "renewal_date": "not-a-date"})
    assert res.status_code == 400
    res = auth_client.post("/api/items", json={"name": "X", "renewal_date": _future(1), "category": "car"})
    assert res.status_code == 400
    res = auth_client.post("/api/items", json={"name": "X", "renewal_date": _future(1), "recurrence": "custom"})
    assert res.status_code == 400
    assert "interval" in res.get_json()["error"].lower()
    res = auth_client.post("/api/items", json={"name": "X", "renewal_date": _future(1), "recurrence": "custom", "interval_days": 45})
    assert res.status_code == 201
    assert res.get_json()["interval_days"] == 45


def test_category_defaults_apply_when_fields_omitted(auth_client):
    res = auth_client.post("/api/items", json={"name": "Passport", "category": "passport", "renewal_date": _future(800)})
    assert res.status_code == 201
    item = res.get_json()
    assert item["recurrence"] == "none"
    assert item["reminder_days"] == "270,180,90"
    assert item["status"] == "ok"


def test_creating_item_inside_window_raises_alert_immediately(auth_client):
    res = auth_client.post("/api/items", json={"name": "Council tax", "category": "bill", "renewal_date": _future(3), "reminder_days": "7"})
    assert res.status_code == 201
    assert res.get_json()["status"] == "upcoming"
    alerts = auth_client.get("/api/alerts?unread=1").get_json()["alerts"]
    assert len(alerts) == 1
    assert alerts[0]["item_name"] == "Council tax"

    res = auth_client.post(f"/api/alerts/{alerts[0]['id']}/ack")
    assert res.get_json()["acknowledged"] is True
    assert auth_client.get("/api/alerts?unread=1").get_json()["alerts"] == []


def test_items_are_isolated_per_user(client):
    client.post("/api/auth/register", json={"username": "one", "password": "password-one"})
    client.post("/api/items", json={"name": "Mine", "renewal_date": _future(10)})
    client.post("/api/auth/logout")

    client.post("/api/auth/register", json={"username": "two", "password": "password-two"})
    assert client.get("/api/items").get_json()["items"] == []
    client.post("/api/items", json={"name": "Theirs", "renewal_date": _future(10)})
    client.post("/api/auth/logout")

    client.post("/api/auth/login", json={"username": "one", "password": "password-one"})
    items = client.get("/api/items").get_json()["items"]
    assert [i["name"] for i in items] == ["Mine"]
    other_id = [i for i in items][0]["id"] + 1
    assert client.get(f"/api/items/{other_id}").status_code == 404


def test_renew_and_archive(auth_client):
    res = auth_client.post("/api/items", json={"name": "Gym", "category": "subscription", "renewal_date": _future(2), "recurrence": "monthly", "reminder_days": "7"})
    item = res.get_json()
    assert len(auth_client.get("/api/alerts?unread=1").get_json()["alerts"]) == 1

    res = auth_client.post(f"/api/items/{item['id']}/renew", json={"amount": "31"})
    assert res.status_code == 200
    renewed = res.get_json()
    assert renewed["renewal_date"] > item["renewal_date"]
    assert renewed["amount"] == 31.0
    assert renewed["status"] == "ok"
    # Old-cycle alerts acknowledged.
    assert auth_client.get("/api/alerts?unread=1").get_json()["alerts"] == []

    # Non-recurring item needs an explicit new date.
    res = auth_client.post("/api/items", json={"name": "Passport", "category": "passport", "renewal_date": _future(100)})
    pid = res.get_json()["id"]
    assert auth_client.post(f"/api/items/{pid}/renew", json={}).status_code == 400
    res = auth_client.post(f"/api/items/{pid}/renew", json={"new_date": _future(3650)})
    assert res.status_code == 200

    res = auth_client.post(f"/api/items/{pid}/archive")
    assert res.get_json()["archived"] is True
    assert [i["name"] for i in auth_client.get("/api/items").get_json()["items"]] == ["Gym"]
    assert len(auth_client.get("/api/items?archived=all").get_json()["items"]) == 2
    assert [i["name"] for i in auth_client.get("/api/items?archived=only").get_json()["items"]] == ["Passport"]


def test_search_and_filter(auth_client):
    auth_client.post("/api/items", json={"name": "Home insurance", "category": "insurance", "provider": "Aviva", "renewal_date": _future(50)})
    auth_client.post("/api/items", json={"name": "Spotify", "category": "subscription", "renewal_date": _future(5)})
    assert [i["name"] for i in auth_client.get("/api/items?category=insurance").get_json()["items"]] == ["Home insurance"]
    assert [i["name"] for i in auth_client.get("/api/items?q=aviva").get_json()["items"]] == ["Home insurance"]
    assert [i["name"] for i in auth_client.get("/api/items").get_json()["items"]] == ["Spotify", "Home insurance"]


# ---------------------------------------------------------------- dashboard


def test_dashboard_buckets_and_totals(auth_client):
    auth_client.post("/api/items", json={"name": "Overdue bill", "category": "bill", "amount": 50, "renewal_date": (date.today() - timedelta(days=2)).isoformat(), "recurrence": "monthly"})
    auth_client.post("/api/items", json={"name": "Soon", "category": "subscription", "amount": 10, "renewal_date": _future(3), "recurrence": "monthly"})
    auth_client.post("/api/items", json={"name": "Insurance", "category": "insurance", "amount": 240, "renewal_date": _future(60), "recurrence": "yearly"})
    auth_client.post("/api/items", json={"name": "Passport", "category": "passport", "renewal_date": _future(1000)})

    data = auth_client.get("/api/dashboard").get_json()
    assert data["totals"]["items"] == 4
    assert data["totals"]["overdue"] == 1
    assert data["totals"]["due_within_30_days"] == 1
    assert data["totals"]["estimated_monthly_spend"] == 80.0  # 50 + 10 + 240/12
    assert data["totals"]["spend_next_30_days"] == 10.0
    assert [i["name"] for i in data["buckets"]["overdue"]] == ["Overdue bill"]
    assert [i["name"] for i in data["buckets"]["next_7_days"]] == ["Soon"]
    assert [i["name"] for i in data["buckets"]["next_90_days"]] == ["Insurance"]
    assert [i["name"] for i in data["buckets"]["later"]] == ["Passport"]
    assert data["by_category"]["insurance"] == {"count": 1, "monthly_equivalent": 20.0}
    assert data["totals"]["unread_alerts"] >= 2  # overdue + upcoming


# ---------------------------------------------------------------- alerts


def test_alert_check_endpoint_and_ack_all(auth_client):
    auth_client.post("/api/items", json={"name": "A", "renewal_date": _future(1), "reminder_days": "7"})
    auth_client.post("/api/items", json={"name": "B", "renewal_date": _future(2), "reminder_days": "7"})
    assert len(auth_client.get("/api/alerts?unread=1").get_json()["alerts"]) == 2
    # Nothing new to raise – idempotent.
    assert auth_client.post("/api/alerts/check").get_json()["created"] == 0
    assert auth_client.post("/api/alerts/ack-all").get_json()["acknowledged"] == 2
    assert auth_client.get("/api/alerts?unread=1").get_json()["alerts"] == []
    assert len(auth_client.get("/api/alerts").get_json()["alerts"]) == 2


# ---------------------------------------------------------------- imports


INSURANCE_TEXT = """Subject: Your home insurance renewal
From: Admiral <hello@admiral.com>

Your policy HI-2233445 renews on 1 December 2026.
Annual premium: £356.20
Your cover will renew automatically unless you tell us otherwise.
"""


def test_import_parse_confirm_flow(auth_client):
    res = auth_client.post("/api/imports/parse", json={"text": INSURANCE_TEXT})
    assert res.status_code == 201, res.get_json()
    record = res.get_json()
    parsed = record["parsed"]
    assert record["status"] == "pending"
    assert parsed["category"] == "insurance"
    assert parsed["provider"] == "Admiral"
    assert parsed["amount"] == 356.20
    assert parsed["renewal_date"] == "2026-12-01"
    assert parsed["reference"] == "HI-2233445"
    assert parsed["auto_renews"] is True

    # User corrects the name before confirming.
    res = auth_client.post(f"/api/imports/{record['id']}/confirm", json={"name": "Home insurance (Admiral)"})
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert body["import"]["status"] == "confirmed"
    assert body["item"]["name"] == "Home insurance (Admiral)"
    assert body["item"]["source"] == "email"
    assert body["item"]["renewal_date"] == "2026-12-01"
    assert body["item"]["recurrence"] == "yearly"

    # Confirming twice is rejected.
    assert auth_client.post(f"/api/imports/{record['id']}/confirm", json={}).status_code == 409

    imports = auth_client.get("/api/imports").get_json()["imports"]
    assert len(imports) == 1 and imports[0]["created_item_id"] == body["item"]["id"]


def test_import_file_upload(auth_client):
    from io import BytesIO

    eml = (
        b"From: Spotify <no-reply@spotify.com>\nSubject: Your Premium receipt\nContent-Type: text/plain\n\n"
        b"Thanks for subscribing. Your Premium subscription renews monthly.\nNext payment: 2026-10-20\nAmount: \xc2\xa310.99\n"
    )
    res = auth_client.post("/api/imports/parse", data={"file": (BytesIO(eml), "receipt.eml")}, content_type="multipart/form-data")
    assert res.status_code == 201, res.get_json()
    parsed = res.get_json()["parsed"]
    assert parsed["provider"] == "Spotify"
    assert parsed["category"] == "subscription"
    assert parsed["renewal_date"] == "2026-10-20"
    assert parsed["amount"] == 10.99
    assert parsed["recurrence"] == "monthly"


def test_import_rejects_bad_input(auth_client):
    from io import BytesIO

    assert auth_client.post("/api/imports/parse", json={}).status_code == 400
    res = auth_client.post("/api/imports/parse", data={"file": (BytesIO(b"x"), "virus.exe")}, content_type="multipart/form-data")
    assert res.status_code == 400


def test_import_without_date_requires_user_to_supply_one(auth_client):
    res = auth_client.post("/api/imports/parse", json={"text": "Subject: Thanks\nYour subscription payment of £5 was received."})
    record = res.get_json()
    assert record["parsed"]["renewal_date"] is None
    assert auth_client.post(f"/api/imports/{record['id']}/confirm", json={}).status_code == 400
    res = auth_client.post(f"/api/imports/{record['id']}/confirm", json={"renewal_date": _future(30)})
    assert res.status_code == 201

    res = auth_client.post("/api/imports/parse", json={"text": "Subject: Hi\nJust saying hello"})
    rec = res.get_json()
    assert auth_client.post(f"/api/imports/{rec['id']}/discard").get_json()["status"] == "discarded"


# ---------------------------------------------------------------- misc


def test_ics_export(auth_client):
    auth_client.post("/api/items", json={"name": "Car insurance", "category": "insurance", "provider": "Aviva", "amount": 400, "renewal_date": "2027-03-14", "recurrence": "yearly", "reminder_days": "30,7"})
    res = auth_client.get("/api/export.ics")
    assert res.status_code == 200
    assert res.mimetype == "text/calendar"
    body = res.get_data(as_text=True)
    assert "BEGIN:VEVENT" in body
    assert "DTSTART;VALUE=DATE:20270314" in body
    assert "SUMMARY:Insurance: Car insurance" in body
    assert "RRULE:FREQ=YEARLY" in body
    assert "TRIGGER:-P30D" in body and "TRIGGER:-P7D" in body


def test_health_and_index(client):
    assert client.get("/health").get_json()["status"] == "ok"
    res = client.get("/")
    assert res.status_code == 200
    assert b"RenewalTracker" in res.data
    assert client.get("/api/nope").status_code == 404
    assert client.get("/api/categories").status_code == 200


def test_cli_check_renewals(app, auth_client):
    auth_client.post("/api/items", json={"name": "Water bill", "category": "bill", "renewal_date": _future(1), "reminder_days": "7"})
    # Alert was already generated on creation; CLI run should report zero new.
    runner = app.test_cli_runner()
    result = runner.invoke(args=["check-renewals"])
    assert result.exit_code == 0, result.output
    assert "Created 0 new alert(s)" in result.output
