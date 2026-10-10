"""Deliver an alert to every member of the restaurant's organization on the channels they opted into.

Providers are called over plain HTTPS so there are no SDK dependencies:
- Expo push:  POST https://exp.host/--/api/v2/push/send
- Web push:   via the optional `pywebpush` package (VAPID); skipped if not installed or not configured
- Email:      Resend  POST https://api.resend.com/emails
- WhatsApp/SMS: Twilio  POST https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json
Unconfigured channels are recorded as "skipped", so local development never sends anything.
"""

import json
import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import (
    Alert,
    Location,
    NotificationDelivery,
    OrgMember,
    PushSubscription,
    User,
    UserChannelPrefs,
)

log = logging.getLogger(__name__)

# Alert channel names (as stored on rules) -> delivery channels.
CHANNEL_MAP = {"push": ("expo", "webpush"), "email": ("email",), "whatsapp": ("whatsapp",), "sms": ("sms",)}

_http: httpx.AsyncClient | None = None


def http() -> httpx.AsyncClient:
    global _http
    if _http is None:
        _http = httpx.AsyncClient(timeout=15.0)
    return _http


class Skipped(Exception):
    pass


async def send_expo(tokens: list[str], title: str, body: str, data: dict) -> None:
    if not tokens:
        raise Skipped("no device")
    resp = await http().post(
        "https://exp.host/--/api/v2/push/send",
        json=[{"to": t, "title": title, "body": body[:180], "data": data, "sound": "default"} for t in tokens],
    )
    resp.raise_for_status()


async def send_webpush(subscriptions: list[str], title: str, body: str, data: dict) -> None:
    s = get_settings()
    if not subscriptions:
        raise Skipped("no browser subscribed")
    if not (s.vapid_private_key and s.vapid_public_key):
        raise Skipped("VAPID keys not configured")
    try:
        from pywebpush import webpush  # optional dependency
    except ImportError as exc:
        raise Skipped("pywebpush not installed") from exc
    payload = json.dumps({"title": title, "body": body[:180], "data": data})
    for sub in subscriptions:
        webpush(json.loads(sub), payload, vapid_private_key=s.vapid_private_key,
                vapid_claims={"sub": s.vapid_subject})


async def send_email(to: str, title: str, body: str) -> None:
    s = get_settings()
    if not s.resend_api_key:
        raise Skipped("email provider not configured")
    html = "<br>".join(json.dumps(line)[1:-1] for line in body.split("\n"))
    resp = await http().post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {s.resend_api_key}"},
        json={"from": s.email_from, "to": [to], "subject": title, "text": body,
              "html": f"<p>{html}</p><p style='color:#888'>Manage alerts in the app under Settings.</p>"},
    )
    resp.raise_for_status()


async def send_twilio(to: str, body: str, *, whatsapp: bool) -> None:
    s = get_settings()
    sender = s.twilio_whatsapp_from if whatsapp else s.twilio_sms_from
    if not (s.twilio_account_sid and s.twilio_auth_token and sender):
        raise Skipped("Twilio not configured")
    if not to:
        raise Skipped("no phone number on profile")
    # Business-initiated WhatsApp messages outside the 24h service window need an approved template;
    # set TWILIO_WHATSAPP_CONTENT_SID to send through one (variables: 1=body).
    form = {"From": sender, "To": f"whatsapp:{to}" if whatsapp else to}
    content_sid = s.twilio_whatsapp_content_sid
    if whatsapp and content_sid:
        form |= {"ContentSid": content_sid, "ContentVariables": json.dumps({"1": body[:900]})}
    else:
        form["Body"] = body[:1500]
    resp = await http().post(
        f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
        data=form,
        auth=(s.twilio_account_sid, s.twilio_auth_token),
    )
    resp.raise_for_status()


async def deliver_alert(session: AsyncSession, alert_id: str) -> dict:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        return {"status": "missing"}
    location = await session.get(Location, alert.location_id)
    members = list(
        await session.execute(
            select(User, UserChannelPrefs)
            .join(OrgMember, OrgMember.user_id == User.id)
            .outerjoin(UserChannelPrefs, UserChannelPrefs.user_id == User.id)
            .where(OrgMember.org_id == location.org_id)
        )
    )
    already = {
        (d.user_id, d.channel)
        for d in await session.scalars(
            select(NotificationDelivery).where(
                NotificationDelivery.alert_id == alert.id, NotificationDelivery.status == "sent"
            )
        )
    }
    results: dict[str, int] = {}
    for user, prefs in members:
        wanted = set()
        for ch in alert.channels or []:
            if prefs is not None and not getattr(prefs, ch, True):
                continue
            if ch in ("whatsapp", "sms") and (prefs is None or not getattr(prefs, ch)):
                continue  # paid channels are opt-in per user
            wanted.update(CHANNEL_MAP.get(ch, ()))
        subs = list(await session.scalars(select(PushSubscription).where(PushSubscription.user_id == user.id)))
        text = f"{alert.title}\n{alert.body}"
        for channel in sorted(wanted):
            if (user.id, channel) in already:
                continue
            delivery = NotificationDelivery(alert_id=alert.id, user_id=user.id, channel=channel)
            try:
                if channel == "expo":
                    await send_expo([s.token for s in subs if s.kind == "expo"], alert.title, alert.body,
                                    {"alert_id": alert.id, "kind": alert.kind})
                elif channel == "webpush":
                    await send_webpush([s.token for s in subs if s.kind == "webpush"], alert.title, alert.body,
                                       {"alert_id": alert.id, "kind": alert.kind})
                elif channel == "email":
                    await send_email(user.email, alert.title, alert.body)
                elif channel == "whatsapp":
                    await send_twilio(user.phone or "", text, whatsapp=True)
                elif channel == "sms":
                    await send_twilio(user.phone or "", text, whatsapp=False)
                delivery.status = "sent"
            except Skipped as exc:
                delivery.status, delivery.error = "skipped", str(exc)
            except Exception as exc:  # noqa: BLE001 - recorded per delivery, other channels still go out
                log.warning("delivery %s via %s failed: %s", alert.id, channel, exc)
                delivery.status, delivery.error = "failed", str(exc)[:500]
            session.add(delivery)
            results[delivery.status] = results.get(delivery.status, 0) + 1
    await session.commit()
    return results
