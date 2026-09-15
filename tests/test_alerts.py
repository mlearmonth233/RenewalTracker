from datetime import date, timedelta

from renewaltracker.alerts import check_renewals, pending_stages
from renewaltracker.models import Alert, TrackedItem, User, advance_date, db, monthly_equivalent, parse_reminder_days


def _user():
    user = User(username="bob", email="bob@example.com")
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    return user


def _item(user, **kwargs):
    defaults = dict(name="Test", category="subscription", renewal_date=date(2026, 10, 1), recurrence="monthly", reminder_days="7,1")
    defaults.update(kwargs)
    item = TrackedItem(user_id=user.id, **defaults)
    db.session.add(item)
    db.session.commit()
    return item


def test_parse_reminder_days_normalises_input():
    assert parse_reminder_days("30, 7,1,7") == [30, 7, 1]
    assert parse_reminder_days("abc,5") == [5]
    assert parse_reminder_days("") == []
    assert parse_reminder_days(None) == []


def test_advance_date_handles_month_ends():
    assert advance_date(date(2026, 1, 31), "monthly", None) == date(2026, 2, 28)
    assert advance_date(date(2026, 3, 15), "quarterly", None) == date(2026, 6, 15)
    assert advance_date(date(2024, 2, 29), "yearly", None) == date(2025, 2, 28)
    assert advance_date(date(2026, 1, 1), "custom", 45) == date(2026, 2, 15)
    assert advance_date(date(2026, 1, 1), "none", None) is None


def test_monthly_equivalent():
    assert monthly_equivalent(120, "yearly", None) == 10
    assert monthly_equivalent(30, "quarterly", None) == 10
    assert round(monthly_equivalent(10, "weekly", None), 2) == 43.33
    assert monthly_equivalent(50, "none", None) == 0


def test_pending_stages(app):
    user = _user()
    item = _item(user, renewal_date=date(2026, 10, 1), reminder_days="30,7,1")
    assert pending_stages(item, date(2026, 8, 1)) == []
    assert [s[0] for s in pending_stages(item, date(2026, 9, 10))] == ["upcoming"]
    kinds = [s[2] for s in pending_stages(item, date(2026, 9, 26))]
    assert kinds == [5, 5]  # 30-day and 7-day stages both reached, 1-day not
    assert [s[0] for s in pending_stages(item, date(2026, 10, 1))] == ["due_today", "upcoming", "upcoming", "upcoming"]
    assert [s[0] for s in pending_stages(item, date(2026, 10, 5))] == ["overdue"]


def test_check_renewals_is_idempotent_and_staged(app):
    user = _user()
    item = _item(user, renewal_date=date(2026, 10, 1), reminder_days="7,1")

    assert check_renewals(today=date(2026, 9, 1), send_email=False) == []

    created = check_renewals(today=date(2026, 9, 25), send_email=False)
    assert len(created) == 1
    assert created[0].kind == "upcoming"
    assert "in 6 days" in created[0].message

    # Running again on the same day creates nothing new.
    assert check_renewals(today=date(2026, 9, 25), send_email=False) == []

    # Reaching the 1-day stage adds one more alert.
    created = check_renewals(today=date(2026, 9, 30), send_email=False)
    assert [a.kind for a in created] == ["upcoming"]
    assert created[0].dedupe_key.endswith(":upcoming:1")

    created = check_renewals(today=date(2026, 10, 1), send_email=False)
    assert [a.kind for a in created] == ["due_today"]

    created = check_renewals(today=date(2026, 10, 3), send_email=False)
    assert [a.kind for a in created] == ["overdue"]
    assert "2 days overdue" in created[0].message

    assert Alert.query.filter_by(item_id=item.id).count() == 4
    assert item.status(date(2026, 10, 3)) == "overdue"


def test_archived_items_do_not_alert(app):
    user = _user()
    _item(user, renewal_date=date(2026, 10, 1), archived=True)
    assert check_renewals(today=date(2026, 10, 5), send_email=False) == []


def test_mark_renewed_rolls_forward_and_resets_alert_cycle(app):
    user = _user()
    item = _item(user, renewal_date=date(2026, 10, 1), recurrence="monthly", reminder_days="7")
    check_renewals(today=date(2026, 9, 28), send_email=False)
    assert Alert.query.count() == 1

    item.mark_renewed()
    db.session.commit()
    assert item.renewal_date == date(2026, 11, 1)

    # New cycle -> new dedupe key -> a fresh alert when the window is reached.
    assert check_renewals(today=date(2026, 10, 2), send_email=False) == []
    created = check_renewals(today=date(2026, 10, 26), send_email=False)
    assert len(created) == 1
    assert created[0].renewal_date == date(2026, 11, 1)


def test_mark_renewed_skips_missed_cycles(app):
    user = _user()
    long_ago = date.today() - timedelta(days=100)
    item = _item(user, renewal_date=long_ago, recurrence="monthly")
    item.mark_renewed()
    assert item.renewal_date > date.today()
    assert (item.renewal_date - date.today()).days <= 31


def test_mark_renewed_non_recurring_requires_date(app):
    user = _user()
    item = _item(user, category="passport", recurrence="none", renewal_date=date(2027, 1, 1))
    try:
        item.mark_renewed()
        assert False, "expected ValueError"
    except ValueError:
        pass
    item.mark_renewed(date(2037, 1, 1))
    assert item.renewal_date == date(2037, 1, 1)


def test_email_sending_marks_alert_when_smtp_configured(app, monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent["tls"] = True

        def login(self, u, p):
            sent["login"] = (u, p)

        def send_message(self, msg):
            sent["to"] = msg["To"]
            sent["body"] = msg.get_content()

    import renewaltracker.alerts as alerts_mod

    monkeypatch.setattr(alerts_mod.smtplib, "SMTP", FakeSMTP)
    app.config.update(SMTP_HOST="smtp.example.com", SMTP_USERNAME="u", SMTP_PASSWORD="p")

    user = _user()
    _item(user, name="Gym", renewal_date=date(2026, 10, 1), reminder_days="7")
    created = check_renewals(today=date(2026, 9, 28), send_email=True)
    assert len(created) == 1
    assert created[0].emailed_at is not None
    assert sent["to"] == "bob@example.com"
    assert "Gym" in sent["body"]
    assert sent["login"] == ("u", "p")
