"""Save, load and refresh integration tokens; mark integrations broken when a token is revoked."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import decrypt, encrypt
from app.integrations.pos.base import POSAuthError, TokenSet
from app.integrations.pos.registry import POS_PROVIDERS, get_adapter
from app.models import Integration, Location, utcnow

REFRESH_MARGIN = timedelta(minutes=5)


async def get_integration(session: AsyncSession, location_id: str, provider: str) -> Integration | None:
    return await session.scalar(
        select(Integration).where(Integration.location_id == location_id, Integration.provider == provider)
    )


async def pos_integration(session: AsyncSession, location_id: str) -> Integration | None:
    return await session.scalar(
        select(Integration).where(
            Integration.location_id == location_id,
            Integration.provider.in_(POS_PROVIDERS),
            Integration.status != "disconnected",
        )
    )


async def save_tokens(
    session: AsyncSession, location: Location, provider: str, tokens: TokenSet, external_id: str | None = None
) -> Integration:
    integ = await get_integration(session, location.id, provider)
    if integ is None:
        integ = Integration(location_id=location.id, provider=provider, cursor={}, meta={})
        session.add(integ)
    integ.access_token_enc = encrypt(tokens.access_token)
    if tokens.refresh_token:
        integ.refresh_token_enc = encrypt(tokens.refresh_token)
    integ.access_expires_at = tokens.access_expires_at
    if external_id or tokens.external_id:
        integ.external_id = external_id or tokens.external_id
    integ.status = "active"
    integ.last_error = None
    await session.flush()
    return integ


async def access_token(session: AsyncSession, integ: Integration) -> str:
    """Return a valid access token, refreshing (and persisting the rotated refresh token) when needed."""
    expires = integ.access_expires_at
    if expires is None or expires - REFRESH_MARGIN > utcnow():
        token = decrypt(integ.access_token_enc)
        if token:
            return token
    refresh_token = decrypt(integ.refresh_token_enc)
    if not refresh_token:
        await mark_broken(session, integ, "No refresh token stored; reconnect required")
        raise POSAuthError("reconnect required")
    try:
        if integ.provider == "google":
            from app.integrations.reviews.google import get_google_client

            tokens = await get_google_client().refresh(refresh_token)
        else:
            tokens = await get_adapter(integ.provider).refresh(refresh_token)
    except POSAuthError as exc:
        await mark_broken(session, integ, str(exc))
        raise
    integ.access_token_enc = encrypt(tokens.access_token)
    if tokens.refresh_token:  # Clover refresh tokens are single use: always store the new one
        integ.refresh_token_enc = encrypt(tokens.refresh_token)
    integ.access_expires_at = tokens.access_expires_at
    await session.commit()
    return tokens.access_token


async def mark_broken(session: AsyncSession, integ: Integration, reason: str) -> None:
    from app.services.alerts import create_alert

    integ.status = "error"
    integ.last_error = reason
    location = await session.get(Location, integ.location_id)
    if location is not None:
        name = {"clover": "Clover", "square": "Square", "google": "Google reviews"}.get(integ.provider, integ.provider)
        await create_alert(
            session,
            location,
            kind="integration_error",
            title=f"{name} is disconnected",
            body=f"We can't reach your {name} account any more. Open Settings and reconnect it.",
            dedupe_key=f"integration_error:{integ.id}:{utcnow().date()}",
        )
    await session.commit()
