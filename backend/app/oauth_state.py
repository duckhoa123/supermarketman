"""Signed, expiring OAuth `state` values so callbacks can't be forged or replayed for another location."""

import base64
import hashlib
import hmac
import json
import secrets
import time


class InvalidState(Exception):
    pass


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def make_state(secret: str, *, location_id: str, user_id: str, provider: str, ttl_s: int = 900) -> str:
    payload = {
        "l": location_id,
        "u": user_id,
        "p": provider,
        "n": secrets.token_urlsafe(8),
        "e": int(time.time()) + ttl_s,
    }
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_state(secret: str, state: str, *, provider: str) -> dict:
    try:
        body, sig = state.split(".", 1)
    except ValueError as exc:
        raise InvalidState("malformed state") from exc
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise InvalidState("bad signature")
    payload = json.loads(_unb64(body))
    if payload.get("p") != provider:
        raise InvalidState("provider mismatch")
    if payload.get("e", 0) < time.time():
        raise InvalidState("state expired")
    return {"location_id": payload["l"], "user_id": payload["u"]}
