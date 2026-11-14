"""Inventory: the ledger and weighted-average cost, recipes and plate cost (the PRD's salmon example), sales
taking ingredients out of stock, counts, waste, receiving shorts, and COGS."""

from datetime import timedelta

from sqlalchemy import select

from app.models import (Item, ItemSalesDaily, Location, OrgMember, ReceivedLine, RecipeLine,
                        StockMovement, User)
from app.services import inventory as inv
from app.services.jobs import run_pending
from tests.conftest import OWNER

STAFF = {"Authorization": "Bearer dev:cook@example.com"}

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def test_units_convert_within_mass_volume_and_count():
    assert inv.convert(8, "oz", "lb") == 0.5
    assert round(inv.convert(1, "kg", "lb"), 4) == 2.2046
    assert round(inv.convert(2, "tbsp", "ml"), 2) == 29.57
    assert inv.convert(1, "dozen", "ea") == 12
    assert inv.convert(3, "Lbs.", "pound") == 3
    assert inv.convert(100, "g", "each") is None  # grams don't turn into pieces
    # An item's own conversions bridge kinds: a case of 40 lb, and flour's density (1 cup = 4.25 oz).
    case = [{"qty": 1, "unit": "case", "to_qty": 40, "to_unit": "lb"}]
    assert inv.convert(2, "case", "lb", case) == 80 and inv.convert(8, "oz", "case", case) == 0.0125
    flour = [{"qty": 1, "unit": "cup", "to_qty": 4.25, "to_unit": "oz"}]
    assert round(inv.convert(2, "cup", "lb", flour), 4) == 0.5312
    assert round(inv.convert(250, "ml", "g", flour), 1) == round(250 / 236.5882365 * 4.25 * 28.349523125, 1)


def test_prd_salmon_example_plate_cost_and_pricing():
    """8 oz salmon bought at $12.00/lb with 80% yield costs $7.50; a $10.27 plate at $32 is 32.1%, and hitting
    30% means charging $34.23."""
    salmon = Item(name="Salmon", unit="lb", avg_cost=1200.0)
    line = RecipeLine(qty=8, unit="oz", yield_pct=80)
    costed = inv.cost_line(line, salmon)
    assert costed.problem is None and round(costed.cost_cents, 2) == 750.0
    assert round(costed.stock_qty, 4) == 0.625  # taken from stock per plate: EP portion ÷ yield
    p = inv.pricing(3200, 1027, 30)
    assert p["food_cost_pct"] == 32.1 and p["suggested_price_cents"] == 3423 and p["margin_cents"] == 2173
    bad = inv.cost_line(RecipeLine(qty=2, unit="g", yield_pct=100), Item(name="Eggs", unit="each", avg_cost=40.0))
    assert bad.problem and "each" in bad.problem


async def test_weighted_average_and_the_ledger_adds_up(session, demo_location_id):
    loc = await session.get(Location, demo_location_id)
    item = Item(location_id=loc.id, provider="pass", external_id="pass-t1", name="Butter", unit="lb",
                stock_qty=10, cost_cents=1000, price_cents=0)
    session.add(item)
    await session.flush()
    await inv.record(session, item, "receive", 10, unit_cost=1200)  # 10 at $10 + 10 at $12
    assert item.stock_qty == 20 and item.avg_cost == 1100 and item.cost_cents == 1100
    out = await inv.record(session, item, "waste", -5, reason="spoiled")
    assert out.unit_cost == 1100 and out.value_cents == -5500
    # Below zero is allowed (a sale before its invoice), flagged as negative; the next delivery sets the cost.
    await inv.record(session, item, "sale", -17)
    assert item.stock_qty == -2 and inv.status(item) == "negative"
    item.par_level, item.low_stock_threshold = 40, 15  # par and reorder point
    for q, want in ((0, "out"), (15, "below_par"), (30, "at_par"), (45, "approaching"), (60, "ok")):
        assert inv.status(Item(stock_qty=q, par_level=40, low_stock_threshold=15)) == want
    await inv.record(session, item, "receive", 10, unit_cost=1300)
    assert item.stock_qty == 8 and item.avg_cost == 1300
    await session.flush()
    rows = list(await session.scalars(select(StockMovement).where(StockMovement.item_id == item.id)))
    assert [r.kind for r in rows][0] == "opening"
    assert sum(r.value_cents for r in rows) == round(item.stock_qty * item.avg_cost)  # value = qty × average
    assert sum(r.qty_delta for r in rows) == item.stock_qty


async def test_recipe_sales_count_waste_and_cogs(client, session, demo_location_id):
    base = f"/locations/{demo_location_id}"
    assert (await client.post(f"{base}/storage-areas/defaults", headers=OWNER)).status_code == 200
    on_hand = (await client.get(f"{base}/inventory/on-hand", headers=OWNER)).json()
    walk_in = next(a for a in on_hand["areas"] if a["name"] == "Walk-in cooler")
    assert on_hand["totals"]["items"] > 0 and not on_hand["suggested_areas"]

    r = await client.post(f"{base}/inventory/items", headers=OWNER, json={
        "name": "Salmon fillet", "unit": "lbs", "unit_cost": 1200, "qty": 20, "par_level": 30, "reorder_point": 5,
        "storage_area_id": walk_in["id"]})
    assert r.status_code == 200, r.text
    salmon_id = r.json()["id"]
    salmon = await session.get(Item, salmon_id)
    assert salmon.unit == "lb" and salmon.stock_qty == 20 and salmon.orderable is False
    assert (await client.post(f"{base}/inventory/items", headers=OWNER,
                              json={"name": "salmon FILLET"})).status_code == 409

    menu = (await client.post(f"{base}/items", headers=OWNER, json={"name": "Salmon plate", "price_cents": 3200})).json()
    bad = await client.put(f"{base}/recipes/{menu['id']}", headers=OWNER, json={
        "lines": [{"ingredient_id": salmon_id, "qty": 2, "unit": "each"}]})
    assert bad.status_code == 422 and "lb" in bad.json()["detail"]
    r = await client.put(f"{base}/recipes/{menu['id']}", headers=OWNER, json={
        "lines": [{"ingredient_id": salmon_id, "qty": 8, "unit": "oz", "yield_pct": 80}], "q_factor_cents": 50})
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["lines"][0]["cost_cents"] == 750 and detail["plate_cost_cents"] == 800
    assert detail["food_cost_pct"] == 25.0 and detail["suggested_price_cents"] == 2667 and detail["complete"]
    assert not detail["over_target"] and detail["history"][0]["plate_cost_cents"] == 800
    recipes = (await client.get(f"{base}/recipes", headers=OWNER)).json()
    row = next(m for m in recipes["menu"] if m["id"] == menu["id"])
    assert row["has_recipe"] and row["plate_cost_cents"] == 800
    assert all(not t["has_recipe"] for t in recipes["todo"]) and recipes["todo"]  # best sellers without recipes

    # Four sold today take 4 × 0.625 lb out; selling fewer after a void puts the difference back, as a new entry.
    loc = await session.get(Location, demo_location_id)
    today = inv.local_today(loc)
    session.add(ItemSalesDaily(location_id=loc.id, day=today, name="Salmon plate", qty=4, revenue_cents=12800))
    await session.commit()
    await inv.deplete_sales(session, loc, {today})
    await session.commit()
    session.expire_all()
    assert (await session.get(Item, salmon_id)).stock_qty == 17.5
    loc = await session.get(Location, demo_location_id)
    await inv.deplete_sales(session, loc, {today})  # run again: nothing more
    row = await session.get(ItemSalesDaily, (loc.id, today, "Salmon plate"))
    row.qty = 2
    await inv.deplete_sales(session, loc, {today})
    await session.commit()
    session.expire_all()
    assert (await session.get(Item, salmon_id)).stock_qty == 18.75
    sales = list(await session.scalars(select(StockMovement).where(StockMovement.item_id == salmon_id,
                                                                   StockMovement.kind == "sale")))
    assert [s.qty_delta for s in sales] == [-2.5, 1.25]
    # Yesterday is before the recipe existed: its sales don't touch stock.
    loc = await session.get(Location, demo_location_id)
    session.add(ItemSalesDaily(location_id=loc.id, day=today - timedelta(days=1), name="Salmon plate", qty=9))
    await session.commit()
    assert await inv.deplete_sales(session, loc, {today - timedelta(days=1)}) == 0

    # Waste in the recipe's unit; the same entry synced twice (offline phone) is kept once.
    waste = {"item_id": salmon_id, "qty": 8, "unit": "oz", "reason": "spoiled", "client_id": "waste-abc123"}
    r = await client.post(f"{base}/waste", headers=OWNER, json=waste)
    assert r.status_code == 200 and r.json()["qty_delta"] == -0.5 and r.json()["value_cents"] == -600
    assert (await client.post(f"{base}/waste", headers=OWNER, json=waste)).json()["id"] == r.json()["id"]
    assert (await client.post(f"{base}/waste", headers=OWNER, json={**waste, "client_id": None,
                                                                   "reason": "bored"})).status_code == 422
    w = (await client.get(f"{base}/waste", headers=OWNER)).json()
    assert w["total_cents"] == 600 and w["by_reason"][0]["label"] == "Spoiled"

    # A count of the walk-in: 18.25 expected, 18 found. Sent twice, kept once.
    count = {"client_id": "count-xyz789", "storage_area_id": walk_in["id"], "approve": True,
             "lines": [{"item_id": salmon_id, "qty": 18}]}
    r = await client.post(f"{base}/stock-counts", headers=OWNER, json=count)
    assert r.status_code == 200, r.text
    c = r.json()
    assert c["area"] == "Walk-in cooler" and c["lines"][0]["expected"] == 18.25 and c["variance_cents"] == -300
    assert c["status"] == "approved" and c["approved_by"] == "Demo Owner"
    assert (await client.post(f"{base}/stock-counts", headers=OWNER, json=count)).json()["id"] == c["id"]
    assert len((await client.get(f"{base}/stock-counts", headers=OWNER)).json()["counts"]) == 1

    # A manual correction, then the ledger shows everything with who did it.
    r = await client.post(f"{base}/stock-adjustments", headers=OWNER,
                          json={"item_id": salmon_id, "qty_delta": 2, "reason": "found", "note": "Back of the freezer"})
    assert r.json()["stock_qty"] == 20
    led = (await client.get(f"{base}/stock-movements?item_id={salmon_id}", headers=OWNER)).json()["movements"]
    assert [m["kind"] for m in led] == ["adjust", "count", "waste", "sale", "sale", "opening"]
    assert led[0]["user"] == "Demo Owner"

    # COGS: opening + received − closing, which is what the sales, waste and counts used.
    cogs = (await client.get(f"{base}/inventory/cogs?start={today}&end={today}", headers=OWNER)).json()
    assert cogs["cogs_cents"] == cogs["opening_cents"] + cogs["received_cents"] - cogs["closing_cents"]
    used = -sum(m.value_cents for m in await session.scalars(select(StockMovement).where(
        StockMovement.location_id == loc.id, StockMovement.kind.not_in(inv.ADDED),
        StockMovement.created_at >= inv.datetime.combine(today, inv.time(0), tzinfo=inv.ZoneInfo(loc.timezone)))))
    assert cogs["cogs_cents"] == used
    v = next(x for x in cogs["variance"] if x["item_id"] == salmon_id)
    assert v["theoretical_qty"] == 1.25 and v["variance_qty"] == 0.5 - 2 + 0.25 and v["waste_cents"] == 600

    # Changing the cost by hand is an entry of its own, not usage.
    assert (await client.put(f"{base}/inventory/items/{salmon_id}", headers=OWNER,
                             json={"unit_cost": 1300})).status_code == 200
    after = (await client.get(f"{base}/inventory/cogs?start={today}&end={today}", headers=OWNER)).json()
    assert after["cogs_cents"] == cogs["cogs_cents"]
    on_hand = (await client.get(f"{base}/inventory/on-hand", headers=OWNER)).json()
    s = next(i for i in on_hand["items"] if i["id"] == salmon_id)
    assert s["value_cents"] == 26000 and s["used_in"] == 1 and s["status"] == "at_par"
    assert s["last_count"]["qty"] == 18 and s["days_of_cover"] is not None
    audit = (await client.get(f"{base}/inventory/audit?item_id={menu['id']}", headers=OWNER)).json()["changes"]
    assert audit[0]["field"] in ("recipe", "q_factor") and any("8 oz Salmon fillet @ 80%" in (a["new"] or "")
                                                               for a in audit)


async def test_receiving_short_and_damaged_lines(client, session, demo_location_id):
    """Stock and cost follow what actually arrived; the issue is kept in the receiving log."""
    base = f"/locations/{demo_location_id}"
    item = (await client.post(f"{base}/inventory/items", headers=OWNER, json={
        "name": "Heirloom tomatoes", "unit": "kg", "unit_cost": 400, "qty": 5})).json()
    r = await client.post(f"{base}/invoices", headers=OWNER, files={"file": ("inv.png", PNG, "image/png")})
    await run_pending(time_budget_s=20)
    scan_id = r.json()["id"]
    r = await client.put(f"{base}/invoices/{scan_id}", headers=OWNER, json={
        "supplier_name": "Coastal Produce Co. (demo)", "invoice_number": "CP-77", "invoice_date": "2026-11-01",
        "lines": [{"description": "Heirloom tomatoes", "qty": 10, "unit": "kg", "unit_price_cents": 520,
                   "item_id": item["id"], "received_qty": 7, "issue": "short"}]})
    assert r.status_code == 200, r.text
    r = await client.post(f"{base}/invoices/{scan_id}/confirm", headers=OWNER)
    assert r.status_code == 200, r.text
    assert r.json()["issues"] == [{"description": "Heirloom tomatoes", "issue": "short", "invoiced": 10, "received": 7}]
    session.expire_all()
    tomatoes = await session.get(Item, item["id"])
    assert tomatoes.stock_qty == 12 and round(tomatoes.avg_cost, 2) == round((5 * 400 + 7 * 520) / 12, 2)
    line = await session.scalar(select(ReceivedLine).where(ReceivedLine.invoice_number == "CP-77"))
    assert line.received_qty == 7 and line.issue == "short" and line.stock_added == 7
    mv = await session.scalar(select(StockMovement).where(StockMovement.item_id == item["id"],
                                                          StockMovement.kind == "receive"))
    assert mv.source == f"invoice:{scan_id}" and mv.reason == "short" and "Short" in mv.note
    log = (await client.get(f"{base}/receiving-log", headers=OWNER)).json()
    assert any(x["issue"] == "short" and x["received_qty"] == 7 for x in log["lines"])


async def test_every_stock_change_is_in_the_ledger(client, session, demo_location_id):
    """Editing stock by hand and deleting an item write entries too, so the ledger always adds up."""
    base = f"/locations/{demo_location_id}"
    item = (await client.post(f"{base}/items", headers=OWNER, json={"name": "Lemonade", "price_cents": 500,
                                                                    "stock_qty": 12})).json()
    assert (await client.put(f"{base}/items/{item['id']}/details", headers=OWNER,
                             json={"stock_qty": 9})).status_code == 200
    kinds = list(await session.scalars(select(StockMovement.kind).where(StockMovement.item_id == item["id"])
                                       .order_by(StockMovement.created_at)))
    assert kinds == ["adjust", "adjust"]
    assert (await client.post(f"{base}/items/delete", headers=OWNER, json={"ids": [item["id"]]})).status_code == 200
    session.expire_all()
    rows = list(await session.scalars(select(StockMovement).where(StockMovement.item_name == "Lemonade")))
    assert sum(r.qty_delta for r in rows) == 0 and all(r.item_id is None for r in rows)  # history kept
    assert rows[-1].reason == "deleted" or any(r.reason == "deleted" for r in rows)


async def _add_staff(session, location_id: str) -> None:
    loc = await session.get(Location, location_id)
    session.add(User(id="dev-cook@example.com", email="cook@example.com"))
    await session.flush()  # Postgres checks the member's user exists
    session.add(OrgMember(org_id=loc.org_id, user_id="dev-cook@example.com", role="staff"))
    await session.commit()


async def test_staff_count_needs_approval_and_sees_no_costs(client, session, demo_location_id):
    """Line staff count and log waste; a manager approves the count, and only then does stock change. Staff never
    see costs. Each line moves by counted − the amount frozen when the count started."""
    base = f"/locations/{demo_location_id}"
    await _add_staff(session, demo_location_id)
    item = (await client.post(f"{base}/inventory/items", headers=OWNER, json={
        "name": "Butter", "unit": "kg", "unit_cost": 900, "qty": 10})).json()
    on_hand = (await client.get(f"{base}/inventory/on-hand", headers=STAFF)).json()
    butter = next(i for i in on_hand["items"] if i["id"] == item["id"])
    assert butter["avg_cost"] is None and on_hand["totals"]["value_cents"] is None and not on_hand["can_manage"]
    assert (await client.get(f"{base}/recipes", headers=STAFF)).status_code == 403
    assert (await client.post(f"{base}/stock-adjustments", headers=STAFF,
                              json={"item_id": item["id"], "qty_delta": 1, "reason": "found"})).status_code == 403

    r = await client.post(f"{base}/stock-counts", headers=STAFF, json={
        "client_id": "count-staff-1", "approve": True, "lines": [{"item_id": item["id"], "qty": 4, "expected": 10}]})
    c = r.json()
    assert r.status_code == 200 and c["status"] == "submitted" and c["variance_cents"] is None  # staff can't approve
    assert c["lines"][0]["big"] and "value_cents" not in c["lines"][0]
    session.expire_all()
    assert (await session.get(Item, item["id"])).stock_qty == 10  # nothing changes until approved
    # Meanwhile 1 kg was thrown out: approval moves stock by counted − frozen (−6), so the waste still counts.
    await client.post(f"{base}/waste", headers=STAFF, json={"item_id": item["id"], "qty": 1, "reason": "spoiled"})
    assert (await client.post(f"{base}/stock-counts/{c['id']}/approve", headers=STAFF)).status_code == 403
    r = await client.post(f"{base}/stock-counts/{c['id']}/approve", headers=OWNER)
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["variance_cents"] == -5400
    session.expire_all()
    assert (await session.get(Item, item["id"])).stock_qty == 3
    assert (await client.post(f"{base}/stock-counts/{c['id']}/approve", headers=OWNER)).status_code == 409
    # A wrong count can be thrown away.
    r = await client.post(f"{base}/stock-counts", headers=STAFF, json={
        "client_id": "count-staff-2", "lines": [{"item_id": item["id"], "qty": 99}]})
    assert (await client.post(f"{base}/stock-counts/{r.json()['id']}/reject", headers=OWNER)).status_code == 200
    session.expire_all()
    assert (await session.get(Item, item["id"])).stock_qty == 3
    counts = (await client.get(f"{base}/stock-counts", headers=OWNER)).json()
    assert [x["status"] for x in counts["counts"]] == ["rejected", "approved"] and counts["can_approve"]


async def test_prd_grilled_salmon_with_a_sub_recipe(client, session, demo_location_id):
    """The PRD's worked example, sub-recipe included: $10.27 a plate, 32.1% of $32, $34.23 to hit 30%. A sale takes
    the sauce's own ingredients out of stock; making a batch instead keeps the sauce as stock of its own."""
    base = f"/locations/{demo_location_id}"

    async def add(**kw):
        r = await client.post(f"{base}/inventory/items", headers=OWNER, json=kw)
        assert r.status_code == 200, r.text
        return r.json()["id"]

    salmon = await add(name="Salmon side", unit="lb", unit_cost=1200, qty=50, default_yield_pct=80)
    asparagus = await add(name="Asparagus", unit="lb", unit_cost=400, qty=20)
    rice = await add(name="Jasmine rice", unit="lb", unit_cost=160, qty=40)
    butter = await add(name="Unsalted butter", unit="lb", unit_cost=640, qty=10)
    # Lemon butter sauce: a batch of 32 oz costs $12.80 → $0.40 an oz.
    sauce = await add(name="Lemon butter sauce", unit="oz", batch_qty=32)
    r = await client.put(f"{base}/recipes/{sauce}", headers=OWNER, json={
        "lines": [{"ingredient_id": butter, "qty": 2, "unit": "lb"}]})
    assert r.status_code == 200 and r.json()["unit_cost"] == 40.0 and r.json()["is_prep"]
    dish = (await client.post(f"{base}/items", headers=OWNER,
                              json={"name": "PRD grilled salmon", "price_cents": 3200})).json()
    r = await client.put(f"{base}/recipes/{dish['id']}", headers=OWNER, json={
        "q_factor_cents": 30, "recipe_notes": "Skin side down, 4 minutes.", "lines": [
            {"ingredient_id": salmon, "qty": 8, "unit": "oz", "yield_pct": 80},
            {"ingredient_id": asparagus, "qty": 4, "unit": "oz", "yield_pct": 55},
            {"ingredient_id": rice, "qty": 2.5, "unit": "oz"},
            {"ingredient_id": sauce, "qty": 1, "unit": "oz"}]})
    d = r.json()
    assert [round(ln["cost_cents"]) for ln in d["lines"]] == [750, 182, 25, 40]
    assert d["plate_cost_cents"] == 1027 and d["food_cost_pct"] == 32.1 and d["suggested_price_cents"] == 3423
    assert d["margin_cents"] == 2173 and d["over_target"] and d["recipe_notes"].startswith("Skin")
    # A loop is refused: the sauce can't contain the dish that contains it.
    assert (await client.put(f"{base}/recipes/{sauce}", headers=OWNER, json={
        "lines": [{"ingredient_id": dish["id"], "qty": 1, "unit": "each"}]})).status_code == 422

    # Selling 2: salmon 2 × 0.625 lb, asparagus 2 × 7.27 oz, and the sauce broken down to its butter (2 oz → 1/16 batch).
    loc = await session.get(Location, demo_location_id)
    today = inv.local_today(loc)
    session.add(ItemSalesDaily(location_id=loc.id, day=today, name="PRD grilled salmon", qty=2, revenue_cents=6400))
    await session.commit()
    await inv.deplete_sales(session, loc, {today})
    await session.commit()
    session.expire_all()
    assert (await session.get(Item, salmon)).stock_qty == 48.75
    assert round((await session.get(Item, butter)).stock_qty, 4) == round(10 - 2 * (1 / 32) * 2, 4)
    assert (await session.get(Item, sauce)).stock_qty is None

    # A price rise flows into the plate: salmon at $13.50/lb makes the plate $11.21 (35.0%) and it's flagged.
    assert (await client.put(f"{base}/inventory/items/{salmon}", headers=OWNER,
                             json={"unit_cost": 1350})).status_code == 200
    report = (await client.get(f"{base}/recipes", headers=OWNER)).json()
    row = next(m for m in report["menu"] if m["id"] == dish["id"])
    assert row["plate_cost_cents"] == 1121 and row["food_cost_pct"] == 35.0 and row["over_target"]
    assert row["previous_cost_cents"] == 1027
    assert next(p for p in report["prep"] if p["id"] == sauce)["unit_cost"] == 40.0

    # Making 2 batches: 4 lb of butter out, 64 oz of sauce in at $25.60.
    r = await client.post(f"{base}/inventory/prep/{sauce}/make", headers=OWNER, json={"batches": 2})
    assert r.status_code == 200 and r.json()["made"] == 64 and r.json()["cost_cents"] == 2560
    session.expire_all()
    assert (await session.get(Item, sauce)).stock_qty == 64
    beverage = (await client.post(f"{base}/items", headers=OWNER, json={"name": "House lager", "price_cents": 800,
                                                                       "category": "Beer"})).json()
    r = await client.get(f"{base}/recipes/{beverage['id']}", headers=OWNER)
    assert r.json()["target_food_cost_pct"] == 22.0  # drinks have their own target


async def test_case_conversion_last_price_and_credits(client, session, demo_location_id):
    """An invoice in cases becomes lb through the item's conversion; with the latest-price method a delivery sets
    the cost outright; a short line opens a credit until someone resolves it; a wild cost change is flagged."""
    base = f"/locations/{demo_location_id}"
    assert (await client.put(f"{base}/inventory/settings", headers=OWNER,
                             json={"cost_method": "last"})).status_code == 200
    chicken = (await client.post(f"{base}/inventory/items", headers=OWNER, json={
        "name": "Chicken breast", "unit": "lb", "unit_cost": 300, "qty": 40,
        "conversions": [{"qty": 1, "unit": "case", "to_qty": 40, "to_unit": "lb"}]})).json()["id"]
    r = await client.post(f"{base}/invoices", headers=OWNER, files={"file": ("inv.png", PNG, "image/png")})
    await run_pending(time_budget_s=20)
    scan_id = r.json()["id"]
    await client.put(f"{base}/invoices/{scan_id}", headers=OWNER, json={
        "supplier_name": "Coastal Produce Co. (demo)", "invoice_number": "CP-88", "invoice_date": "2026-11-02",
        "lines": [{"description": "Chicken breast 40 lb", "qty": 3, "unit": "case", "unit_price_cents": 26000,
                   "item_id": chicken, "received_qty": 2, "issue": "damaged"}]})
    r = await client.post(f"{base}/invoices/{scan_id}/confirm", headers=OWNER)
    assert r.status_code == 200, r.text
    assert r.json()["cost_warnings"] and "Chicken breast" in r.json()["cost_warnings"][0]  # $6.50/lb vs $3.00
    session.expire_all()
    item = await session.get(Item, chicken)
    assert item.stock_qty == 120 and item.avg_cost == 650  # 2 cases × 40 lb; latest price $260 / 40 lb
    credits = (await client.get(f"{base}/credits", headers=OWNER)).json()
    assert credits["open_cents"] == 26000 and credits["credits"][0]["issue"] == "damaged"
    line_id = credits["credits"][0]["id"]
    assert (await client.post(f"{base}/credits/{line_id}/resolve", headers=OWNER,
                              json={"note": "Credit note CN-12"})).status_code == 200
    assert (await client.get(f"{base}/credits", headers=OWNER)).json()["credits"] == []
    log = (await client.get(f"{base}/receiving-log?issue=any", headers=OWNER)).json()
    assert len(log["lines"]) == 1 and log["lines"][0]["credit_status"] == "resolved"
