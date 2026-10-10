"""Chat with the agent. Replies stream as Server-Sent Events so text appears as it's written."""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import LLMNotConfigured, get_llm
from app.agent.runner import run_chat
from app.auth import AuthUser, current_user
from app.db import get_session, sessionmaker
from app.models import Conversation, Location, Message
from app.tenancy import require_location

router = APIRouter(prefix="/locations/{location_id}", tags=["chat"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = None
    deep: bool = False  # "Deep analysis" toggle: use the stronger model


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@router.post("/chat")
async def chat(body: ChatIn, location: Location = Depends(require_location), user: AuthUser = Depends(current_user),
               session: AsyncSession = Depends(get_session)):
    if body.conversation_id:
        conv = await session.get(Conversation, body.conversation_id)
        if conv is None or conv.location_id != location.id or conv.user_id != user.id:
            raise HTTPException(404, "Conversation not found")
    else:
        conv = Conversation(location_id=location.id, user_id=user.id, channel="app")
        session.add(conv)
        await session.commit()
    try:
        llm = get_llm()
    except LLMNotConfigured as exc:
        raise HTTPException(503, "The AI assistant isn't configured yet (ANTHROPIC_API_KEY missing)") from exc

    location_id, conversation_id = location.id, conv.id

    async def events():
        # The stream outlives the request's dependency session, so it opens its own.
        async with sessionmaker()() as s:
            loc = await s.get(Location, location_id)
            c = await s.get(Conversation, conversation_id)
            async for event in run_chat(s, llm, location=loc, user_id=user.id, conversation=c,
                                        text=body.message, deep=body.deep):
                yield _sse(event)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/conversations")
async def list_conversations(location: Location = Depends(require_location), user: AuthUser = Depends(current_user),
                             session: AsyncSession = Depends(get_session)):
    rows = await session.scalars(
        select(Conversation)
        .where(Conversation.location_id == location.id, Conversation.user_id == user.id)
        .order_by(Conversation.updated_at.desc())
        .limit(30)
    )
    return [{"id": c.id, "title": c.title, "updated_at": c.updated_at.isoformat()} for c in rows]


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str, location: Location = Depends(require_location),
                           user: AuthUser = Depends(current_user), session: AsyncSession = Depends(get_session)):
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.location_id != location.id or conv.user_id != user.id:
        raise HTTPException(404, "Conversation not found")
    msgs = await session.scalars(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
    )
    return {
        "id": conv.id,
        "title": conv.title,
        "messages": [
            {"role": m.role, "text": "".join(b.get("text", "") for b in m.content if b.get("type") == "text"),
             "created_at": m.created_at.isoformat()}
            for m in msgs
        ],
    }
