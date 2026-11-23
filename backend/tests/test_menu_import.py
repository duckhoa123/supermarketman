"""Menu import: read a menu, suggest recipes, review them, and create the menu items, inventory items and recipes."""

from sqlalchemy import select

from app.agent.llm import LLMResult
from app.agent.pricing import Usage
from app.models import Item, MenuImport, RecipeLine
from app.services import menu_import as mi
from app.services.jobs import run_pending
from tests.conftest import INTRUDER, OWNER

MENU_CSV = b"Dish,Section,Price\nPho Tai,Pho,16.50\nBun Bo Hue,Noodle soups,17.00\nBurrata & Tomato,Starters,21\n"


def tool(name: str, payload: dict) -> LLMResult:
    return LLMResult(content=[{"type": "tool_use", "id": f"toolu_{name}", "name": name, "input": payload}],
                     stop_reason="tool_use", usage=Usage(input_tokens=1500, output_tokens=600))


READ = tool("record_menu", {"dishes": [
    {"name": "Pho Tai", "category": "Pho", "price": 16.5},
    {"name": "Bun Bo Hue", "category": "Noodle soups", "price": 17},
    {"name": "Burrata & Tomato", "category": "Starters", "price": 21},
]})
RECIPES = tool("suggest_recipes", {
    "recipes": [
        {"dish": "Pho Tai", "lines": [{"ingredient": "Rice noodles", "qty": 200, "unit": "g"},
                                      {"ingredient": "Beef eye round", "qty": 120, "unit": "g"},
                                      {"ingredient": "Pho broth", "qty": 450, "unit": "ml"}]},
        {"dish": "Bun Bo Hue", "lines": [{"ingredient": "Rice vermicelli", "qty": 180, "unit": "g"},
                                         {"ingredient": "Beef shank", "qty": 100, "unit": "g"}]},
        {"dish": "Burrata & Tomato", "lines": [{"ingredient": "Burrata", "qty": 1, "unit": "each"}]},
    ],
    "new_ingredients": [{"name": "Rice noodles", "unit": "kg", "category": "Dry goods"},
                        {"name": "Beef eye round", "unit": "lb", "category": "Protein"},
                        {"name": "Pho broth", "unit": "l", "category": "Prep"},
                        {"name": "Beef shank", "unit": "lb", "category": "Protein"},
                        {"name": "Burrata", "unit": "each", "category": "Dairy"}],
})


async def _upload(client, location_id: str, data: bytes = MENU_CSV, name: str = "menu.csv", ctype: str = "text/csv"):
    return await client.post(f"/locations/{location_id}/menu-imports", headers=OWNER,
                             files={"file": (name, data, ctype)})


async def test_menu_to_recipes(client, session, demo_location_id, fake_llm):
    session.add(Item(location_id=demo_location_id, provider="pass", external_id="pass-verm", name="Rice vermicelli",
                     unit="kg", stock_qty=5, price_cents=0, active=True, category="Dry goods"))
    await session.commit()
    llm = fake_llm([READ, RECIPES])
    r = await _upload(client, demo_location_id)
    assert r.status_code == 200 and r.json()["status"] == "processing"
    await run_pending(time_budget_s=20)
    base = f"/locations/{demo_location_id}/menu-imports/{r.json()['id']}"
    imp = (await client.get(base, headers=OWNER)).json()
    assert imp["status"] == "review", imp["error"]
    # The spreadsheet went to the AI as text; the recipes came with the inventory's names to reuse.
    assert "Pho Tai,Pho,16.50" in llm.calls[0]["messages"][0]["content"][0]["text"]
    assert "Rice vermicelli (kg)" in llm.calls[1]["system"][1]["text"]

    dishes = {d["name"]: d for d in imp["dishes"]}
    assert dishes["Pho Tai"]["price_cents"] == 1650 and dishes["Pho Tai"]["menu_item_id"] is None
    assert dishes["Burrata & Tomato"]["menu_item_id"] and dishes["Burrata & Tomato"]["menu_match"] == "exact"
    verm = dishes["Bun Bo Hue"]["lines"][0]
    assert verm["item_id"] and verm["ingredient"] == "Rice vermicelli" and verm["match"] == "exact"
    assert imp["ingredients"]["beef eye round"] == {"name": "Beef eye round", "unit": "lb", "category": "Protein"}

    # Review: a bigger portion, drop the broth, add lime wedges. Another restaurant can't touch it.
    pho = dishes["Pho Tai"]
    pho["lines"][1]["qty"] = 140
    pho["lines"] = [ln for ln in pho["lines"] if ln["ingredient"] != "Pho broth"] + [
        {"ingredient": "Lime", "item_id": None, "qty": 1, "unit": "each"}]
    body = {"dishes": list(dishes.values()), "ingredients": imp["ingredients"]}
    assert (await client.put(base, headers=INTRUDER, json=body)).status_code in (403, 404)
    saved = (await client.put(base, headers=OWNER, json=body)).json()
    assert "pho broth" not in saved["ingredients"] and saved["ingredients"]["lime"]["unit"] == "each"

    # A portion in a unit its item can't be counted in is caught before anything is created.
    bad = {**body, "dishes": [{**d, "lines": [{**ln, "unit": "each"} if ln["ingredient"] == "Rice vermicelli" else ln
                                              for ln in d["lines"]]} for d in body["dishes"]]}
    await client.put(base, headers=OWNER, json=bad)
    r = await client.post(f"{base}/confirm", headers=OWNER)
    assert r.status_code == 422 and "Rice vermicelli is counted in kg" in r.json()["detail"]
    await client.put(base, headers=OWNER, json=body)

    r = await client.post(f"{base}/confirm", headers=OWNER)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["recipes"] == 3 and sorted(out["dishes_created"]) == ["Bun Bo Hue", "Pho Tai"] and not out["errors"]
    assert sorted(out["items_created"]) == ["Beef eye round", "Beef shank", "Burrata", "Lime", "Rice noodles"]
    pho_item = await session.scalar(select(Item).where(Item.location_id == demo_location_id, Item.name == "Pho Tai"))
    assert pho_item.price_cents == 1650 and pho_item.recipe_since is not None
    lines = list(await session.scalars(select(RecipeLine).where(RecipeLine.menu_item_id == pho_item.id)
                                       .order_by(RecipeLine.sort)))
    assert [(ln.qty, ln.unit) for ln in lines] == [(200, "g"), (140, "g"), (1, "each")]
    beef = await session.scalar(select(Item).where(Item.location_id == demo_location_id, Item.name == "Beef eye round"))
    assert beef.unit == "lb" and beef.category == "Protein" and beef.price_cents == 0 and beef.stock_qty == 0
    assert (await session.get(MenuImport, out_id(base))).status == "confirmed"

    # Its new ingredients are inventory items; the new dishes are menu items, not inventory.
    on_hand = {i["name"] for i in (await client.get(f"/locations/{demo_location_id}/inventory/on-hand",
                                                    headers=OWNER)).json()["items"]}
    assert {"Beef eye round", "Lime"} <= on_hand and "Pho Tai" not in on_hand


def out_id(base: str) -> str:
    return base.rsplit("/", 1)[1]


async def test_a_photo_goes_as_an_image_and_existing_recipes_stay(client, session, demo_location_id, fake_llm):
    llm = fake_llm([tool("record_menu", {"dishes": []})])
    r = await _upload(client, demo_location_id, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "menu.png", "image/png")
    await run_pending(time_budget_s=20)
    imp = (await client.get(f"/locations/{demo_location_id}/menu-imports/{r.json()['id']}", headers=OWNER)).json()
    assert llm.calls[0]["messages"][0]["content"][0]["type"] == "image"
    assert imp["status"] == "failed" and "No dishes" in imp["error"]
    bad = await _upload(client, demo_location_id, b"hello", "notes.docx", "application/msword")
    assert bad.status_code == 422


def test_clean_dishes_and_units():
    out = mi.clean_dishes({"dishes": [{"name": " Pho  Tai ", "price": "16.50"}, {"name": "pho tai"}, {"name": ""},
                                      {"name": "Water", "price": 0}]})
    assert [d["name"] for d in out] == ["Pho Tai", "Water"] and out[0]["price_cents"] == 1650
    assert out[1]["price_cents"] is None
    assert mi.fits("kg", "g") and mi.fits("each", "each") and not mi.fits("kg", "each")
    assert mi._unit_for("oz") == "lb" and mi._unit_for("ml") == "l" and mi._unit_for("each") == "each"


def test_suggestions_find_their_dish_when_the_name_is_written_differently():
    dishes = [{"name": "Pork Spring Rolls - Cha Gio (2)"}, {"name": "Chicken Wings - Canh Ga"}]
    assert mi.find_dish(dishes, {"n": 1, "dish": "Pork Spring Rolls – Cha Gio"}) is dishes[0]
    assert mi.find_dish(dishes, {"dish": "Chicken Wings"}) is dishes[1]  # half the name, no number
    assert mi.find_dish(dishes, {"n": 2, "dish": "Chicken wings (Canh Ga)"}) is dishes[1]
    assert mi.find_dish(dishes, {"n": 2, "dish": "Bun Bo Hue"}) is None  # a wrong number and a name that fits nothing
    assert mi.find_dish(dishes, {"dish": "Bun Bo Hue"}) is None


async def test_empty_dishes_are_asked_again_then_on_request(client, session, demo_location_id, fake_llm):
    # The first answer skips Bun Bo Hue and renames Pho Tai; the retry fills Bun Bo Hue in.
    first = tool("suggest_recipes", {"recipes": [
        {"n": 1, "dish": "Phở Tái", "lines": [{"ingredient": "Rice noodles", "qty": 200, "unit": "g"}]},
        {"n": 3, "dish": "Burrata & Tomato", "lines": [{"ingredient": "Burrata", "qty": 1, "unit": "each"}]}]})
    retry = tool("suggest_recipes", {"recipes": [
        {"n": 1, "dish": "Bun Bo Hue", "lines": [{"ingredient": "Beef shank", "qty": 100, "unit": "g"}]}]})
    llm = fake_llm([READ, first, retry])
    r = await _upload(client, demo_location_id)
    await run_pending(time_budget_s=20)
    base = f"/locations/{demo_location_id}/menu-imports/{r.json()['id']}"
    imp = (await client.get(base, headers=OWNER)).json()
    lines = {d["name"]: [ln["ingredient"] for ln in d["lines"]] for d in imp["dishes"]}
    assert lines == {"Pho Tai": ["Rice noodles"], "Bun Bo Hue": ["Beef shank"], "Burrata & Tomato": ["Burrata"]}
    assert "1. Bun Bo Hue" in llm.calls[2]["messages"][0]["content"] and not imp["warnings"]

    # During the review: clear Pho Tai, edit Burrata, then ask again for Pho Tai only.
    dishes = imp["dishes"]
    dishes[0]["lines"] = []
    dishes[2]["lines"][0]["qty"] = 2
    await client.put(base, headers=OWNER, json={"dishes": dishes, "ingredients": imp["ingredients"]})
    fake_llm([tool("suggest_recipes", {"recipes": [
        {"n": 1, "dish": "Pho Tai", "lines": [{"ingredient": "Pho broth", "qty": 450, "unit": "ml"}]}]})])
    r = await client.post(f"{base}/suggest", headers=OWNER, json={"keys": [dishes[0]["key"]]})
    assert r.status_code == 200 and r.json()["status"] == "processing"
    await run_pending(time_budget_s=20)
    imp = (await client.get(base, headers=OWNER)).json()
    assert imp["status"] == "review"
    assert [ln["ingredient"] for ln in imp["dishes"][0]["lines"]] == ["Pho broth"]
    assert imp["dishes"][2]["lines"][0]["qty"] == 2  # the edit is kept
    assert imp["ingredients"]["pho broth"]["unit"] == "l"


async def test_an_empty_dish_never_wipes_a_recipe(client, session, demo_location_id, fake_llm):
    fake_llm([READ, RECIPES])
    r = await _upload(client, demo_location_id)
    await run_pending(time_budget_s=20)
    base = f"/locations/{demo_location_id}/menu-imports/{r.json()['id']}"
    imp = (await client.get(base, headers=OWNER)).json()
    dishes = imp["dishes"]
    dishes[2]["lines"] = []  # Burrata & Tomato, on the menu: ticked but emptied
    await client.put(base, headers=OWNER, json={"dishes": dishes, "ingredients": imp["ingredients"]})
    out = (await client.post(f"{base}/confirm", headers=OWNER)).json()
    assert out["recipes"] == 2 and "Burrata" not in out["items_created"]
