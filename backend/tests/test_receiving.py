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


async def _stock_item(session, location_id: str, name: str, unit: str, qty: float = 4) -> str:
    """An inventory item (an ingredient, not a dish), as added on Inventory items."""
    item = Item(location_id=location_id, provider="pass", external_id=f"pass-{name}", name=name, unit=unit,
                stock_qty=qty, price_cents=0, category="Dairy", active=True)
    session.add(item)
    await session.commit()
    return item.id


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

    def test_packs_are_read_from_the_packing_column_or_the_name(self):
        assert im.parse_pack("40X400G") == {"pack_count": 40, "each_qty": 400, "each_unit": "g"}
        assert im.parse_pack("BAG", "GOLDEN CAMEL RICE/ GAO LAC DA 40LBS GRRI013P") == \
            {"pack_count": 1, "each_qty": 40, "each_unit": "lb"}
        assert im.parse_pack("LEE KUM KEE HOISIN SAUCE 6X5LBS-P") == {"pack_count": 6, "each_qty": 5, "each_unit": "lb"}
        assert im.parse_pack("Burrata 125g x 6") == {"pack_count": 6, "each_qty": 125, "each_unit": "g"}
        assert im.parse_pack("Tomatoes 6/5LB") == {"pack_count": 6, "each_qty": 5, "each_unit": "lb"}
        assert im.parse_pack("Napkins 500ct") == {"pack_count": 500, "each_qty": None, "each_unit": None}
        assert im.parse_pack("ANGEL RICE GRNO005A", "BAG") is None  # an item code isn't a size
        assert im.clean_name("Peroni Nastro Azzurro 330ml") == "Peroni Nastro Azzurro"

    def test_what_one_invoice_unit_adds_to_stock(self):
        # 1 case of 40 × 400 g: 16 kg, 40 packs, or 1 case, depending on what it's counted in.
        assert im.per_invoice_unit("case", 40, 400, "g", "kg") == 16
        assert im.per_invoice_unit("case", 40, 400, "g", "pack") == 40
        assert im.per_invoice_unit("case", 40, 400, "g", "case") == 1
        assert im.per_invoice_unit("bag", 1, 40, "lb", "lb") == 40
        assert round(im.per_invoice_unit("bag", 1, 40, "lb", "kg"), 2) == 18.14
        # Already sold by weight: 10 LBS packed in 1 lb bags is 1 lb per invoice unit.
        assert im.per_invoice_unit("lbs", 1, 1, "lb", "lb") == 1
        assert im.per_invoice_unit("lbs", 1, 1, "lb", "bag") == 1
        # A case of 330 ml bottles with no count printed can't be worked out, unless the item knows its case.
        assert im.per_invoice_unit("case", 1, 330, "ml", "each") is None
        assert im.per_invoice_unit("case", None, None, None, "each",
                                   [{"qty": 1, "unit": "case", "to_qty": 24, "to_unit": "each"}]) == 24
        # New items: food by weight, drinks by the bottle, else the invoice unit.
        assert im.default_stock_unit("case", 40, 400, "g") == "kg"
        assert im.default_stock_unit("bag", 1, 40, "lb") == "lb"
        assert im.default_stock_unit("case", 24, 330, "ml", "bottle") == "bottle"
        assert im.default_stock_unit("case", 1, 330, "ml") == "each"
        assert im.default_stock_unit("case", None, None, None) == "case"
        assert im.stock_units("case", 40, 400, "g")[:3] == ["kg", "g", "lb"]
        assert im.new_item_conversions("case", 16, "kg", 400, "g") == [{"qty": 1, "unit": "case", "to_qty": 16,
                                                                         "to_unit": "kg"}]
        assert im.new_item_conversions("case", 24, "bottle", 330, "ml") == [
            {"qty": 1, "unit": "case", "to_qty": 24, "to_unit": "bottle"},
            {"qty": 1, "unit": "bottle", "to_qty": 330, "to_unit": "ml"}]

    def test_price_change_compares_per_stock_unit(self):
        # A case of 24 at $52.80 is $2.20 a bottle; last time singles were $2.00: +10%.
        assert im.price_change_pct(5280, 24, 200, 1) == 10.0
        assert im.price_change_pct(750, 1, 750, 1) == 0.0
        assert im.price_change_pct(750, 1, None, 1) is None
        assert im.product_key("Harbour Dairy", "Burrata 125g", "BR-125") == "harbour dairy|sku:br 125"
        assert im.weekly_impact_cents(90, 24) == 270  # +90c on 24 units over 8 weeks = $2.70 a week


async def test_scan_review_confirm_then_price_change_alert(client, session, demo_location_id, fake_llm):
    burrata_id, stock_before = await _stock_item(session, demo_location_id, "Burrata", "ea"), 4

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
    llm = fake_llm([tool_result("get_price_changes", {"days": 365}), text_result("Burrata went up 12%.")])
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
    await _stock_item(session, demo_location_id, "Peroni", "bottle")
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


async def test_import_adds_new_stock_items_and_exports_excel(client, session, demo_location_id, fake_llm):
    """Unmatched lines marked "new" become stock items (kept off the menu) with the delivered amount."""
    import io

    from openpyxl import load_workbook

    await _stock_item(session, demo_location_id, "Burrata", "ea")
    scan = (await _scan_and_read(client, demo_location_id, fake_llm, DAIRY_1))["scan"]
    base = f"/locations/{demo_location_id}/invoices/{scan['id']}"
    lines = [{k: v for k, v in ln.items() if k not in ("last_price_cents", "last_received_on", "change_pct")}
             for ln in scan["lines"]]
    for ln in lines:
        if ln["description"] == "Mascarpone 500g":
            ln["create_item"] = True
            ln["pack_size"] = 1
    assert (await client.put(base, headers=OWNER, json={"lines": lines})).status_code == 200

    xlsx = await client.get(f"{base}/export.xlsx", headers=OWNER)
    assert xlsx.status_code == 200 and "Harbour-Dairy" in xlsx.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(xlsx.content)).active
    rows = [[c.value for c in row] for row in ws.iter_rows()]
    assert rows[0][:2] == ["Supplier", "Harbour Dairy & Deli (demo)"] and rows[1][1] == "HD-2041"
    header = rows.index(next(r for r in rows if r[0] == "Item on invoice"))
    body = {r[0]: r for r in rows[header + 1:] if r[0]}
    assert body["Mascarpone 500g"][3:8] == [2, "tub", 10.5, 21.0, "New stock item"]
    assert body["Burrata 125g x 6"][7] == "Burrata" and body["Delivery fee"][2] == "fee"

    r = (await client.post(f"{base}/confirm", headers=OWNER)).json()
    # Named without the size, counted by weight: 2 tubs of 500 g are 1 kg, at $21.00 a kg.
    assert r["created_items"] == ["Mascarpone"]
    item = await session.scalar(select(Item).where(Item.location_id == demo_location_id, Item.name == "Mascarpone"))
    assert item.stock_qty == 1 and item.unit == "kg" and item.cost_cents == 2100 and item.orderable is False
    assert item.conversions == [{"qty": 1, "unit": "tub", "to_qty": 0.5, "to_unit": "kg"}]
    assert item.supplier_id == scan["supplier_id"] and item.category == "Supplies"
    logged = await session.scalar(select(ReceivedLine).where(ReceivedLine.scan_id == scan["id"],
                                                             ReceivedLine.description == "Mascarpone 500g"))
    assert logged.item_id == item.id and logged.stock_added == 1 and logged.pack_size == 0.5
    after = (await client.get(base, headers=OWNER)).json()
    assert next(ln for ln in after["lines"] if ln["description"] == "Mascarpone 500g")["item_id"] == item.id


THREE_DOLPHINS = dict(
    supplier_name="Three Dolphins Wholesale Ltd.", invoice_number="52951", invoice_date="2026-11-20", total=383.80,
    lines=[
        {"description": "ANGEL RICE VERMICELLI BBH (M) 40X400G BUN BO HUE GRNO005A", "name": "ANGEL RICE VERMICELLI BBH",
         "sku": "GRNO005A", "qty": 1, "unit": "CASE", "packing": "40X400G", "pack_count": 40, "each_size": 400,
         "each_unit": "g", "unit_price": 60.0, "line_total": 60.0},
        {"description": "GOLDEN CAMEL RICE/ GAO LAC DA 40LBS GRRI013P", "name": "GOLDEN CAMEL RICE", "sku": "GRRI013P",
         "qty": 2, "unit": "BAG", "packing": "BAG", "unit_price": 52.0, "line_total": 104.0},  # size only in the name
        {"description": "BLANCHED PEANUT DAU PHONG KHONG VO 1LB GRDR012P", "name": "BLANCHED PEANUT",
         "sku": "GRDR012P", "qty": 10, "unit": "LBS", "packing": "1LB", "unit_price": 2.78, "line_total": 27.80},
    ],
)


async def test_cases_become_kilos_new_items_get_clean_names(client, session, demo_location_id, fake_llm):
    """1 case of 40 × 400 g vermicelli at $60 goes in as 16 kg at $3.75/kg, under a clean name."""
    scan = (await _scan_and_read(client, demo_location_id, fake_llm, THREE_DOLPHINS))["scan"]
    lines = {ln["name"]: ln for ln in scan["lines"]}
    noodles, rice, peanut = (lines["ANGEL RICE VERMICELLI BBH"], lines["GOLDEN CAMEL RICE"],
                             lines["BLANCHED PEANUT"])
    assert (noodles["pack_count"], noodles["each_qty"], noodles["each_unit"]) == (40, 400, "g")
    assert noodles["stock_unit"] == "kg" and noodles["pack_size"] == 16 and noodles["pack_auto"] is True
    assert (rice["each_qty"], rice["each_unit"], rice["stock_unit"], rice["pack_size"]) == (40, "lb", "lb", 40)
    assert peanut["stock_unit"] == "lb"
    assert peanut["pack_size"] == 1

    # Counting the noodles by the pack instead: 40 per case.
    base = f"/locations/{demo_location_id}/invoices/{scan['id']}"
    edited = [{k: v for k, v in ln.items() if k not in ("last_price_cents", "last_received_on", "change_pct",
                                                        "pack_auto")} | {"create_item": True} for ln in scan["lines"]]
    edited[0]["stock_unit"] = "pack"
    r = await client.put(base, headers=OWNER, json={"lines": edited})
    assert r.status_code == 200 and r.json()["lines"][0]["pack_size"] == 40
    edited[0]["stock_unit"] = "kg"
    assert (await client.put(base, headers=OWNER, json={"lines": edited})).json()["lines"][0]["pack_size"] == 16

    out = (await client.post(f"{base}/confirm", headers=OWNER)).json()
    assert sorted(out["created_items"]) == ["ANGEL RICE VERMICELLI BBH", "BLANCHED PEANUT", "GOLDEN CAMEL RICE"]
    item = await session.scalar(select(Item).where(Item.location_id == demo_location_id,
                                                   Item.name == "ANGEL RICE VERMICELLI BBH"))
    assert item.unit == "kg" and item.stock_qty == 16 and item.cost_cents == 375
    assert item.conversions == [{"qty": 1, "unit": "case", "to_qty": 16, "to_unit": "kg"}]
    rice_item = await session.scalar(select(Item).where(Item.location_id == demo_location_id,
                                                        Item.name == "GOLDEN CAMEL RICE"))
    assert rice_item.unit == "lb" and rice_item.stock_qty == 80 and rice_item.cost_cents == 130
    logged = await session.scalar(select(ReceivedLine).where(ReceivedLine.item_id == item.id))
    assert logged.description.startswith("ANGEL RICE VERMICELLI BBH (M) 40X400G") and logged.stock_added == 16

    # Next time the same product (by its code) goes straight to the item, 16 kg a case.
    later = {**THREE_DOLPHINS, "invoice_number": "53010", "lines": THREE_DOLPHINS["lines"][:1]}
    again = (await _scan_and_read(client, demo_location_id, fake_llm, later))["scan"]["lines"][0]
    assert again["item_id"] == item.id and again["match"] == "remembered" and again["pack_size"] == 16


async def test_lines_match_inventory_items_never_menu_dishes(client, session, demo_location_id, fake_llm):
    """Burrata on an invoice is the Burrata you stock, not the Burrata & Tomato starter; unknown products are new."""
    fields = {**DAIRY_1, "invoice_number": "HD-3001"}
    scan = (await _scan_and_read(client, demo_location_id, fake_llm, fields))["scan"]
    lines = {ln["description"]: ln for ln in scan["lines"]}
    assert lines["Burrata 125g x 6"]["item_id"] is None  # only the dish exists: not a match

    stocked = await _stock_item(session, demo_location_id, "Burrata", "ea")
    scan = (await _scan_and_read(client, demo_location_id, fake_llm, {**fields, "invoice_number": "HD-3002"}))["scan"]
    lines = {ln["description"]: ln for ln in scan["lines"]}
    assert lines["Burrata 125g x 6"]["item_id"] == stocked and lines["Burrata 125g x 6"]["item_name"] == "Burrata"
    assert lines["Mascarpone 500g"]["item_id"] is None


async def test_typed_name_and_amount_win(client, session, demo_location_id, fake_llm):
    """A name typed in Matched inventory names the new item; an amount typed for 1 case isn't worked out again."""
    scan = (await _scan_and_read(client, demo_location_id, fake_llm, {**THREE_DOLPHINS, "invoice_number": "53100",
                                                                       "lines": THREE_DOLPHINS["lines"][:1]}))["scan"]
    base = f"/locations/{demo_location_id}/invoices/{scan['id']}"
    line = {k: v for k, v in scan["lines"][0].items() if k not in ("last_price_cents", "last_received_on", "change_pct")}
    line |= {"create_item": True, "match": "new", "new_name": "Bun bo Hue noodles", "pack_size": 15, "pack_manual": True}
    saved = (await client.put(base, headers=OWNER, json={"lines": [line]})).json()["lines"][0]
    assert saved["pack_size"] == 15 and saved["item_id"] is None  # not re-matched, not recalculated
    out = (await client.post(f"{base}/confirm", headers=OWNER)).json()
    assert out["created_items"] == ["Bun bo Hue noodles"]
    item = await session.scalar(select(Item).where(Item.location_id == demo_location_id,
                                                   Item.name == "Bun bo Hue noodles"))
    assert item.stock_qty == 15 and item.unit == "kg" and item.cost_cents == 400
