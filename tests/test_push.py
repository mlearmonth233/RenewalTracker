"""Browser push notifications: crypto primitives, subscription API and alert dispatch."""
import json
from datetime import date

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from renewaltracker import webpush
from renewaltracker.alerts import check_renewals
from renewaltracker.models import PushSubscription, TrackedItem, User, db
from renewaltracker.webpush import (
    PushError,
    PushGone,
    VapidKeys,
    b64url_decode,
    b64url_encode,
    encrypt_payload,
    generate_vapid_private_key_b64,
    load_or_create_vapid_keys,
    load_private_key,
    send_web_push,
)

ENDPOINT = "https://push.example.com/send/abc123"


# ---------------------------------------------------------------- helpers


def make_browser_keys():
    """Simulate what ``pushManager.subscribe`` gives a page: p256dh + auth."""
    priv = ec.generate_private_key(ec.SECP256R1())
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    import os

    auth = os.urandom(16)
    return priv, b64url_encode(pub), b64url_encode(auth)


def browser_decrypt(body: bytes, browser_priv, auth_b64: str) -> bytes:
    """Reference RFC 8291 decryption, as a browser would perform it."""
    salt, rs, idlen = body[:16], body[16:20], body[20]
    server_pub = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    assert int.from_bytes(rs, "big") == 4096
    client_pub = browser_priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = browser_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_pub))
    ikm = HKDF(hashes.SHA256(), 32, b64url_decode(auth_b64), b"WebPush: info\x00" + client_pub + server_pub).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def subscription_json(p256dh, auth, endpoint=ENDPOINT):
    return {"endpoint": endpoint, "expirationTime": None, "keys": {"p256dh": p256dh, "auth": auth}}


# ---------------------------------------------------------------- crypto


def test_encrypt_payload_roundtrip_matches_rfc8291():
    priv, p256dh, auth = make_browser_keys()
    body = encrypt_payload(b'{"title":"hi"}', p256dh, auth)
    assert browser_decrypt(body, priv, auth) == b'{"title":"hi"}'
    # Every message uses a fresh salt and ephemeral key.
    assert encrypt_payload(b"x", p256dh, auth) != encrypt_payload(b"x", p256dh, auth)


def test_encrypt_payload_rejects_bad_keys():
    _, p256dh, auth = make_browser_keys()
    with pytest.raises(ValueError):
        encrypt_payload(b"x", b64url_encode(b"\x04" + b"\x00" * 10), auth)
    with pytest.raises(ValueError):
        encrypt_payload(b"x", p256dh, b64url_encode(b"short"))
    with pytest.raises(ValueError):
        encrypt_payload(b"x" * 5000, p256dh, auth)


def test_vapid_keys_roundtrip_and_jwt():
    private_b64 = generate_vapid_private_key_b64()
    keys = VapidKeys(load_private_key(private_b64), "mailto:ops@example.com")
    assert keys.private_key_b64 == private_b64
    assert len(b64url_decode(keys.public_key_b64)) == 65

    header = keys.authorization_header(ENDPOINT)
    assert header.startswith("vapid t=") and f", k={keys.public_key_b64}" in header
    token = header.split("t=")[1].split(",")[0]
    h, c, sig = token.split(".")
    assert json.loads(b64url_decode(h)) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(b64url_decode(c))
    assert claims["aud"] == "https://push.example.com"
    assert claims["sub"] == "mailto:ops@example.com"
    # Verify the ES256 signature with the public key, as a push service would.
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    raw = b64url_decode(sig)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    keys.private_key.public_key().verify(der, f"{h}.{c}".encode(), ec.ECDSA(hashes.SHA256()))


def test_load_private_key_accepts_pem():
    priv = ec.generate_private_key(ec.SECP256R1())
    pem = priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    assert load_private_key(pem).private_numbers().private_value == priv.private_numbers().private_value
    with pytest.raises(ValueError):
        load_private_key(b64url_encode(b"too short"))


def test_load_or_create_persists_generated_key(tmp_path):
    path = tmp_path / "vapid.json"
    first = load_or_create_vapid_keys(None, "mailto:a@b.c", str(path))
    assert path.exists()
    second = load_or_create_vapid_keys(None, "mailto:a@b.c", str(path))
    assert first.public_key_b64 == second.public_key_b64
    explicit = load_or_create_vapid_keys(first.private_key_b64, "mailto:a@b.c", None)
    assert explicit.public_key_b64 == first.public_key_b64


def test_send_web_push_posts_encrypted_body(monkeypatch):
    priv, p256dh, auth = make_browser_keys()
    keys = VapidKeys(load_private_key(generate_vapid_private_key_b64()), "mailto:a@b.c")
    captured = {}

    class FakeResponse:
        status = 201

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["body"] = req.data
        return FakeResponse()

    monkeypatch.setattr(webpush.urllib.request, "urlopen", fake_urlopen)
    status = send_web_push(endpoint=ENDPOINT, p256dh=p256dh, auth=auth, payload={"title": "T", "body": "B"}, vapid=keys, urgency="high")
    assert status == 201
    assert captured["url"] == ENDPOINT
    assert captured["headers"]["content-encoding"] == "aes128gcm"
    assert captured["headers"]["urgency"] == "high"
    assert captured["headers"]["ttl"] == str(24 * 3600)
    assert captured["headers"]["authorization"].startswith("vapid t=")
    assert json.loads(browser_decrypt(captured["body"], priv, auth)) == {"title": "T", "body": "B"}


def test_send_web_push_maps_http_errors(monkeypatch):
    import io
    import urllib.error

    _, p256dh, auth = make_browser_keys()
    keys = VapidKeys(load_private_key(generate_vapid_private_key_b64()), "mailto:a@b.c")

    def gone(req, timeout=None):
        raise urllib.error.HTTPError(ENDPOINT, 410, "Gone", {}, io.BytesIO(b""))

    monkeypatch.setattr(webpush.urllib.request, "urlopen", gone)
    with pytest.raises(PushGone):
        send_web_push(endpoint=ENDPOINT, p256dh=p256dh, auth=auth, payload="x", vapid=keys)

    def bad(req, timeout=None):
        raise urllib.error.HTTPError(ENDPOINT, 400, "Bad", {}, io.BytesIO(b"invalid jwt"))

    monkeypatch.setattr(webpush.urllib.request, "urlopen", bad)
    with pytest.raises(PushError) as exc:
        send_web_push(endpoint=ENDPOINT, p256dh=p256dh, auth=auth, payload="x", vapid=keys)
    assert exc.value.status == 400


# ---------------------------------------------------------------- API


def test_vapid_public_key_endpoint(client, app):
    data = client.get("/api/push/vapid-public-key").get_json()
    assert data["enabled"] is True
    assert data["public_key"] == app.extensions["vapid"].public_key_b64


def test_service_worker_is_served_from_root(client):
    res = client.get("/sw.js")
    assert res.status_code == 200
    assert res.headers["Service-Worker-Allowed"] == "/"
    assert b"addEventListener(\"push\"" in res.data


def test_subscribe_list_unsubscribe(auth_client):
    _, p256dh, auth = make_browser_keys()
    assert auth_client.post("/api/push/subscribe", json={"subscription": {"endpoint": "http://insecure", "keys": {}}}).status_code == 400

    res = auth_client.post("/api/push/subscribe", json={"subscription": subscription_json(p256dh, auth)})
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert body["service"] == "push.example.com"
    assert "endpoint" not in body and "p256dh" not in body  # secrets never echoed

    # Re-subscribing the same endpoint updates rather than duplicates.
    _, p2, a2 = make_browser_keys()
    auth_client.post("/api/push/subscribe", json=subscription_json(p2, a2))
    subs = auth_client.get(f"/api/push/subscriptions?endpoint={ENDPOINT}").get_json()["subscriptions"]
    assert len(subs) == 1 and subs[0]["current"] is True
    assert PushSubscription.query.first().p256dh == p2

    assert auth_client.post("/api/push/unsubscribe", json={}).status_code == 400
    assert auth_client.post("/api/push/unsubscribe", json={"endpoint": ENDPOINT}).get_json()["removed"] == 1
    assert auth_client.get("/api/push/subscriptions").get_json()["subscriptions"] == []


def test_subscription_moves_to_new_account_on_same_browser(client):
    _, p256dh, auth = make_browser_keys()
    client.post("/api/auth/register", json={"username": "first", "password": "password-one"})
    client.post("/api/push/subscribe", json=subscription_json(p256dh, auth))
    client.post("/api/auth/logout")
    client.post("/api/auth/register", json={"username": "second", "password": "password-two"})
    client.post("/api/push/subscribe", json=subscription_json(p256dh, auth))
    sub = PushSubscription.query.one()
    assert sub.user.username == "second"


def test_test_notification_endpoint(auth_client, monkeypatch):
    calls = []
    monkeypatch.setattr("renewaltracker.push_api.send_web_push", lambda **kw: calls.append(kw) or 201)
    assert auth_client.post("/api/push/test", json={}).status_code == 404

    _, p256dh, auth = make_browser_keys()
    auth_client.post("/api/push/subscribe", json=subscription_json(p256dh, auth))
    res = auth_client.post("/api/push/test", json={"endpoint": ENDPOINT})
    assert res.status_code == 200, res.get_json()
    assert res.get_json() == {"sent": 1, "removed": 0}
    assert calls[0]["endpoint"] == ENDPOINT
    assert "set up" in calls[0]["payload"]["title"]
    assert PushSubscription.query.first().last_used_at is not None


def test_push_disabled_server(monkeypatch):
    from renewaltracker import create_app
    from renewaltracker.config import TestConfig

    class NoPush(TestConfig):
        PUSH_ENABLED = False

    app = create_app(NoPush)
    with app.app_context():
        db.create_all()
        c = app.test_client()
        assert c.get("/api/push/vapid-public-key").get_json() == {"enabled": False, "public_key": None}
        res = c.post("/api/auth/register", json={"username": "nopush", "password": "password-np"})
        assert res.status_code == 201, res.get_json()
        assert c.post("/api/push/subscribe", json=subscription_json("a", "b")).status_code == 503
        db.drop_all()


# ---------------------------------------------------------------- alert dispatch


def _user_with_item(reminder_days="7"):
    user = User(username="pusher", email=None)
    user.set_password("password123")
    db.session.add(user)
    db.session.commit()
    item = TrackedItem(user_id=user.id, name="Netflix", category="subscription", renewal_date=date(2026, 10, 1), recurrence="monthly", reminder_days=reminder_days, amount=15.99, currency="GBP")
    db.session.add(item)
    db.session.commit()
    return user, item


def test_check_renewals_pushes_digest_and_prunes_dead_subscriptions(app, monkeypatch):
    user, _ = _user_with_item()
    _, p1, a1 = make_browser_keys()
    _, p2, a2 = make_browser_keys()
    db.session.add(PushSubscription(user_id=user.id, endpoint=ENDPOINT, p256dh=p1, auth=a1))
    db.session.add(PushSubscription(user_id=user.id, endpoint=ENDPOINT + "-dead", p256dh=p2, auth=a2))
    db.session.commit()

    sent = []

    def fake_send(**kw):
        if kw["endpoint"].endswith("-dead"):
            raise PushGone("410")
        sent.append(kw)
        return 201

    monkeypatch.setattr("renewaltracker.push_api.send_web_push", fake_send)

    created = check_renewals(today=date(2026, 9, 28), send_email=False, send_push=True)
    assert len(created) == 1
    assert created[0].pushed_at is not None
    assert len(sent) == 1
    payload = sent[0]["payload"]
    assert payload["title"] == "Renewal coming up"
    assert "Netflix" in payload["body"] and "3 days" in payload["body"]
    assert payload["url"] == "/#alerts"
    assert sent[0]["urgency"] == "normal"
    # Dead subscription was removed, live one kept.
    assert [s.endpoint for s in PushSubscription.query.all()] == [ENDPOINT]

    # Nothing new -> nothing pushed.
    assert check_renewals(today=date(2026, 9, 28), send_email=False, send_push=True) == []
    assert len(sent) == 1


def test_overdue_push_is_urgent_and_multi_alert_digest(app, monkeypatch):
    user, item = _user_with_item(reminder_days="30,7")
    second = TrackedItem(user_id=user.id, name="Car insurance", category="insurance", renewal_date=date(2026, 9, 20), recurrence="yearly", reminder_days="30")
    db.session.add(second)
    _, p1, a1 = make_browser_keys()
    db.session.add(PushSubscription(user_id=user.id, endpoint=ENDPOINT, p256dh=p1, auth=a1))
    db.session.commit()

    sent = []
    monkeypatch.setattr("renewaltracker.push_api.send_web_push", lambda **kw: sent.append(kw) or 201)

    created = check_renewals(today=date(2026, 9, 25), send_email=False, send_push=True)
    # Netflix: 30-day and 7-day stages; Car insurance: overdue.
    assert len(created) == 3
    assert len(sent) == 1  # one digest per user
    payload = sent[0]["payload"]
    assert payload["title"] == "3 renewals need attention"
    assert payload["requireInteraction"] is True
    assert sent[0]["urgency"] == "high"
    assert payload["body"].splitlines()[0].startswith("Car insurance was due")


def test_send_push_false_skips_dispatch(app, monkeypatch):
    user, _ = _user_with_item()
    _, p1, a1 = make_browser_keys()
    db.session.add(PushSubscription(user_id=user.id, endpoint=ENDPOINT, p256dh=p1, auth=a1))
    db.session.commit()
    monkeypatch.setattr("renewaltracker.push_api.send_web_push", lambda **kw: pytest.fail("should not push"))
    created = check_renewals(today=date(2026, 9, 28), send_email=False, send_push=False)
    assert len(created) == 1 and created[0].pushed_at is None


def test_users_without_subscriptions_are_skipped(app, monkeypatch):
    _user_with_item()
    monkeypatch.setattr("renewaltracker.push_api.send_web_push", lambda **kw: pytest.fail("should not push"))
    created = check_renewals(today=date(2026, 9, 28), send_email=False, send_push=True)
    assert len(created) == 1
