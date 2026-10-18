"""Agent avatars (name, icon, colour, tone) and chat with the Customer agent and the Driver Assistant.

The Restaurant Manager chats per restaurant at /locations/{id}/chat; the other two agents belong to the
person, not to a restaurant.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import LLMNotConfigured, get_llm
from app.agent.profiles import AGENT_LABELS, ICONS, TONES, all_profiles, save_profile
from app.agent.runner import run_chat
from app.auth import AuthUser, current_user
from app.db import get_session, sessionmaker
from app.models import Conversation, Driver, Message
from app.services.fleet import driver_for_user

router = APIRouter(prefix="/agents", tags=["agents"])
PERSONAL = ("customer", "driver")


@router.get("")
async def list_agents(user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    profiles = await all_profiles(session, user.id)
    driver = await driver_for_user(session, user.id, user.email)
    if driver is None:
        profiles.pop("driver")  # staff only, by invite
    return {"agents": list(profiles.values()), "icons": list(ICONS), "tones": list(TONES)}


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=60)
    icon: str | None = None
    color: str | None = Field(default=None, pattern=r"^#[0-9a-fA-F]{6}$")
    tone: str | None = None


@router.put("/{agent}/profile")
async def update_profile(body: ProfileIn, agent: str = Path(...), user: AuthUser = Depends(current_user),
                         session: AsyncSession = Depends(get_session)):
    if agent not in AGENT_LABELS:
        raise HTTPException(404, "Unknown agent")
    if agent == "driver" and await driver_for_user(session, user.id, user.email) is None:
        raise HTTPException(403, "The Driver Assistant is for our drivers")
    if body.icon is not None and body.icon not in ICONS:
        raise HTTPException(422, "Unknown icon")
    if body.tone is not None and body.tone not in TONES:
        raise HTTPException(422, "Unknown tone")
    return await save_profile(session, user.id, agent, display_name=body.display_name, icon=body.icon,
                              color=body.color, tone=body.tone)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    deep: bool = False
    lat: float | None = Field(default=None, ge=-90, le=90)  # the customer's current position, if shared
    lng: float | None = Field(default=None, ge=-180, le=180)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _personal_agent(agent: str, user: AuthUser, session: AsyncSession) -> Driver | None:
    if agent not in PERSONAL:
        raise HTTPException(404, "Unknown agent")
    if agent == "driver":
        driver = await driver_for_user(session, user.id, user.email)
        if driver is None:
            raise HTTPException(403, "The Driver Assistant is for our drivers")
        return driver
    return None


@router.post("/{agent}/chat")
async def chat(body: ChatIn, agent: str = Path(...), user: AuthUser = Depends(current_user),
               session: AsyncSession = Depends(get_session)):
    driver = await _personal_agent(agent, user, session)
    if body.conversation_id:
        conv = await session.get(Conversation, body.conversation_id)
        if conv is None or conv.user_id != user.id or conv.agent != agent or conv.location_id is not None:
            raise HTTPException(404, "Conversation not found")
    else:
        conv = Conversation(location_id=None, user_id=user.id, agent=agent, channel="app")
        session.add(conv)
        await session.commit()
    try:
        llm = get_llm()
    except LLMNotConfigured as exc:
        raise HTTPException(503, "The AI assistant isn't configured yet (ANTHROPIC_API_KEY missing)") from exc
    conversation_id, driver_id = conv.id, driver.id if driver else None
    position = (body.lat, body.lng) if body.lat is not None and body.lng is not None else None

    async def events():
        async with sessionmaker()() as s:
            c = await s.get(Conversation, conversation_id)
            d = await s.get(Driver, driver_id) if driver_id else None
            async for event in run_chat(s, llm, conversation=c, text=body.message, user_id=user.id, agent=agent,
                                        driver=d, position=position, deep=body.deep):
                yield _sse(event)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/{agent}/conversations")
async def conversations(agent: str = Path(...), user: AuthUser = Depends(current_user),
                        session: AsyncSession = Depends(get_session)):
    await _personal_agent(agent, user, session)
    rows = await session.scalars(select(Conversation).where(
        Conversation.user_id == user.id, Conversation.agent == agent, Conversation.location_id.is_(None)
    ).order_by(Conversation.updated_at.desc()).limit(30))
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()} for c in rows]


@router.get("/{agent}/conversations/{conversation_id}")
async def conversation(conversation_id: str, agent: str = Path(...), user: AuthUser = Depends(current_user),
                       session: AsyncSession = Depends(get_session)):
    await _personal_agent(agent, user, session)
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user.id or conv.agent != agent:
        raise HTTPException(404, "Conversation not found")
    msgs = await session.scalars(select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at))
    return {"id": conv.id, "title": conv.title, "messages": [
        {"role": m.role, "text": "".join(b.get("text", "") for b in m.content if b.get("type") == "text"),
         "created_at": m.created_at.isoformat()} for m in msgs]}
