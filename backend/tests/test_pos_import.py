"""Importing Clover's Items and Orders exports, for restaurants that don't connect Clover live."""

import io
import json
from datetime import date

from openpyxl import Workbook
from sqlalchemy import func, select

from app.models import DailySales, Item, ItemSalesDaily, Order
from tests.conftest import INTRUDER, OWNER


def _xlsx(sheets: dict[str, list[list]]) -> bytes:
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


ITEMS = _xlsx({
    "Instructions": [["How to fill in this template"], ["Name is required"]],
    "Items": [
        ["Inventory export for Test Merchant"], [],
        ["Clover ID", "Name", "Alternate Name", "Price", "Price Type", "Price Unit", "Tax Rates", "Cost",
         "Product Code", "SKU", "Quantity", "Hidden", "Categories"],
        ["CLV1CHICKEN", "Chicken", "", 12, "FIXED", "", "GST", 4.5, "", "CH-1", 20, "No", "Meat"],
        ["CLV2SOUP", "Tomato soup", "", "$7.50", "FIXED", "", "GST", "", "", "", "", "No", "Starters, Soups"],
        ["CLV3OLD", "Old special", "", 9, "FIXED", "", "", "", "", "", "", "Yes", ""],
    ],
})

ORDERS = (
    "Order ID,Order Number,Order Date,Order Total,Tax Amount,Tip,Payment State,Order Employee Name\n"
    "5TY9XK4G41YSC,1,05-Nov-2026 06:17 AM,$12.00,$0.00,$0.00,Paid,Duc Khoa\n"
    "8AB2CD3EF4GH5,2,05-Nov-2026 12:30 PM,\"$1,031.50\",$31.50,$5.00,Paid,Duc Khoa\n"
    "9ZZ9ZZ9ZZ9ZZ9,3,06-Nov-2026 7:05 PM,$19.50,$0.00,$0.00,Open,\n"
).encode()

LINES = (
    "Order ID,Order Date,Item Name,Quantity,Item Revenue\n"
    "8AB2CD3EF4GH5,05-Nov-2026 12:30 PM,Chicken,50,$600.00\n"
    ",,Tomato soup,40,$300.00\n"
    "8AB2CD3EF4GH5,05-Nov-2026 12:30 PM,Mystery dish,1,$100.00\n"
).encode()


async def _post(client, location_id, path, name, data, **form):
    return await client.post(f"/locations/{location_id}/imports{path}", headers=OWNER,
                             files={"file": (name, data, "application/octet-stream")}, data=form)


async def test_items_export_is_recognised_and_imported(client, session, demo_location_id):
    p = (await _post(client, demo_location_id, "/preview", "inventory.xlsx", ITEMS)).json()
    assert p["kind"] == "items" and p["sheet"] == "Items" and p["header_row"] == 3 and p["rows"] == 3
    cols = p["columns"]
    assert cols[p["mapping"]["name"]] == "Name" and cols[p["mapping"]["price"]] == "Price"
    assert cols[p["mapping"]["external_id"]] == "Clover ID" and cols[p["mapping"]["stock"]] == "Quantity"
    assert p["sample"][0]["name"] == "Chicken"

    r = (await _post(client, demo_location_id, "", "inventory.xlsx", ITEMS)).json()
    assert r == {"kind": "items", "added": 3, "updated": 0, "skipped": 0}
    chicken = await session.scalar(select(Item).where(Item.location_id == demo_location_id,
                                                      Item.external_id == "CLV1CHICKEN"))
    assert (chicken.name, chicken.price_cents, chicken.cost_cents, chicken.stock_qty, chicken.category,
            chicken.provider) == ("Chicken", 1200, 450, 20, "Meat", "clover")
    soup = await session.scalar(select(Item).where(Item.external_id == "CLV2SOUP"))
    assert soup.price_cents == 750 and soup.category == "Starters" and soup.stock_qty is None
    old = await session.scalar(select(Item).where(Item.external_id == "CLV3OLD"))
    assert old.active is False

    again = (await _post(client, demo_location_id, "", "inventory.xlsx", ITEMS)).json()
    assert again["added"] == 0 and again["updated"] == 3  # re-importing updates, never duplicates
    assert (await client.post(f"/locations/{demo_location_id}/imports/preview", headers=INTRUDER,
                              files={"file": ("x.xlsx", ITEMS, "x")})).status_code == 404


async def test_orders_and_line_items_build_sales(client, session, demo_location_id):
    await _post(client, demo_location_id, "", "inventory.xlsx", ITEMS)
    p = (await _post(client, demo_location_id, "/preview", "orders.csv", ORDERS)).json()
    assert p["kind"] == "orders" and p["rows"] == 3
    assert p["sample"][0]["date"] == "2026-11-05 06:17" and any("item column" in w for w in p["warnings"])
    r = (await _post(client, demo_location_id, "", "orders.csv", ORDERS)).json()
    assert r["added"] == 3 and r["from"] == "2026-11-05" and r["to"] == "2026-11-06"
    big = await session.scalar(select(Order).where(Order.external_id == "8AB2CD3EF4GH5"))
    assert (big.total_cents, big.tax_cents, big.tip_cents, big.status, big.employee_name) == (
        103150, 3150, 500, "paid", "Duc Khoa")
    assert (await session.scalar(select(Order).where(Order.external_id == "9ZZ9ZZ9ZZ9ZZ9"))).status == "open"

    # The line items export fills in what sold in each order (rows with a blank order id belong to the one above).
    li = (await _post(client, demo_location_id, "", "line-items.csv", LINES)).json()
    assert li["added"] == 0 and li["updated"] == 1
    session.expire_all()
    big = await session.scalar(select(Order).where(Order.external_id == "8AB2CD3EF4GH5"))
    assert big.total_cents == 103150  # the order's own total is kept
    assert [(ln.name, ln.qty, ln.category, ln.item_external_id) for ln in big.lines] == [
        ("Chicken", 50, "Meat", "CLV1CHICKEN"), ("Tomato soup", 40, "Starters", "CLV2SOUP"),
        ("Mystery dish", 1, None, None)]
    day = await session.scalar(select(DailySales).where(DailySales.location_id == demo_location_id,
                                                        DailySales.day == date(2026, 11, 5)))
    assert day.orders_count >= 2
    sold = await session.scalar(select(func.sum(ItemSalesDaily.qty)).where(
        ItemSalesDaily.location_id == demo_location_id, ItemSalesDaily.name == "Chicken",
        ItemSalesDaily.day == date(2026, 11, 5)))
    assert sold == 50

    again = (await _post(client, demo_location_id, "", "orders.csv", ORDERS)).json()
    assert again["added"] == 0 and again["updated"] == 3
    count = await session.scalar(select(func.count(Order.id)).where(Order.location_id == demo_location_id,
                                                                    Order.provider == "clover"))
    assert count == 3


async def test_owner_can_fix_columns_and_bad_files_are_explained(client, demo_location_id):
    odd = "Ticket,When,Grand Total,Dish\nA1,2026-11-07 18:00,$20.00,Soup\n".encode()
    p = (await _post(client, demo_location_id, "/preview", "odd.csv", odd)).json()
    assert p["kind"] == "orders" and p["columns"][p["mapping"]["date"]] == "When"
    assert p["mapping"]["order_id"] is None and p["mapping"]["item_name"] is None
    mapping = {**p["mapping"], "order_id": 0, "item_name": 3}
    r = (await _post(client, demo_location_id, "", "odd.csv", odd, mapping=json.dumps(mapping))).json()
    assert r["added"] == 1
    bad = await _post(client, demo_location_id, "", "odd.csv", odd, mapping=json.dumps({"date": None}))
    assert bad.status_code == 422 and "Date and time" in bad.json()["detail"]

    xls = await _post(client, demo_location_id, "/preview", "old.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 100)
    assert xls.status_code == 422 and ".xlsx" in xls.json()["detail"]
    junk = await _post(client, demo_location_id, "/preview", "notes.csv", b"hello\nworld\n")
    assert junk.status_code == 422 and "doesn't look like" in junk.json()["detail"]


def _sales_report(period: str | None) -> bytes:
    hours = [f"{h % 12 or 12}:00 {'a.m.' if h < 12 else 'p.m.'}" for h in range(24)]
    def by_hour(vals: dict[int, float]):
        return [vals.get(h, 0.0) for h in range(24)]
    net = {12: 43.95, 13: 130.55, 18: 234.65}
    tax = {12: 2.50, 13: 6.53, 18: 11.81}
    rows = [["Sales Report"]]
    if period:
        rows.append([period])
    rows += [["Requested on: Wed, Nov 11, 2026, 8:44 p.m."], ["Filters: none"], [],
             ["Net sales", 409.15, "from 31 orders"], ["Amount collected", 450.0], ["Gross sales", 420.0], [],
             ["Sales"], ["", "Total", *hours],
             ["Gross sales", 420.0, *by_hour(net)], ["Refunds", 0.0, *by_hour({})],
             ["Net sales", 409.15, *by_hour(net)], ["Taxes collected", 20.84, *by_hour(tax)],
             ["Tips", 12.0, *by_hour({18: 12.0})], ["Amount collected", 441.99, *by_hour({})], [],
             ["Tender types"], ["Tender", "Total"], ["Credit card", 441.99]]
    return _xlsx({"Sales Report": rows})


async def test_clover_sales_report_fills_in_a_days_totals(client, session, demo_location_id):
    one = _sales_report("Nov 10, 2026 12:00 a.m. - Nov 10, 2026 11:59 p.m.")
    blocked = await _post(client, demo_location_id, "", "Sales Report.xlsx", one, day="2026-11-10")
    assert blocked.status_code == 422 and "demo data" in blocked.json()["detail"]
    await client.post(f"/locations/{demo_location_id}/demo-data/clear", headers=OWNER)
    p = (await _post(client, demo_location_id, "/preview", "Sales Report.xlsx", one)).json()
    assert p["kind"] == "sales_report" and p["day"] == "2026-11-10" and p["needs_day"] is False
    assert p["totals"] == {"sales": 40915 + 2084, "tax": 2084, "tips": 1200, "orders": 31, "busiest_hour": "18"}
    r = (await _post(client, demo_location_id, "", "Sales Report.xlsx", one)).json()
    assert r == {"kind": "sales_report", "day": "2026-11-10", "sales_cents": 42999, "orders": 31}
    row = await session.get(DailySales, (demo_location_id, date(2026, 11, 10)))
    assert (row.gross_cents, row.tax_cents, row.tip_cents, row.orders_count) == (42999, 2084, 1200, 31)
    assert row.by_hour == {"12": 4645, "13": 13708, "18": 24646}

    # No date in the file: the owner picks the day.
    undated = _sales_report(None)
    p = (await _post(client, demo_location_id, "/preview", "r.xlsx", undated)).json()
    assert p["needs_day"] is True
    r = await _post(client, demo_location_id, "", "r.xlsx", undated)
    assert r.status_code == 422 and "which day" in r.json()["detail"]
    r = (await _post(client, demo_location_id, "", "r.xlsx", undated, day="2026-11-09")).json()
    assert r["day"] == "2026-11-09"

    # A day that already has single orders keeps them (they're more detailed than the report).
    orders = "Order ID,Order Date,Order Total\nX1,2026-11-08 12:00,$10.00\n".encode()
    await _post(client, demo_location_id, "", "o.csv", orders)
    r = await _post(client, demo_location_id, "", "r.xlsx", undated, day="2026-11-08")
    assert r.status_code == 422 and "already has single orders" in r.json()["detail"]

    many = _sales_report("Nov 1, 2026 12:00 a.m. - Nov 10, 2026 11:59 p.m.")
    p = (await _post(client, demo_location_id, "/preview", "r.xlsx", many)).json()
    assert p["multi_day"] is True and any("one day at a time" in w for w in p["warnings"])
