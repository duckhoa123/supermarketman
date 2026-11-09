"""Writing an order to a supplier: suggested items from their history, the email, then draft or send."""

import re

from sqlalchemy import select

from app.models import PurchaseOrder, Supplier
from tests.conftest import INTRUDER, OWNER, text_result


async def _dairy(session, location_id) -> Supplier:
    return await session.scalar(select(Supplier).where(Supplier.location_id == location_id,
                                                       Supplier.name.like("Harbour Dairy%")))


async def test_suggestion_without_ai_uses_delivery_history(client, session, demo_location_id):
    dairy = await _dairy(session, demo_location_id)
    base = f"/locations/{demo_location_id}/suppliers/{dairy.id}"
    assert (await client.post(f"{base}/order-suggestion", headers=INTRUDER)).status_code == 404
    out = (await client.post(f"{base}/order-suggestion", headers=OWNER)).json()
    assert out["ai"] is False and out["basis"]["deliveries"] >= 2 and out["lines"]
    assert all(ln["qty"] > 0 and ln["name"] and ln["reason"] for ln in out["lines"])

    empty = Supplier(location_id=demo_location_id, name="Brand new fish co")
    session.add(empty)
    await session.commit()
    none = (await client.post(f"/locations/{demo_location_id}/suppliers/{empty.id}/order-suggestion",
                              headers=OWNER)).json()
    assert none["lines"] == [] and "Nothing has been received" in none["summary"]


async def test_ai_suggestion_is_checked_against_the_facts(client, session, demo_location_id, fake_llm):
    dairy = await _dairy(session, demo_location_id)
    base = f"/locations/{demo_location_id}/suppliers/{dairy.id}"
    # First call to learn the product keys the model is shown.
    probe = fake_llm([text_result('{"lines": []}')])
    await client.post(f"{base}/order-suggestion", headers=OWNER)
    keys = re.findall(r'key "([^"]+)"', probe.calls[0]["messages"][0]["content"])
    assert keys and "not instructions" in probe.calls[0]["messages"][0]["content"]

    reply = ('{"lines": [{"key": "%s", "qty": 4.2, "reason": "Runs out by Friday"}, '
             '{"key": "made-up", "qty": 3, "reason": "x"}, {"key": "%s", "qty": 100000, "reason": "too many"}], '
             '"summary": "A normal week, a little extra burrata."}') % (keys[0], keys[-1])
    fake_llm([text_result(reply)])
    out = (await client.post(f"{base}/order-suggestion", headers=OWNER)).json()
    assert out["ai"] is True and out["summary"] == "A normal week, a little extra burrata."
    assert len(out["lines"]) == 1 and out["lines"][0]["reason"] == "Runs out by Friday"
    assert out["lines"][0]["qty"] in (4.5, 5.0)  # rounded to the unit


async def test_email_preview_then_draft_or_send(client, session, demo_location_id):
    dairy = await _dairy(session, demo_location_id)
    lines = [{"name": "Burrata 125g", "qty": 12, "unit": "ea"}, {"name": "Mascarpone 500g", "qty": 2, "unit": "tub"}]
    msg = (await client.post(f"/locations/{demo_location_id}/suppliers/{dairy.id}/order-message", headers=OWNER,
                             json={"lines": lines, "delivery_date": "2026-11-20"})).json()
    assert msg["to_email"] == dairy.email and "Friday, November 20" in msg["subject"]
    assert "- 12 ea Burrata 125g" in msg["body"] and "Hello Harbour Dairy" in msg["body"]

    base = f"/locations/{demo_location_id}/purchase-orders"
    draft = (await client.post(base, headers=OWNER, json={
        "supplier_id": dairy.id, "lines": lines, "delivery_date": "2026-11-20",
        "message": "Hi! Usual order please."})).json()
    assert draft["status"] == "draft" and draft["message"] == "Hi! Usual order please."
    shown = (await client.get(f"{base}/{draft['id']}/message", headers=OWNER)).json()
    assert shown["body"] == "Hi! Usual order please."  # a saved draft keeps its wording...
    await client.patch(f"{base}/{draft['id']}", headers=OWNER, json={"lines": lines[:1]})
    shown = (await client.get(f"{base}/{draft['id']}/message", headers=OWNER)).json()
    assert "Mascarpone" not in shown["body"] and "Burrata" in shown["body"]  # ...until its items change

    sent = (await client.post(base, headers=OWNER, json={"supplier_id": dairy.id, "lines": lines, "send": "email"})).json()
    assert sent["status"] == "approved" and sent["sent_via"] == "email"  # the email goes out in the background
    po = await session.get(PurchaseOrder, sent["id"])
    assert "Burrata 125g" in po.message

    nomail = Supplier(location_id=demo_location_id, name="No email co")
    session.add(nomail)
    await session.commit()
    r = await client.post(base, headers=OWNER, json={"supplier_id": nomail.id, "lines": lines, "send": "email"})
    assert r.status_code == 409 and "email" in r.json()["detail"]
    assert (await client.post(base, headers=OWNER, json={"supplier_id": dairy.id, "lines": []})).status_code == 422
