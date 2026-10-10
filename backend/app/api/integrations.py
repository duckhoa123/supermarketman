"""Connect, list, re-sync and disconnect Clover / Square / Google for a location."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.config import get_settings
from app.db import get_session
from app.integrations.pos.base import POSAuthError
from app.integrations.pos.registry import POS_PROVIDERS, get_adapter
from app.integrations.reviews.google import get_google_client
from app.models import Integration, Location
from app.oauth_state import InvalidState, make_state, read_state
from app.services.integration_store import get_integration, save_tokens
from app.services.jobs import enqueue
from app.tenancy import require_manager, user_role

router = APIRouter(tags=["integrations"])
PROVIDERS = (*POS_PROVIDERS, "google")


def _redirect_uri(provider: str) -> str:
    return f"{get_settings().app_base_url}/oauth/{provider}/callback"


@router.get("/locations/{location_id}/integrations")
async def list_integrations(location: Location = Depends(require_manager), session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(select(Integration).where(Integration.location_id == location.id))
    return [
        {
            "provider": i.provider,
            "status": i.status,
            "external_id": i.external_id,
            "last_synced_at": i.last_synced_at.isoformat() if i.last_synced_at else None,
            "last_error": i.last_error,
            "choices": (i.meta or {}).get("location_choices"),  # Google: pick which business location
        }
        for i in rows
    ]


@router.get("/locations/{location_id}/integrations/{provider}/connect")
async def connect(provider: str, location: Location = Depends(require_manager), user: AuthUser = Depends(current_user)):
    if provider not in PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    s = get_settings()
    state = make_state(s.internal_token, location_id=location.id, user_id=user.id, provider=provider)
    if provider == "google":
        if not s.google_client_id:
            raise HTTPException(503, "Google is not configured on the server")
        return {"url": get_google_client().authorize_url(state, _redirect_uri(provider))}
    if provider == "clover" and not s.clover_app_id:
        raise HTTPException(503, "Clover is not configured on the server")
    try:
        return {"url": get_adapter(provider).authorize_url(state, _redirect_uri(provider))}
    except NotImplementedError as exc:
        raise HTTPException(501, str(exc)) from exc


@router.get("/oauth/{provider}/callback")
async def oauth_callback(provider: str, request: Request, session: AsyncSession = Depends(get_session)):
    """The provider sends the owner's browser here; we store tokens and send them back to the web app."""
    s = get_settings()
    q = dict(request.query_params)
    back = f"{s.web_app_url}/settings"
    if "error" in q or "code" not in q:
        return RedirectResponse(f"{back}?connected={provider}&error=cancelled")
    try:
        st = read_state(s.internal_token, q.get("state", ""), provider=provider)
    except InvalidState:
        return RedirectResponse(f"{back}?connected={provider}&error=expired")
    location = await session.get(Location, st["location_id"])
    if location is None or await user_role(session, st["user_id"], location) not in ("owner", "manager"):
        return RedirectResponse(f"{back}?connected={provider}&error=forbidden")
    try:
        if provider == "google":
            client = get_google_client()
            tokens = await client.exchange_code(q["code"], _redirect_uri(provider))
            integ = await save_tokens(session, location, provider, tokens)
            choices = await client.list_locations(tokens.access_token)
            integ.meta = {**(integ.meta or {}), "location_choices": choices}
            if len(choices) == 1:
                integ.external_id = choices[0]["name"]
                await enqueue(session, "poll_reviews", {"integration_id": integ.id})
        else:
            tokens = await get_adapter(provider).exchange_code(q["code"], q, _redirect_uri(provider))
            if not tokens.external_id:
                return RedirectResponse(f"{back}?connected={provider}&error=no_merchant")
            # One merchant can't be linked to two of our customers.
            clash = await session.scalar(
                select(Integration).where(Integration.provider == provider,
                                          Integration.external_id == tokens.external_id,
                                          Integration.location_id != location.id,
                                          Integration.status != "disconnected")
            )
            if clash:
                return RedirectResponse(f"{back}?connected={provider}&error=already_linked")
            integ = await save_tokens(session, location, provider, tokens)
            await enqueue(session, "sync_pos", {"integration_id": integ.id, "full": True},
                          dedupe_key=f"sync_pos:{integ.id}")
    except POSAuthError:
        return RedirectResponse(f"{back}?connected={provider}&error=token")
    await session.commit()
    return RedirectResponse(f"{back}?connected={provider}")


class GoogleLocationIn(BaseModel):
    name: str  # accounts/{a}/locations/{l} from the choices list


@router.put("/locations/{location_id}/integrations/google/location")
async def choose_google_location(body: GoogleLocationIn, location: Location = Depends(require_manager),
                                 session: AsyncSession = Depends(get_session)):
    integ = await get_integration(session, location.id, "google")
    if integ is None:
        raise HTTPException(404, "Google is not connected")
    valid = {c["name"] for c in (integ.meta or {}).get("location_choices", [])}
    if body.name not in valid:
        raise HTTPException(422, "Pick one of the listed locations")
    integ.external_id = body.name
    integ.cursor = {}
    await enqueue(session, "poll_reviews", {"integration_id": integ.id})
    await session.commit()
    return {"ok": True}


@router.post("/locations/{location_id}/integrations/{provider}/sync")
async def sync_now(provider: str, location: Location = Depends(require_manager),
                   session: AsyncSession = Depends(get_session)):
    integ = await get_integration(session, location.id, provider)
    if integ is None:
        raise HTTPException(404, "Not connected")
    kind = "poll_reviews" if provider == "google" else "sync_pos"
    await enqueue(session, kind, {"integration_id": integ.id}, dedupe_key=f"{kind}:{integ.id}")
    await session.commit()
    return {"queued": True}


@router.delete("/locations/{location_id}/integrations/{provider}")
async def disconnect(provider: str, location: Location = Depends(require_manager),
                     session: AsyncSession = Depends(get_session)):
    integ = await get_integration(session, location.id, provider)
    if integ is None:
        raise HTTPException(404, "Not connected")
    integ.status = "disconnected"
    integ.access_token_enc = None
    integ.refresh_token_enc = None
    await session.commit()
    return {"ok": True}
