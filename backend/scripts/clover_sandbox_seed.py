#!/usr/bin/env python3
"""Fill a Clover SANDBOX test merchant with a small restaurant menu and two weeks of orders, for trying Pass.

Only talks to Clover's sandbox (apisandbox.dev.clover.com). Uses Python's standard library: no installs.

1. In the sandbox merchant dashboard (sandbox.dev.clover.com), open Account & Setup -> API Tokens and create a
   token with Inventory (read and write) and Orders (read and write).
2. Find the merchant ID: it's in the dashboard address, e.g. .../m/ABCD1234EFGH5/...
3. Run:
       CLOVER_TOKEN=<the token> python3 clover_sandbox_seed.py --merchant ABCD1234EFGH5

Running it again reuses the menu it already made (matched by name) and adds more orders.
"""

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta

SANDBOX = "https://apisandbox.dev.clover.com"

# (category, name, price in dollars, cost in dollars, stock or None, popularity weight)
MENU = [
    ("Starters", "Garlic Bread", 9.00, 2.10, 30, 5),
    ("Starters", "Arancini (3)", 15.00, 4.20, 24, 4),
    ("Starters", "Burrata & Tomato", 21.00, 7.50, 6, 3),
    ("Pizza", "Margherita Pizza", 22.00, 5.40, None, 8),
    ("Pizza", "Pepperoni Pizza", 25.00, 6.30, None, 6),
    ("Pizza", "Truffle Mushroom Pizza", 29.00, 8.10, None, 3),
    ("Pasta", "Spaghetti Carbonara", 26.00, 6.80, None, 7),
    ("Pasta", "Beef Lasagne", 27.00, 7.90, 12, 4),
    ("Pasta", "Prawn Linguine", 32.00, 11.20, None, 3),
    ("Mains", "Chicken Parmigiana", 32.00, 9.60, None, 4),
    ("Mains", "Grilled Salmon", 36.00, 13.50, 10, 3),
    ("Salads", "Caesar Salad", 19.00, 5.10, None, 3),
    ("Desserts", "Tiramisu", 14.00, 3.60, 8, 5),
    ("Desserts", "Panna Cotta", 13.00, 2.90, 10, 3),
    ("Drinks", "Flat White", 5.50, 0.90, None, 6),
    ("Drinks", "House Red (glass)", 13.00, 3.80, None, 4),
    ("Drinks", "Peroni", 11.00, 3.20, 48, 4),
    ("Drinks", "Lemon Lime Bitters", 7.00, 1.20, None, 3),
]


class Clover:
    def __init__(self, base: str, merchant: str, token: str):
        self.base, self.merchant, self.token = base.rstrip("/"), merchant, token
        self.calls = 0

    def req(self, method: str, path: str, body: dict | None = None, params: str = "") -> dict:
        url = f"{self.base}/v3/merchants/{self.merchant}{path}{params}"
        data = json.dumps(body).encode() if body is not None else None
        for attempt in range(5):
            r = urllib.request.Request(url, data=data, method=method, headers={
                "Authorization": f"Bearer {self.token}", "Content-Type": "application/json",
                "Accept": "application/json"})
            try:
                with urllib.request.urlopen(r, timeout=30) as resp:
                    self.calls += 1
                    time.sleep(0.08)  # stay well under the sandbox rate limit
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < 4:
                    time.sleep(2 * (attempt + 1))
                    continue
                detail = e.read().decode(errors="replace")[:300]
                if e.code == 401:
                    sys.exit("Clover said 401: the token is wrong, expired, or for a different merchant.")
                if e.code == 403:
                    sys.exit("Clover said 403: give the token Inventory and Orders read AND write permissions.")
                sys.exit(f"Clover said {e.code} for {method} {path}: {detail}")
        sys.exit("Clover kept rate-limiting; wait a minute and run again.")

    def all(self, path: str) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self.req("GET", path, params=f"?limit=100&offset={offset}").get("elements", [])
            out += page
            if len(page) < 100:
                return out
            offset += 100


def cents(dollars: float) -> int:
    return round(dollars * 100)


def build_menu(c: Clover) -> list[tuple[dict, int]]:
    """Categories and items (reusing any with the same name), with cost and stock. Returns (item, weight)."""
    categories = {x["name"]: x["id"] for x in c.all("/categories")}
    items = {x["name"]: x for x in c.all("/items")}
    made = []
    for cat, name, price, cost, stock, weight in MENU:
        if cat not in categories:
            categories[cat] = c.req("POST", "/categories", {"name": cat})["id"]
            print(f"  + category {cat}")
        item = items.get(name)
        if item is None:
            item = c.req("POST", "/items", {"name": name, "price": cents(price), "priceType": "FIXED",
                                            "cost": cents(cost), "sku": f"PASS-{len(made) + 1:03d}"})
            c.req("POST", "/category_items", {"elements": [{"category": {"id": categories[cat]},
                                                            "item": {"id": item["id"]}}]})
            print(f"  + item {name} (${price:.2f}, {cat})")
        if stock is not None:
            c.req("POST", f"/item_stocks/{item['id']}", {"quantity": stock})
        made.append((item | {"price": item.get("price") or cents(price)}, weight))
    return made


def make_orders(c: Clover, menu: list[tuple[dict, int]], days: int, per_day: int) -> int:
    """Lunch and dinner orders over the past days, busier at weekends. Locked orders count as paid in Pass."""
    items, weights = [m[0] for m in menu], [m[1] for m in menu]
    now = datetime.now()
    count = 0
    for d in range(days, 0, -1):
        day = (now - timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        n = per_day + (per_day // 2 if day.weekday() >= 4 else 0) + random.randint(-2, 2)
        for _ in range(max(3, n)):
            hour = random.choice([12, 12, 13, 13, 17, 18, 18, 19, 19, 20, 21])
            when = day + timedelta(hours=hour, minutes=random.randint(0, 59))
            lines = random.choices(items, weights=weights, k=random.choice([1, 2, 2, 3, 3, 4, 5]))
            total = sum(int(i["price"]) for i in lines)
            total_with_tax = round(total * 1.05)  # BC: 5% GST on restaurant meals
            order = c.req("POST", "/orders", {"state": "open", "total": total_with_tax,
                                              "clientCreatedTime": int(when.timestamp() * 1000),
                                              "title": "Pass test order"})
            c.req("POST", f"/orders/{order['id']}/bulk_line_items",
                  {"items": [{"item": {"id": i["id"]}} for i in lines]})
            c.req("POST", f"/orders/{order['id']}", {"state": "locked", "total": total_with_tax})
            count += 1
        print(f"  {day:%a %d %b}: orders added")
    return count


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--merchant", required=True, help="the sandbox merchant ID (13 characters)")
    p.add_argument("--days", type=int, default=14, help="days of order history to create (default 14)")
    p.add_argument("--per-day", type=int, default=10, help="orders on a weekday (weekends get more)")
    p.add_argument("--menu-only", action="store_true", help="create the menu and stock, but no orders")
    p.add_argument("--api-base", default=SANDBOX, help=argparse.SUPPRESS)  # for testing against a local fake
    a = p.parse_args()
    token = os.environ.get("CLOVER_TOKEN", "").strip()
    if not token:
        sys.exit("Set CLOVER_TOKEN to a sandbox API token first (see the top of this file).")
    if a.api_base != SANDBOX and not a.api_base.startswith("http://127.0.0.1"):
        sys.exit("This script only works with Clover's sandbox.")
    c = Clover(a.api_base, a.merchant.strip(), token)
    print("Menu and stock:")
    menu = build_menu(c)
    if not a.menu_only:
        print(f"Orders for the last {a.days} days:")
        n = make_orders(c, menu, max(1, min(a.days, 60)), max(1, min(a.per_day, 40)))
        print(f"Done: {len(menu)} menu items and {n} orders ({c.calls} requests).")
    else:
        print(f"Done: {len(menu)} menu items ({c.calls} requests).")
    print("In Pass: Settings -> Connected accounts -> Clover -> Sync now (or wait up to 15 minutes).")


if __name__ == "__main__":
    main()
