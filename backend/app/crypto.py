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
            raise KeyMissing("The server has no TOKEN_ENCRYPTION_KEY, so it can't store connection tokens safely. "
                             "Add it to the api service's variables in Railway, then try again.")
        key = _DEV_KEY
    try:
        return Fernet(key)
    except ValueError as exc:
        raise KeyMissing("The server's TOKEN_ENCRYPTION_KEY isn't a valid key. Make a new one with "
                         "Fernet.generate_key() (see the README), put it in Railway, then try again.") from exc


class KeyMissing(RuntimeError):
    """The token encryption key isn't set up on the server."""


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
