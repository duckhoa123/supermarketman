"""Create a demo restaurant with ~60 days of realistic sales, stock, tables and reviews.

    python -m app.seed --email owner@example.com

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
    DiningTable,
    Item,
    Location,
    Order,
    OrderLine,
    Organization,
    OrgMember,
    Review,
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


async def seed(email: str, days: int = 60, reset: bool = False) -> str:
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)  # convenience for local dev; prod uses Alembic
    rng = random.Random(42)
    user_id = f"dev-{email.lower()}"
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
        loc = Location(org_id=org.id, name="Demo Bistro", timezone="Australia/Sydney", currency="AUD")
        session.add(loc)
        await session.flush()
        for kind, channels in (("bad_review", ["push", "email"]), ("low_stock", ["push"]),
                               ("sales_drop", ["push"]), ("daily_digest", ["email"])):
            session.add(AlertRule(location_id=loc.id, kind=kind, channels=channels, params={}))

        items = []
        for i, (name, cat, price, cost, stock, low, unit, weight) in enumerate(MENU):
            item = Item(location_id=loc.id, provider="demo", external_id=f"demo-item-{i}", name=name, category=cat,
                        price_cents=price, cost_cents=cost, stock_qty=stock, low_stock_threshold=low, unit=unit)
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
                    status="open" if open_now else "paid", total_cents=subtotal, tax_cents=round(subtotal / 11),
                    tip_cents=rng.choice([0, 0, 0, 200, 500]), table_name=f"T{rng.randint(1, 12)}",
                    employee_name=rng.choice(STAFF), guests=rng.randint(1, 6), opened_at=opened.astimezone(ZoneInfo("UTC")),
                    closed_at=None if open_now else (opened + timedelta(minutes=rng.randint(25, 90))),
                    lines=lines,
                ))
            touched.add(day)
        for i, (rating, author, text) in enumerate(REVIEWS):
            posted = utcnow() - timedelta(days=i * 4 + rng.randint(0, 2), hours=rng.randint(0, 20))
            session.add(Review(location_id=loc.id, source="google", external_id=f"demo-review-{i}", author=author,
                               rating=rating, text=text, posted_at=posted, topics=[],
                               sentiment="positive" if rating >= 4 else "neutral" if rating == 3 else "negative",
                               reply_text="Thanks so much, see you again soon!" if rating >= 4 and i % 2 else None))
        await session.flush()
        await rebuild_rollups(session, loc, touched)
        await session.commit()
        print(f"Seeded Demo Bistro ({loc.id}) for {email}")
        return loc.id


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--email", default="owner@example.com")
    p.add_argument("--days", type=int, default=60)
    p.add_argument("--reset", action="store_true", help="create a fresh copy even if one exists")
    a = p.parse_args()
    asyncio.run(seed(a.email, a.days, a.reset))
