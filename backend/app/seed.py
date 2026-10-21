"""Create a demo restaurant in Vancouver with ~60 days of realistic sales, stock, tables, reviews, staff shifts,
suppliers, guests and bookings. It is listed in the customer app (visible only to its own team, since demo
restaurants are hidden from other customers) and takes pickup, delivery and booking requests.

    python -m app.seed --email owner@example.com
    python -m app.seed --driver-email driver@example.com     # invite a test driver (Driver Assistant)

Then sign in to the web app in dev mode as that email.
"""

import argparse
import asyncio
import random
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db import Base, get_engine, sessionmaker
from app.models import (
    AlertRule,
    Booking,
    DiningTable,
    Driver,
    Guest,
    Item,
    Location,
    Order,
    OrderLine,
    Organization,
    OrgMember,
    Review,
    StaffShift,
    Supplier,
    User,
    utcnow,
)
from app.services.sync import rebuild_rollups

MENU = [
    # name, category, price cents, cost cents, stock, low threshold, unit, popularity weight
    ("Margherita Pizza", "Pizza", 2200, 550, None, None, None, 9),
    ("Pepperoni Pizza", "Pizza", 2500, 650, None, None, None, 8),
    ("Truffle Mushroom Pizza", "Pizza", 2900, 900, None, None, None, 4),
    ("Spaghetti Carbonara", "Pasta", 2600, 600, None, None, None, 7),
    ("Beef Lasagne", "Pasta", 2700, 800, 14, 6, "portions", 5),
    ("Prawn Linguine", "Pasta", 3200, 1100, None, None, None, 4),
    ("Caesar Salad", "Salads", 1900, 450, None, None, None, 4),
    ("Burrata & Tomato", "Starters", 2100, 700, 4, 5, "balls", 3),
    ("Garlic Bread", "Starters", 900, 150, None, None, None, 8),
    ("Arancini (3)", "Starters", 1500, 350, None, None, None, 5),
    ("Grilled Salmon", "Mains", 3600, 1300, 9, 4, "fillets", 4),
    ("Chicken Parmigiana", "Mains", 3200, 900, None, None, None, 6),
    ("Tiramisu", "Desserts", 1400, 300, 3, 4, "portions", 5),
    ("Panna Cotta", "Desserts", 1300, 250, 11, 4, "portions", 3),
    ("Flat White", "Drinks", 550, 90, None, None, None, 7),
    ("House Red (glass)", "Drinks", 1300, 300, None, None, None, 5),
    ("Peroni", "Drinks", 1100, 350, 40, 24, "bottles", 6),
    ("Lemon Lime Bitters", "Drinks", 700, 120, None, None, None, 4),
]

STAFF = ["Mia", "Jack", "Olivia", "Noah", "Ava"]

# Nutrition per serving (estimates for the demo) and menu tags, by item name.
NUTRITION = {
    "Margherita Pizza": (850, 34, 104, 30, ["vegetarian"]),
    "Pepperoni Pizza": (1020, 42, 102, 46, []),
    "Truffle Mushroom Pizza": (930, 33, 100, 42, ["vegetarian"]),
    "Spaghetti Carbonara": (780, 30, 82, 36, []),
    "Beef Lasagne": (720, 38, 54, 38, []),
    "Prawn Linguine": (640, 34, 78, 20, ["pescatarian"]),
    "Caesar Salad": (420, 14, 18, 32, []),
    "Burrata & Tomato": (380, 18, 10, 30, ["vegetarian", "gluten_free"]),
    "Garlic Bread": (310, 8, 40, 13, ["vegetarian"]),
    "Arancini (3)": (450, 12, 52, 21, ["vegetarian"]),
    "Grilled Salmon": (560, 42, 12, 38, ["gluten_free", "pescatarian"]),
    "Chicken Parmigiana": (890, 58, 46, 50, []),
    "Tiramisu": (450, 7, 42, 28, ["vegetarian"]),
    "Panna Cotta": (330, 5, 28, 22, ["vegetarian", "gluten_free"]),
}
DESCRIPTIONS = {
    "Margherita Pizza": "Tomato, fior di latte, basil.",
    "Spaghetti Carbonara": "Guanciale, egg yolk, pecorino, black pepper.",
    "Grilled Salmon": "With lemon, capers and seasonal greens.",
    "Burrata & Tomato": "Local tomatoes, basil oil.",
}
SUPPLIERS = [  # fictional suppliers with example.com addresses, so nothing is ever emailed for real
    ("Coastal Produce Co. (demo)", "produce@example.com", "Order by 2 pm for next-day delivery."),
    ("Harbour Dairy & Deli (demo)", "orders@example.com", "Delivers Tue, Thu, Sat."),
    ("Westside Beverages (demo)", "drinks@example.com", None),
]
ITEM_SUPPLIER = {"Burrata & Tomato": 1, "Tiramisu": 1, "Panna Cotta": 1, "Beef Lasagne": 0, "Grilled Salmon": 0,
                 "Peroni": 2}
GUESTS = [("Sophie Lee", True, 9), ("Daniel Kim", True, 6), ("Priya Shah", False, 3), ("Ben Harris", True, 12),
          ("Grace Tran", True, 2), ("Luca Bianchi", False, 5), ("Hannah Wong", True, 1), ("Omar Haddad", True, 4)]
HOURS = {d: [["11:30", "22:00"]] for d in ("mon", "tue", "wed", "thu", "sun")} | {
    d: [["11:30", "23:00"]] for d in ("fri", "sat")}

REVIEWS = [
    (5, "Sophie L.", "Best carbonara I've had outside Rome. Friendly staff and quick service even on a Friday."),
    (5, "Daniel K.", "Lovely spot for a family dinner. Kids loved the pizza, we loved the wine list."),
    (4, "Priya S.", "Great food, slightly noisy on a Saturday night but would come back."),
    (2, "Tom R.", "Waited 50 minutes for our mains and nobody checked on us. Food was fine once it came."),
    (5, "Hannah W.", "The tiramisu is incredible. Service was warm and attentive."),
    (3, "Chris P.", "Pizza was good but the salad was overdressed and the bread was cold."),
    (1, "Alex M.", "Booked a table for 7pm, still waited 30 minutes to be seated. Very disappointing."),
    (4, "Grace T.", "Solid Italian, good value lunch specials."),
    (5, "Luca B.", "Authentic flavours, generous portions. The burrata is a must."),
    (2, "Jess D.", "Found a hair in my pasta. Staff replaced it but no apology from the manager."),
    (5, "Ben H.", "Fantastic service from Mia, she made our anniversary special."),
    (4, "Nina F.", "Really good coffee and pastries for a weekday breakfast meeting."),
]


def _hour_weights(weekday: int) -> dict[int, float]:
    lunch = {11: 0.5, 12: 1.6, 13: 1.4, 14: 0.6}
    dinner = {17: 0.6, 18: 1.5, 19: 2.0, 20: 1.5, 21: 0.7}
    base = {9: 0.2, 10: 0.3, 15: 0.3, 16: 0.3, **lunch, **dinner}
    if weekday >= 4:  # Fri-Sun busier dinners
        base = {h: w * (1.4 if h >= 17 else 1.1) for h, w in base.items()}
    return base


async def seed(
    email: str, days: int = 60, reset: bool = False, user_id: str | None = None, create_tables: bool = True
) -> str:
    """Create the demo restaurant for a user. `user_id` defaults to the dev sign-in id for `email`;
    pass the real account id (the Supabase user id) to seed a signed-in production account."""
    if create_tables:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)  # convenience for local dev; prod uses Alembic
    rng = random.Random(42)
    user_id = user_id or f"dev-{email.lower()}"
    async with sessionmaker()() as session:
        existing = await session.scalar(
            select(Location).join(OrgMember, OrgMember.org_id == Location.org_id)
            .where(OrgMember.user_id == user_id, Location.name == "Demo Bistro")
        )
        if existing and not reset:
            print(f"Demo Bistro already exists for {email}: {existing.id}")
            return existing.id
        if await session.get(User, user_id) is None:
            session.add(User(id=user_id, email=email.lower(), name="Demo Owner"))
        org = Organization(name="Demo Hospitality Group", plan="demo")
        session.add(org)
        await session.flush()
        session.add(OrgMember(org_id=org.id, user_id=user_id, role="owner"))
        loc = Location(
            org_id=org.id, name="Demo Bistro", timezone="America/Vancouver", currency="CAD",
            address="Main St & E 8th Ave, Vancouver, BC (demo)", lat=49.2636, lng=-123.1009, cuisine="Italian",
            phone="+16045550100", opening_hours=HOURS, listed=True, accepts_orders=True, accepts_delivery=True,
            accepts_bookings=True, tax_rate_bp=500,
        )
        session.add(loc)
        await session.flush()
        from app.api.account import DEFAULT_RULES

        for kind, channels in DEFAULT_RULES:
            session.add(AlertRule(location_id=loc.id, kind=kind, channels=channels, params={}))
        suppliers = []
        for name, email_, notes in SUPPLIERS:
            sup = Supplier(location_id=loc.id, name=name, email=email_, notes=notes)
            session.add(sup)
            suppliers.append(sup)
        await session.flush()

        items = []
        for i, (name, cat, price, cost, stock, low, unit, weight) in enumerate(MENU):
            cal, protein, carbs, fat, tags = NUTRITION.get(name, (None, None, None, None, []))
            sup = ITEM_SUPPLIER.get(name)
            item = Item(location_id=loc.id, provider="demo", external_id=f"demo-item-{i}", name=name, category=cat,
                        price_cents=price, cost_cents=cost, stock_qty=stock, low_stock_threshold=low, unit=unit,
                        calories=cal, protein_g=protein, carbs_g=carbs, fat_g=fat, tags=tags,
                        description=DESCRIPTIONS.get(name),
                        supplier_id=suppliers[sup].id if sup is not None else None,
                        reorder_qty={"Peroni": 48}.get(name))
            session.add(item)
            items.append((item, weight))
        for n in range(1, 13):
            session.add(DiningTable(location_id=loc.id, name=f"T{n}", seats=2 if n <= 4 else 4 if n <= 10 else 6,
                                    status=rng.choice(["free", "free", "occupied", "reserved"])))

        tz = ZoneInfo(loc.timezone)
        today = utcnow().astimezone(tz).date()
        touched = set()
        weights = [w for _, w in items]
        for back in range(days, -1, -1):
            day = today - timedelta(days=back)
            trend = 1 + (days - back) * 0.003  # gentle growth
            n_orders = int(rng.gauss(70 if day.weekday() < 4 else 105, 10) * trend)
            if back == 3:
                n_orders = int(n_orders * 0.6)  # a slow day the agent can find
            hours = _hour_weights(day.weekday())
            now_local = utcnow().astimezone(tz)
            for k in range(max(10, n_orders)):
                hour = rng.choices(list(hours), weights=list(hours.values()))[0]
                opened = datetime.combine(day, time(hour, rng.randint(0, 59)), tzinfo=tz)
                if opened > now_local:
                    continue
                picks = rng.choices(items, weights=weights, k=rng.randint(1, 5))
                lines = [OrderLine(item_external_id=it.external_id, name=it.name, category=it.category, qty=1,
                                   unit_price_cents=it.price_cents, total_cents=it.price_cents) for it, _ in picks]
                subtotal = sum(ln.total_cents for ln in lines)
                open_now = back == 0 and (now_local - opened) < timedelta(minutes=45)
                session.add(Order(
                    location_id=loc.id, provider="demo", external_id=f"demo-{day.isoformat()}-{k}",
                    status="open" if open_now else "paid", total_cents=subtotal + round(subtotal * 0.05),
                    tax_cents=round(subtotal * 0.05),  # BC: 5% GST on restaurant meals, added on top of menu prices
                    tip_cents=rng.choice([0, 0, 0, 200, 500]), table_name=f"T{rng.randint(1, 12)}",
                    employee_name=rng.choice(STAFF), guests=rng.randint(1, 6), opened_at=opened.astimezone(ZoneInfo("UTC")),
                    closed_at=None if open_now else (opened + timedelta(minutes=rng.randint(25, 90))),
                    lines=lines,
                ))
            touched.add(day)
            # Staff shifts for labour cost (about 25-30% of sales, typical for full service), more at weekends.
            if day <= today:
                crew = [("kitchen", 10, 15, 2200), ("kitchen", 10, 15, 2200), ("kitchen", 15, 23, 2200),
                        ("kitchen", 15, 23, 2200), ("kitchen", 16, 23, 2200), ("floor", 11, 15, 1825),
                        ("floor", 11, 15, 1825), ("floor", 16, 22, 1825), ("floor", 16, 22, 1825),
                        ("floor", 17, 22, 1825), ("dish", 17, 23, 1825), ("manager", 10, 20, 2800)]
                if day.weekday() >= 4:
                    crew += [("floor", 17, 23, 1825), ("floor", 17, 23, 1825), ("kitchen", 17, 22, 2200)]
                for n, (role, start_h, end_h, rate) in enumerate(crew):
                    session.add(StaffShift(
                        location_id=loc.id, staff_name=STAFF[n % len(STAFF)], role=role,
                        starts_at=datetime.combine(day, time(start_h), tzinfo=tz),
                        ends_at=datetime.combine(day, time(end_h), tzinfo=tz), hourly_rate_cents=rate))
        for i, (rating, author, text) in enumerate(REVIEWS):
            posted = utcnow() - timedelta(days=i * 4 + rng.randint(0, 2), hours=rng.randint(0, 20))
            session.add(Review(location_id=loc.id, source="google", external_id=f"demo-review-{i}", author=author,
                               rating=rating, text=text, posted_at=posted, topics=[],
                               sentiment="positive" if rating >= 4 else "neutral" if rating == 3 else "negative",
                               reply_text="Thanks so much, see you again soon!" if rating >= 4 and i % 2 else None))
        now = utcnow()
        for i, (name, consent, visits) in enumerate(GUESTS):
            first = name.split()[0].lower()
            session.add(Guest(
                location_id=loc.id, name=name, email=f"{first}@example.com", marketing_consent=consent,
                consent_at=now if consent else None, visits=visits, total_spend_cents=visits * rng.randint(4500, 9000),
                last_visit_at=now - timedelta(days=3 + i * 9), tags=["regular"] if visits >= 5 else []))
        for i, (hour, size, name) in enumerate([(18, 2, "Sophie Lee"), (19, 4, "Daniel Kim"), (19, 6, "Omar Haddad"),
                                                (20, 2, "Grace Tran")]):
            day = today + timedelta(days=i % 2)
            session.add(Booking(location_id=loc.id, name=name, party_size=size, source="phone" if i % 2 else "staff",
                                starts_at=datetime.combine(day, time(hour, 30 if i == 3 else 0), tzinfo=tz),
                                status="confirmed"))
        await session.flush()
        await rebuild_rollups(session, loc, touched)
        # Low items have suppliers, so the demo opens with a supplier order waiting for approval.
        from app.services.purchasing import draft_from_low_stock

        await draft_from_low_stock(session, loc)
        await seed_deliveries(session, loc, suppliers, today)
        await session.commit()
        print(f"Seeded Demo Bistro ({loc.id}) for {email}")
        return loc.id


# Past deliveries for the receiving log: (days ago, supplier index, invoice no., [(description, qty, unit, price)])
DAIRY = [("Burrata 125g", 12, "ea"), ("Mascarpone 500g", 4, "tub"), ("Fior di latte 1kg", 3, "kg")]
PRODUCE = [("Roma tomatoes", 10, "kg"), ("Basil bunch", 8, "ea"), ("Atlantic salmon fillet", 6, "kg")]
DRINKS = [("Peroni Nastro Azzurro 330ml", 4, "case"), ("San Pellegrino 750ml", 2, "case")]


def _priced(rows, prices):
    return [(d, q, u, c) for (d, q, u), c in zip(rows, prices, strict=True)]


DELIVERIES = [
    (24, 1, "HD-1012", _priced(DAIRY, [750, 1050, 1490])),
    (20, 0, "CP-3381", _priced(PRODUCE, [420, 225, 3150])),
    (17, 2, "WB-552", _priced(DRINKS, [5280, 3360])),
    (10, 1, "HD-1041", _priced(DAIRY, [750, 1050, 1490])),
    (6, 0, "CP-3410", _priced(PRODUCE, [465, 225, 3150])),  # tomatoes up 11%
    (3, 1, "HD-1077", _priced(DAIRY, [840, 1050, 1440])),  # burrata up 12%, fior di latte down 3%
]


async def seed_deliveries(session, loc: Location, suppliers: list[Supplier], today) -> None:
    """Confirmed invoices with a couple of price changes, and the Restaurant Manager's in-app notice about the
    latest one (not emailed: it's sample data)."""
    from app.models import Alert, InvoiceScan, ReceivedLine
    from app.services import invoice_math as im
    from app.services.receiving import price_alert_text

    # Link delivered products to the demo's stock items (no stock is added: these deliveries are history).
    links = {"Burrata 125g": "Burrata & Tomato", "Atlantic salmon fillet": "Grilled Salmon",
             "Peroni Nastro Azzurro 330ml": "Peroni"}
    item_ids = {name: iid for name, iid in await session.execute(
        select(Item.name, Item.id).where(Item.location_id == loc.id, Item.name.in_(links.values())))}
    last: dict[str, ReceivedLine] = {}
    for days_ago, sup_i, number, rows in DELIVERIES:
        sup = suppliers[sup_i]
        day = today - timedelta(days=days_ago)
        lines = [{"description": d, "qty": q, "unit": u, "unit_price_cents": c, "line_total_cents": q * c,
                  "kind": "item", "item_id": None, "pack_size": 1, "match": "none"} for d, q, u, c in rows]
        scan = InvoiceScan(location_id=loc.id, status="confirmed", file_name=f"{number}.jpg", content_type="image/jpeg",
                           supplier_id=sup.id, supplier_name=sup.name, invoice_number=number, invoice_date=day,
                           subtotal_cents=sum(ln["line_total_cents"] for ln in lines), tax_cents=0, lines=lines,
                           warnings=[], confirmed_at=utcnow() - timedelta(days=days_ago))
        session.add(scan)
        await session.flush()
        changes = []
        for ln in lines:
            key = im.product_key(sup.name, ln["description"])
            prev = last.get(key)
            pct = im.price_change_pct(ln["unit_price_cents"], 1, prev.unit_price_cents, 1) if prev else None
            row = ReceivedLine(location_id=loc.id, scan_id=scan.id, supplier_id=sup.id, supplier_name=sup.name,
                               invoice_number=number, received_on=day, description=ln["description"], product_key=key,
                               kind="item", qty=ln["qty"], unit=ln["unit"], unit_price_cents=ln["unit_price_cents"],
                               line_total_cents=ln["line_total_cents"], item_id=item_ids.get(links.get(ln["description"])),
                               pack_size=24 if ln["unit"] == "case" and "Peroni" in ln["description"] else 1,
                               previous_unit_price_cents=prev.unit_price_cents if prev else None, price_change_pct=pct)
            session.add(row)
            await session.flush()
            if pct:
                changes.append({"line": ln, "prev": prev, "pct": pct, "key": key, "pack": 1})
            last[key] = row
        if changes and days_ago == DELIVERIES[-1][0]:
            title, body, data = await price_alert_text(session, loc, sup.name, scan, changes)
            session.add(Alert(location_id=loc.id, kind="price_change", title=title, body=body, data=data,
                              channels=[], dedupe_key=f"price:{scan.id}"))


async def invite_driver(email: str, name: str = "Test Driver", role: str = "driver") -> str:
    """Create a driver invite; the account with this email gets the Driver Assistant on sign-in."""
    async with sessionmaker()() as session:
        existing = await session.scalar(select(Driver).where(Driver.email == email.lower()))
        if existing:
            print(f"Driver already invited: {email}")
            return existing.id
        d = Driver(email=email.lower(), name=name, role=role, vehicle="e_bike", hourly_wage_cents=2000)
        session.add(d)
        await session.commit()
        print(f"Invited driver {email}")
        return d.id


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--email", default="owner@example.com")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--reset", action="store_true", help="create a fresh copy even if one exists")
    p.add_argument("--driver-email", help="invite this email as a driver instead of seeding a restaurant")
    p.add_argument("--dispatcher", action="store_true", help="with --driver-email: make them a dispatcher")
    a = p.parse_args()
    if a.driver_email:
        asyncio.run(invite_driver(a.driver_email, role="dispatcher" if a.dispatcher else "driver"))
    else:
        asyncio.run(seed(a.email, a.days, a.reset))
