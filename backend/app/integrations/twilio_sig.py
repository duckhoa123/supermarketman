"""Validate Twilio's X-Twilio-Signature header (HMAC-SHA1 of URL + sorted POST params, base64)."""

import base64
import hashlib
import hmac


def compute_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def is_valid(auth_token: str, url: str, params: dict[str, str], signature: str | None) -> bool:
    if not auth_token or not signature:
        return False
    return hmac.compare_digest(compute_signature(auth_token, url, params), signature)
