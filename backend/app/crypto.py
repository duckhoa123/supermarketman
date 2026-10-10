"""Encrypt OAuth tokens at rest with Fernet (AES-128-CBC + HMAC)."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

# Fixed key used only when no key is configured outside production, so local dev works out of the box.
_DEV_KEY = b"ZGV2LW9ubHkta2V5LWRvLW5vdC11c2UtaW4tcHJvZCE="


@lru_cache
def _fernet() -> Fernet:
    settings = get_settings()
    key = settings.token_encryption_key.encode() if settings.token_encryption_key else b""
    if not key:
        if settings.is_production:
            raise RuntimeError("TOKEN_ENCRYPTION_KEY must be set in production")
        key = _DEV_KEY
    return Fernet(key)


def encrypt(value: str | None) -> str | None:
    if value is None:
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:  # key rotated or data corrupted
        raise RuntimeError("Could not decrypt stored token; reconnect the integration") from exc
