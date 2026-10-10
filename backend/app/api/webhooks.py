"""Inbound webhooks: Clover (POS changes) and Twilio (owners texting the assistant on WhatsApp/SMS)."""

import logging
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import LLMNotConfigured, get_llm
from app.agent.runner import answer_text
from app.config import get_settings
from app.db import get_session
from app.integrations.pos.clover_parse import verify_auth_header
from app.integrations.twilio_sig import is_valid as twilio_valid
from app.models import Conversation, User, WebhookEvent, utcnow
from app.services.jobs import enqueue
from app.tenancy import user_locations

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger(__name__)


@router.post("/clover")
async def clover_webhook(request: Request, x_clover_auth: str | None = Header(default=None),
                         session: AsyncSession = Depends(get_session)):
    body = await request.json()
    if "verificationCode" in body:
        # One-time handshake when the webhook URL is saved in the Clover dashboard. The code is shown in the
        # logs so you can paste it into the dashboard; nothing else is done with it.
        log.warning("Clover webhook verification code: %s", body["verificationCode"])
        return {"ok": True}
    if not verify_auth_header(x_clover_auth, get_settings().clover_webhook_auth_code):
        raise HTTPException(401, "Bad Clover auth code")
    event = WebhookEvent(provider="clover", body=body)
    session.add(event)
    await session.flush()
    # Acknowledge fast; fetching the changed orders happens in the worker.
    await enqueue(session, "pos_webhook", {"event_id": event.id})
    await session.commit()
    return {"ok": True}


def _twiml(text: str) -> Response:
    xml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{escape(text[:1500])}</Message></Response>'
    return Response(content=xml, media_type="application/xml")


@router.post("/twilio")
async def twilio_inbound(request: Request, x_twilio_signature: str | None = Header(default=None),
                         session: AsyncSession = Depends(get_session)):
    s = get_settings()
    form = {k: str(v) for k, v in (await request.form()).items()}
    url = f"{s.app_base_url}/webhooks/twilio"
    if not twilio_valid(s.twilio_auth_token, url, form, x_twilio_signature):
        raise HTTPException(403, "Bad Twilio signature")
    sender = form.get("From", "")
    channel = "whatsapp" if sender.startswith("whatsapp:") else "sms"
    phone = sender.removeprefix("whatsapp:")
    text = (form.get("Body") or "").strip()
    user = await session.scalar(select(User).where(User.phone == phone))
    if user is None:
        return _twiml("This number isn't linked to an account. Add your mobile number in the app under Settings.")
    locations = await user_locations(session, user.id)
    if not locations:
        return _twiml("Your account has no restaurant set up yet. Finish setup in the app first.")
    location = locations[0]
    conv = await session.scalar(
        select(Conversation)
        .where(Conversation.user_id == user.id, Conversation.location_id == location.id,
               Conversation.channel == channel)
        .order_by(Conversation.updated_at.desc())
    )
    if conv is None or (utcnow() - conv.updated_at).total_seconds() > 6 * 3600:
        conv = Conversation(location_id=location.id, user_id=user.id, channel=channel)
        session.add(conv)
        await session.commit()
    try:
        llm = get_llm()
    except LLMNotConfigured:
        return _twiml("The assistant isn't switched on yet.")
    reply, proposals = await answer_text(session, llm, location=location, user_id=user.id, conversation=conv,
                                         text=text)
    if proposals:
        reply += "\n\nOpen the app to approve the suggested change."
    return _twiml(reply or "Sorry, I couldn't answer that.")
