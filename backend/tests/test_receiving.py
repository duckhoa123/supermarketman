"""Scanning supplier invoices into the receiving log, stock updates and price-change alerts."""

import unittest

from sqlalchemy import select

from app.agent.llm import LLMResult
from app.agent.pricing import Usage
from app.models import AIUsage, Alert, InvoiceScan, Item, ReceivedLine, Supplier
from app.services import invoice_math as im
from app.services.jobs import run_pending
from tests.conftest import INTRUDER, OWNER, parse_sse, text_result, tool_result

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # the fake model doesn't look at it


def invoice_result(**fields) -> LLMResult:
    return LLMResult(content=[{"type": "tool_use", "id": "toolu_inv", "name": "record_invoice", "input": fields}],
                     stop_reason="tool_use", usage=Usage(input_tokens=1800, output_tokens=300))


DAIRY_1 = dict(
    supplier_name="Harbour Dairy & Deli", supplier_email="accounts@harbourdairy.example", invoice_number="HD-2041",
    invoice_date="2026-10-01", subtotal=71.0, tax=0.0, total=71.0,
    lines=[
        {"description": "Burrata 125g x 6", "qty": 6, "unit": "ea", "unit_price": 7.50, "line_total": 45.0},
        {"description": "Mascarpone 500g", "qty": 2, "unit": "tub", "unit_price": 10.50, "line_total": 21.0},
        {"description": "Delivery fee", "qty": 1, "unit_price": 5.0, "line_total": 5.0, "kind": "fee"},
    ],
)


async def _upload(client, location_id: str, name: str = "invoice.png", data: bytes = PNG, ctype: str = "image/png"):
    return await client.post(f"/locations/{location_id}/invoices", headers=OWNER, files={"file": (name, data, ctype)})


async def _scan_and_read(client, location_id: str, fake_llm, fields: dict) -> dict:
    llm = fake_llm([invoice_result(**fields)])
    r = await _upload(client, location_id)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "processing"
    await run_pending(time_budget_s=20)
    scan = (await client.get(f"/locations/{location_id}/invoices/{r.json()['id']}", headers=OWNER)).json()
    return {"scan": scan, "llm": llm}


class InvoiceMathTests(unittest.TestCase):
    def test_clean_extraction_fills_gaps_and_warns(self):
        out = im.clean_extraction({"supplier_name": "Coastal", "subtotal": 30, "lines": [
            {"description": "Tomatoes", "qty": 2, "line_total": 10},
            {"description": "Basil", "qty": 1, "unit_price": "$4.00"},
            {"description": "Return: cracked eggs", "line_total": -3},
            {"description": ""},
        ]})
        assert [ln["unit_price_cents"] for ln in out["lines"]] == [500, 400, -300]
        assert out["lines"][2]["kind"] == "credit"
        assert any("add up to $11.00" in w for w in out["warnings"])
        assert any("date" in w for w in out["warnings"])

    def test_price_change_compares_per_stock_unit(self):
        # A case of 24 at $52.80 is $2.20 a bottle; last time singles were $2.00: +10%.
        assert im.price_change_pct(5280, 24, 200, 1) == 10.0
        assert im.price_change_pct(750, 1, 750, 1) == 0.0
        assert im.price_change_pct(750, 1, None, 1) is None
        assert im.product_key("Harbour Dairy", "Burrata 125g", "BR-125") == "harbour dairy|sku:br 125"
        assert im.weekly_impact_cents(90, 24) == 270  # +90c on 24 units over 8 weeks = $2.70 a week


async def test_scan_review_confirm_then_price_change_alert(client, session, demo_location_id, fake_llm):
    burrata = await session.scalar(select(Item).where(Item.location_id == demo_location_id,
                                                      Item.name == "Burrata & Tomato"))
    burrata_id, stock_before = burrata.id, burrata.stock_qty  # plain values: objects expire below

    first = await _scan_and_read(client, demo_location_id, fake_llm, DAIRY_1)
    scan, llm = first["scan"], first["llm"]
    call = llm.calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "record_invoice"}
    assert call["messages"][0]["content"][0]["type"] == "image"
    assert scan["status"] == "review" and scan["invoice_number"] == "HD-2041"
    assert scan["supplier_name"] == "Harbour Dairy & Deli (demo)"  # matched to the existing supplier
    lines = {ln["description"]: ln for ln in scan["lines"]}
    assert lines["Burrata 125g x 6"]["item_id"] == burrata_id and lines["Burrata 125g x 6"]["match"].startswith("name")
    assert lines["Mascarpone 500g"]["item_id"] is None
    assert lines["Delivery fee"]["kind"] == "fee"
    assert lines["Burrata 125g x 6"]["last_price_cents"] is None  # first delivery: nothing to compare with
    usage = await session.scalar(select(AIUsage).where(AIUsage.purpose == "invoice_scan"))
    assert usage.location_id == demo_location_id and usage.cost_usd > 0
    supplier = await session.scalar(select(Supplier).where(Supplier.id == scan["supplier_id"]))
    assert supplier.email == "orders@example.com"  # an existing email isn't overwritten

    r = await client.post(f"/locations/{demo_location_id}/invoices/{scan['id']}/confirm", headers=OWNER)
    assert r.status_code == 200, r.text
    assert r.json()["alerted"] is False and r.json()["stock_updates"][0]["added"] == 6
    session.expire_all()
    assert (await session.get(Item, burrata_id)).stock_qty == stock_before + 6
    log = (await client.get(f"/locations/{demo_location_id}/receiving-log?q=HD-20", headers=OWNER)).json()
    assert log["count"] == 3 and log["total"] == "$71.00"
    assert (await client.post(f"/locations/{demo_location_id}/invoices/{scan['id']}/confirm",
                              headers=OWNER)).status_code == 409  # already confirmed

    # Next delivery: burrata up from $7.50 to $8.40 (+12%), mascarpone unchanged.
    second_fields = {**DAIRY_1, "invoice_number": "HD-2077", "invoice_date": "2026-10-15", "subtotal": 76.4, "lines": [
        {"description": "Burrata 125g x 6", "qty": 6, "unit": "ea", "unit_price": 8.40, "line_total": 50.4},
        {"description": "Mascarpone 500g", "qty": 2, "unit": "tub", "unit_price": 10.50, "line_total": 21.0},
        {"description": "Delivery fee", "qty": 1, "unit_price": 5.0, "line_total": 5.0, "kind": "fee"},
    ]}
    second = (await _scan_and_read(client, demo_location_id, fake_llm, second_fields))["scan"]
    lines = {ln["description"]: ln for ln in second["lines"]}
    assert lines["Burrata 125g x 6"]["match"] == "remembered" and lines["Burrata 125g x 6"]["change_pct"] == 12.0
    assert lines["Mascarpone 500g"]["change_pct"] == 0.0
    r = await client.post(f"/locations/{demo_location_id}/invoices/{second['id']}/confirm", headers=OWNER)
    assert r.json()["alerted"] is True

    alert = await session.scalar(select(Alert).where(Alert.dedupe_key == f"price:{second['id']}"))
    assert alert.title == "Remy: Burrata 125g x 6 is up 12% at Harbour Dairy & Deli (demo)"
    assert "$8.40 per ea, was $7.50 on 01 Oct (+12%)" in alert.body and "a week" in alert.body
    assert alert.data["from_agent"] == "restaurant_manager"
    changes = (await client.get(f"/locations/{demo_location_id}/price-changes", headers=OWNER)).json()
    ours = [c for c in changes["changes"] if c["invoice_number"] == "HD-2077"]
    assert len(ours) == 1 and ours[0]["previous_unit_price"] == "$7.50"
    history = (await client.get(f"/locations/{demo_location_id}/price-history?q=burrata 125g x", headers=OWNER)).json()
    assert [p["unit_price"] for p in history["products"][0]["prices"]] == ["$8.40", "$7.50"]
    assert history["products"][0]["change_since_first_pct"] == 12.0

    csv_r = await client.get(f"/locations/{demo_location_id}/receiving-log?format=csv&q=HD-20", headers=OWNER)
    assert csv_r.headers["content-type"].startswith("text/csv")
    rows = csv_r.text.strip().splitlines()
    assert rows[0].startswith("received_on,supplier,invoice_number,description") and len(rows) == 7
    assert "8.4" in csv_r.text and "12.0" in csv_r.text

    # The Restaurant Manager answers from the same data.
    llm = fake_llm([tool_result("get_price_changes", {"days": 30}), text_result("Burrata went up 12%.")])
    events = parse_sse((await client.post(f"/locations/{demo_location_id}/chat", headers=OWNER,
                                          json={"message": "Did any supplier prices go up?"})).text)
    assert {"type": "tool", "name": "get_price_changes"} in events
    assert "Burrata 125g x 6" in llm.calls[1]["messages"][-1]["content"][0]["content"]

    # Another restaurant can't see any of it.
    for path in ("invoices", "receiving-log", "price-changes", f"invoices/{scan['id']}"):
        assert (await client.get(f"/locations/{demo_location_id}/{path}", headers=INTRUDER)).status_code == 404


async def test_edit_lines_pack_size_new_supplier_and_duplicates(client, session, demo_location_id, fake_llm):
    fields = dict(supplier_name="Granville Island Beverages", invoice_number="GIB-9", invoice_date="2026-10-10",
                  lines=[{"description": "Peroni Nastro Azzurro 330ml", "qty": 2, "unit": "case", "unit_price": 52.80,
                          "line_total": 105.60}])
    scan = (await _scan_and_read(client, demo_location_id, fake_llm, fields))["scan"]
    assert any("isn't in your suppliers" in w for w in scan["warnings"])
    peroni_line = scan["lines"][0]
    assert peroni_line["match"].startswith("name")
    # Each case holds 24 bottles: stock goes up by 48.
    r = await client.put(f"/locations/{demo_location_id}/invoices/{scan['id']}", headers=OWNER,
                         json={"lines": [{**peroni_line, "pack_size": 24}]})
    assert r.status_code == 200 and r.json()["lines"][0]["pack_size"] == 24
    peroni = await session.get(Item, peroni_line["item_id"])
    before = peroni.stock_qty
    r = await client.post(f"/locations/{demo_location_id}/invoices/{scan['id']}/confirm", headers=OWNER)
    assert r.status_code == 200 and r.json()["stock_updates"][0]["added"] == 48
    session.expire_all()
    assert (await session.get(Item, peroni_line["item_id"])).stock_qty == before + 48
    new_supplier = await session.scalar(select(Supplier).where(Supplier.name == "Granville Island Beverages"))
    assert new_supplier is not None  # added from the invoice

    # The same invoice scanned twice is caught.
    dup = (await _scan_and_read(client, demo_location_id, fake_llm, fields))["scan"]
    assert any("already received" in w for w in dup["warnings"])
    assert (await client.post(f"/locations/{demo_location_id}/invoices/{dup['id']}/confirm",
                              headers=OWNER)).status_code == 409
    assert (await client.post(f"/locations/{demo_location_id}/invoices/{dup['id']}/discard",
                              headers=OWNER)).status_code == 200
    # A later invoice remembers the pack size, so a case at $55.20 reads as $2.30 a bottle (+4.5%).
    later = {**fields, "invoice_number": "GIB-12", "lines": [{**fields["lines"][0], "unit_price": 55.20,
                                                              "line_total": 110.40}]}
    again = (await _scan_and_read(client, demo_location_id, fake_llm, later))["scan"]
    assert again["lines"][0]["pack_size"] == 24 and again["lines"][0]["change_pct"] == 4.5


async def test_unmatching_a_line_by_hand_sticks(client, demo_location_id, fake_llm):
    scan = (await _scan_and_read(client, demo_location_id, fake_llm, DAIRY_1))["scan"]
    lines = [{**ln, "item_id": None, "match": "none"} if ln["description"].startswith("Burrata") else ln
             for ln in scan["lines"]]
    r = await client.put(f"/locations/{demo_location_id}/invoices/{scan['id']}", headers=OWNER, json={"lines": lines})
    assert next(ln for ln in r.json()["lines"] if ln["description"].startswith("Burrata"))["item_id"] is None


async def test_upload_checks_and_failures(client, session, demo_location_id, fake_llm):
    bad = await _upload(client, demo_location_id, "notes.txt", b"hello", "text/plain")
    assert bad.status_code == 422 and "photo" in bad.json()["detail"]
    pdf = await _upload(client, demo_location_id, "inv.pdf", b"%PDF-1.4 fake", "application/pdf")
    assert pdf.status_code == 200
    # The model returns nothing usable: the scan fails with a message and can be retried.
    fake_llm([text_result("Sorry, I can't read that.")])
    await run_pending(time_budget_s=20)
    session.expire_all()
    scan = await session.get(InvoiceScan, pdf.json()["id"])
    assert scan.status == "failed" and "couldn't read" in scan.error
    llm = fake_llm([invoice_result(**DAIRY_1)])
    assert (await client.post(f"/locations/{demo_location_id}/invoices/{scan.id}/retry", headers=OWNER)).status_code == 200
    await run_pending(time_budget_s=20)
    assert llm.calls[0]["messages"][0]["content"][0]["type"] == "document"  # PDFs go to the model as documents
    out = (await client.get(f"/locations/{demo_location_id}/invoices/{scan.id}", headers=OWNER)).json()
    assert out["status"] == "review" and len(out["lines"]) == 3
    file_r = await client.get(f"/locations/{demo_location_id}/invoices/{scan.id}/file", headers=OWNER)
    assert file_r.status_code == 200 and file_r.content.startswith(b"%PDF")


async def test_without_ai_key_lines_can_be_entered_by_hand(client, session, demo_location_id):
    r = await _upload(client, demo_location_id)
    await run_pending(time_budget_s=20)
    scan = (await client.get(f"/locations/{demo_location_id}/invoices/{r.json()['id']}", headers=OWNER)).json()
    assert scan["status"] == "review" and "by hand" in scan["warnings"][0]
    r = await client.put(f"/locations/{demo_location_id}/invoices/{scan['id']}", headers=OWNER, json={
        "supplier_name": "Coastal Produce Co. (demo)", "invoice_number": "CP-1", "invoice_date": "2026-10-20",
        "lines": [{"description": "Heirloom tomatoes", "qty": 10, "unit": "kg", "unit_price_cents": 420}]})
    assert r.json()["lines"][0]["line_total_cents"] == 4200
    assert (await client.post(f"/locations/{demo_location_id}/invoices/{scan['id']}/confirm",
                              headers=OWNER)).status_code == 200
    line = await session.scalar(select(ReceivedLine).where(ReceivedLine.description == "Heirloom tomatoes"))
    assert line.received_on.isoformat() == "2026-10-20" and line.supplier_name == "Coastal Produce Co. (demo)"
    report = (await client.get(f"/locations/{demo_location_id}/reports/month", headers=OWNER)).json()
    assert "deliveries" in report
