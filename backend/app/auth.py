"""Request authentication.

Production: the web and mobile apps sign in with Supabase Auth and send the Supabase access token as
`Authorization: Bearer <jwt>`. We verify it against Supabase's published signing keys (JWKS).

Development: with DEV_AUTH_ENABLED=true (never in production) `Authorization: Bearer dev:owner@example.com`
signs in as that email, so the app runs without a Supabase project.
"""

from dataclasses import dataclass

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.models import User


@dataclass
class AuthUser:
    id: str
    email: str


_jwks_client: jwt.PyJWKClient | None = None


def _jwks() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        if not settings.supabase_url:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "SUPABASE_URL is not configured")
        _jwks_client = jwt.PyJWKClient(f"{settings.supabase_url}/auth/v1/.well-known/jwks.json", cache_keys=True)
    return _jwks_client


def verify_token(token: str) -> AuthUser:
    settings = get_settings()
    if token.startswith("dev:"):
        if not settings.dev_auth_enabled or settings.is_production:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Dev tokens are disabled")
        email = token[4:].strip().lower()
        if "@" not in email:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid dev token")
        return AuthUser(id=f"dev-{email}", email=email)
    try:
        if settings.supabase_jwt_secret:  # older Supabase projects sign with a shared HS256 secret
            key, algorithms = settings.supabase_jwt_secret, ["HS256"]
        else:  # current projects publish asymmetric signing keys
            key, algorithms = _jwks().get_signing_key_from_jwt(token).key, ["ES256", "RS256"]
        claims = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=settings.supabase_jwt_audience,
            issuer=f"{settings.supabase_url}/auth/v1",
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token") from exc
    return AuthUser(id=claims["sub"], email=(claims.get("email") or "").lower())


async def current_user(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuthUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")
    user = verify_token(authorization.split(" ", 1)[1].strip())
    # Keep a local mirror of the user so we can join memberships and send notifications.
    existing = await session.scalar(select(User).where(User.id == user.id))
    if existing is None:
        session.add(User(id=user.id, email=user.email))
        await session.commit()
    return user
