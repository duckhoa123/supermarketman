"""Signed-in user: profile, onboarding (create first restaurant), notification preferences, push devices."""

from zoneinfo import available_timezones

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthUser, current_user
from app.db import get_session
from app.models import AlertRule, Location, Organization, OrgMember, PushSubscription, User, UserChannelPrefs
from app.tenancy import user_locations

router = APIRouter(tags=["account"])


class LocationOut(BaseModel):
    id: str
    name: str
    timezone: str
    currency: str


class MeOut(BaseModel):
    id: str
    email: str
    name: str | None
    phone: str | None
    locations: list[LocationOut]
    channels: dict


@router.get("/me", response_model=MeOut)
async def me(user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    row = await session.get(User, user.id)
    prefs = await session.get(UserChannelPrefs, user.id)
    locs = await user_locations(session, user.id)
    return MeOut(
        id=row.id, email=row.email, name=row.name, phone=row.phone,
        locations=[LocationOut(id=x.id, name=x.name, timezone=x.timezone, currency=x.currency) for x in locs],
        channels={
            "push": prefs.push if prefs else True,
            "email": prefs.email if prefs else True,
            "whatsapp": prefs.whatsapp if prefs else False,
            "sms": prefs.sms if prefs else False,
        },
    )


class OnboardIn(BaseModel):
    business_name: str = Field(min_length=1, max_length=200)
    location_name: str = Field(min_length=1, max_length=200)
    timezone: str = "Australia/Sydney"
    currency: str = Field(default="AUD", min_length=3, max_length=3)


@router.post("/onboarding", response_model=LocationOut)
async def onboard(body: OnboardIn, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    if body.timezone not in available_timezones():
        raise HTTPException(422, "Unknown time zone")
    org = Organization(name=body.business_name)
    session.add(org)
    await session.flush()
    session.add(OrgMember(org_id=org.id, user_id=user.id, role="owner"))
    loc = Location(org_id=org.id, name=body.location_name, timezone=body.timezone, currency=body.currency.upper())
    session.add(loc)
    await session.flush()
    for kind, channels in (("bad_review", ["push", "email"]), ("low_stock", ["push"]),
                           ("sales_drop", ["push"]), ("daily_digest", ["email"])):
        session.add(AlertRule(location_id=loc.id, kind=kind, channels=channels, params={}))
    await session.commit()
    return LocationOut(id=loc.id, name=loc.name, timezone=loc.timezone, currency=loc.currency)


class ProfileIn(BaseModel):
    name: str | None = None
    phone: str | None = Field(default=None, pattern=r"^\+[1-9]\d{6,14}$", description="E.164, e.g. +61412345678")
    channels: dict[str, bool] | None = None


@router.put("/me")
async def update_me(body: ProfileIn, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    row = await session.get(User, user.id)
    if body.name is not None:
        row.name = body.name
    if body.phone is not None:
        row.phone = body.phone
    if body.channels is not None:
        prefs = await session.get(UserChannelPrefs, user.id)
        if prefs is None:
            prefs = UserChannelPrefs(user_id=user.id)
            session.add(prefs)
        for ch in ("push", "email", "whatsapp", "sms"):
            if ch in body.channels:
                setattr(prefs, ch, bool(body.channels[ch]))
    await session.commit()
    return {"ok": True}


class PushIn(BaseModel):
    kind: str = Field(pattern="^(expo|webpush)$")
    token: str = Field(min_length=10, max_length=4000)


@router.post("/me/push-subscriptions")
async def add_push(body: PushIn, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    exists = await session.scalar(
        select(PushSubscription).where(PushSubscription.user_id == user.id, PushSubscription.token == body.token)
    )
    if exists is None:
        session.add(PushSubscription(user_id=user.id, kind=body.kind, token=body.token))
        await session.commit()
    return {"ok": True}


@router.delete("/me/push-subscriptions")
async def remove_push(body: PushIn, user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    await session.execute(
        delete(PushSubscription).where(PushSubscription.user_id == user.id, PushSubscription.token == body.token)
    )
    await session.commit()
    return {"ok": True}
