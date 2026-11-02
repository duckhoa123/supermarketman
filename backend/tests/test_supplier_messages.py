"""Messages to suppliers: the restaurant's template, the per-order message, and the ways to send it."""

from app.models import PurchaseOrder
from app.services.jobs import run_pending
from app.services.purchasing import DEFAULT_TEMPLATE, fill
from tests.conftest import INTRUDER, OWNER


def test_fill_drops_empty_lines_and_keeps_unknown_words():
    values = {"supplier": "Harbour Dairy", "items": "- 5 Tiramisu", "restaurant": "Demo Bistro",
              "delivery_date": "Tuesday, November 3", "address": "", "phone": "", "your_name": "Remy", "note": "",
              "ref": "ab12"}
    text = fill(DEFAULT_TEMPLATE, values)
    assert text.startswith("Hello Harbour Dairy,") and "- 5 Tiramisu" in text
    assert "for Demo Bistro on Tuesday, November 3." in text
    assert "Delivery address" not in text  # no address: the line goes
    assert fill("Hi {supplier} {Friday}", values) == "Hi Harbour Dairy {Friday}"


async def _draft(client, loc: str) -> dict:
    await client.post(f"/locations/{loc}/purchase-orders/draft", headers=OWNER)
    pos = (await client.get(f"/locations/{loc}/purchase-orders", headers=OWNER)).json()
    return next(p for p in pos if p["status"] == "draft" and p["supplier"].startswith("Harbour Dairy"))


async def test_template_and_message(client, demo_location_id):
    base = f"/locations/{demo_location_id}"
    t = (await client.get(f"{base}/order-template", headers=OWNER)).json()
    assert t["is_default"] and "{items}" in t["template"] and {p["key"] for p in t["placeholders"]} >= {
        "supplier", "items", "restaurant", "delivery_date"}

    bad = await client.put(f"{base}/order-template", headers=OWNER, json={"template": "Hello {supplier}"})
    assert bad.status_code == 422  # must say what's being ordered
    mine = "Hi {supplier}!\nPlease bring:\n{items}\nto {restaurant} on {delivery_date}.\nCheers"
    r = (await client.put(f"{base}/order-template", headers=OWNER, json={"template": mine})).json()
    assert r["template"] == mine and not r["is_default"]
    assert (await client.put(f"{base}/order-template", headers=INTRUDER, json={"template": mine})).status_code == 404

    draft = await _draft(client, demo_location_id)
    msg = (await client.get(f"{base}/purchase-orders/{draft['id']}/message?delivery_date=2026-11-06",
                            headers=OWNER)).json()
    assert msg["body"].startswith("Hi Harbour Dairy") and "Tiramisu" in msg["body"]
    assert "on Friday, November 6." in msg["body"] and msg["delivery_date"] == "2026-11-06"
    assert "Friday, November 6" in msg["subject"] and msg["ready"] == {"email": False, "sms": False}

    # Back to the default template.
    r = (await client.put(f"{base}/order-template", headers=OWNER, json={"template": ""})).json()
    assert r["is_default"] and r["template"] == DEFAULT_TEMPLATE


async def test_send_yourself_after_editing(client, session, demo_location_id):
    base = f"/locations/{demo_location_id}/purchase-orders"
    draft = await _draft(client, demo_location_id)
    assert (await client.patch(f"{base}/{draft['id']}", headers=OWNER,
                               json={"delivery_date": "2026-11-07"})).status_code == 200
    text = "Hello Harbour Dairy,\nI want to order 5 Tiramisu for Demo Bistro on Saturday.\nThank you."
    r = await client.post(f"{base}/{draft['id']}/send", headers=OWNER, json={"channel": "manual", "message": text})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    po = next(p for p in (await client.get(base, headers=OWNER)).json() if p["id"] == draft["id"])
    assert po["sent_via"] == "manual" and po["message"] == text and po["delivery_date"] == "2026-11-07"
    assert po["sent_at"]
    # Once sent, the message shown is what was sent, and it can't be sent again.
    shown = (await client.get(f"{base}/{draft['id']}/message", headers=OWNER)).json()
    assert shown["body"] == text
    assert (await client.post(f"{base}/{draft['id']}/send", headers=OWNER,
                              json={"channel": "manual"})).status_code == 409


async def test_email_falls_back_to_sending_yourself(client, session, demo_location_id):
    base = f"/locations/{demo_location_id}/purchase-orders"
    draft = await _draft(client, demo_location_id)
    sms = await client.post(f"{base}/{draft['id']}/send", headers=OWNER, json={"channel": "sms"})
    assert sms.status_code == 409  # no phone on file or texting not set up
    r = await client.post(f"{base}/{draft['id']}/send", headers=OWNER, json={"channel": "email"})
    assert r.json()["status"] == "approved"
    await run_pending(time_budget_s=20)
    session.expire_all()
    po = await session.get(PurchaseOrder, draft["id"])
    assert po.status == "approved" and "Not sent by email" in po.send_error and "{items}" not in po.message
    # Email isn't set up in tests: the manager sends it themselves and marks it sent.
    r = await client.post(f"{base}/{draft['id']}/send", headers=OWNER, json={"channel": "manual"})
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert (await client.post(f"{base}/{draft['id']}/send", headers=INTRUDER,
                              json={"channel": "manual"})).status_code == 404
