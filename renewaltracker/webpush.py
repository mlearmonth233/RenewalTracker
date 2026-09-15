"""Minimal Web Push implementation (RFC 8030 / 8291 / 8292) built on ``cryptography``.

Why not ``pywebpush``? It pulls in ``http-ece`` which does not build cleanly
everywhere. The protocol is small enough to implement directly:

* **VAPID** (RFC 8292): an ES256 JWT signed with the server's P-256 key
  proves to the push service who is sending.
* **Content encryption** (RFC 8291, ``aes128gcm``): the payload is encrypted
  with a key derived (ECDH + HKDF) from the browser's subscription keys, so
  the push service never sees the message.
"""
from __future__ import annotations

import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096
CURVE = ec.SECP256R1()


# ---------------------------------------------------------------------------
# base64url helpers
# ---------------------------------------------------------------------------


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(data: str) -> bytes:
    data = data.strip()
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


# ---------------------------------------------------------------------------
# VAPID keys
# ---------------------------------------------------------------------------


@dataclass
class VapidKeys:
    private_key: ec.EllipticCurvePrivateKey
    subject: str  # "mailto:you@example.com" or an https:// URL

    @property
    def public_key_bytes(self) -> bytes:
        return self.private_key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )

    @property
    def public_key_b64(self) -> str:
        """The ``applicationServerKey`` the browser needs to subscribe."""
        return b64url_encode(self.public_key_bytes)

    @property
    def private_key_b64(self) -> str:
        value = self.private_key.private_numbers().private_value
        return b64url_encode(value.to_bytes(32, "big"))

    def authorization_header(self, endpoint: str, expiry_seconds: int = 12 * 3600) -> str:
        parts = urlsplit(endpoint)
        audience = f"{parts.scheme}://{parts.netloc}"
        header = {"typ": "JWT", "alg": "ES256"}
        claims = {"aud": audience, "exp": int(time.time()) + expiry_seconds, "sub": self.subject}
        signing_input = (
            b64url_encode(json.dumps(header, separators=(",", ":")).encode())
            + "."
            + b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
        )
        der_sig = self.private_key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der_sig)
        raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        token = signing_input + "." + b64url_encode(raw_sig)
        return f"vapid t={token}, k={self.public_key_b64}"


def generate_vapid_private_key_b64() -> str:
    key = ec.generate_private_key(CURVE)
    return b64url_encode(key.private_numbers().private_value.to_bytes(32, "big"))


def load_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    """Accept a base64url raw 32-byte private value or a PEM-encoded key."""
    value = value.strip()
    if "-----BEGIN" in value:
        key = serialization.load_pem_private_key(value.encode(), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("VAPID private key must be an EC P-256 key.")
        return key
    raw = b64url_decode(value)
    if len(raw) != 32:
        raise ValueError("VAPID private key must be 32 raw bytes (base64url) or PEM.")
    return ec.derive_private_key(int.from_bytes(raw, "big"), CURVE)


def load_or_create_vapid_keys(private_key_value: str | None, subject: str, storage_path: str | None) -> VapidKeys:
    """Use the configured key, otherwise load/generate one persisted at ``storage_path``."""
    if private_key_value:
        return VapidKeys(load_private_key(private_key_value), subject)

    if storage_path and os.path.exists(storage_path):
        with open(storage_path, encoding="utf-8") as fh:
            stored = json.load(fh)
        return VapidKeys(load_private_key(stored["private_key"]), subject)

    private_b64 = generate_vapid_private_key_b64()
    keys = VapidKeys(load_private_key(private_b64), subject)
    if storage_path:
        os.makedirs(os.path.dirname(storage_path) or ".", exist_ok=True)
        with open(storage_path, "w", encoding="utf-8") as fh:
            json.dump({"private_key": private_b64, "public_key": keys.public_key_b64}, fh, indent=2)
        try:
            os.chmod(storage_path, 0o600)
        except OSError:  # pragma: no cover - e.g. Windows
            pass
    return keys


# ---------------------------------------------------------------------------
# Payload encryption (RFC 8291, aes128gcm)
# ---------------------------------------------------------------------------


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt_payload(payload: bytes, client_public_key_b64: str, client_auth_b64: str) -> bytes:
    """Encrypt ``payload`` for a subscription's ``p256dh`` and ``auth`` keys.

    Returns the complete ``aes128gcm`` body: header block followed by the
    single encrypted record.
    """
    client_public_bytes = b64url_decode(client_public_key_b64)
    auth_secret = b64url_decode(client_auth_b64)
    if len(client_public_bytes) != 65 or client_public_bytes[0] != 0x04:
        raise ValueError("Subscription p256dh key must be a 65-byte uncompressed P-256 point.")
    if len(auth_secret) != 16:
        raise ValueError("Subscription auth secret must be 16 bytes.")

    client_public_key = ec.EllipticCurvePublicKey.from_encoded_point(CURVE, client_public_bytes)
    local_key = ec.generate_private_key(CURVE)
    local_public_bytes = local_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared_secret = local_key.exchange(ec.ECDH(), client_public_key)

    key_info = b"WebPush: info\x00" + client_public_bytes + local_public_bytes
    ikm = _hkdf(auth_secret, shared_secret, key_info, 32)

    salt = os.urandom(16)
    content_key = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    max_plaintext = RECORD_SIZE - 16 - 1  # tag + delimiter
    if len(payload) > max_plaintext:
        raise ValueError(f"Push payload too large ({len(payload)} bytes, max {max_plaintext}).")
    # 0x02 marks the final (only) record.
    ciphertext = AESGCM(content_key).encrypt(nonce, payload + b"\x02", None)

    header = salt + struct.pack("!I", RECORD_SIZE) + bytes([len(local_public_bytes)]) + local_public_bytes
    return header + ciphertext


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


class PushGone(Exception):
    """The push service reports the subscription no longer exists (404/410)."""


class PushError(Exception):
    def __init__(self, status: int, body: str):
        super().__init__(f"Push service returned {status}: {body[:200]}")
        self.status = status


def send_web_push(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    payload: dict | str | bytes,
    vapid: VapidKeys,
    ttl: int = 24 * 3600,
    urgency: str = "normal",
    timeout: float = 10.0,
) -> int:
    """POST an encrypted notification to ``endpoint``. Returns the HTTP status."""
    if isinstance(payload, dict):
        payload = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    elif isinstance(payload, str):
        payload = payload.encode("utf-8")

    body = encrypt_payload(payload, p256dh, auth)
    headers = {
        "Content-Type": "application/octet-stream",
        "Content-Encoding": "aes128gcm",
        "Content-Length": str(len(body)),
        "TTL": str(ttl),
        "Urgency": urgency,
        "Authorization": vapid.authorization_header(endpoint),
    }
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            raise PushGone(str(exc.code)) from exc
        try:
            detail = exc.read().decode("utf-8", errors="replace")
        except Exception:  # pragma: no cover
            detail = ""
        raise PushError(exc.code, detail) from exc
