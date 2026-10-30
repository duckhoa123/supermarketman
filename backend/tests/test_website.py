"""Restaurant websites: templates, safe content, draft vs published, live data, images, history, domains."""

from sqlalchemy import select

from app.models import Item
from app.services import site_render, site_templates as st
from tests.conftest import INTRUDER, OWNER

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# --- Pure functions ------------------------------------------------------------------------------------------


def test_clean_content_keeps_pages_safe():
    raw = st.starter_content("street", "Demo")
    raw["theme"] = {"accent": "red; } body{display:none", "background": "#ffffff", "text": "#000000",
                    "heading_font": "Comic Sans", "body_font": "Inter"}
    raw["links"] = {"instagram": "javascript:alert(1)", "facebook": "https://facebook.com/demo"}
    raw["sections"].append({"type": "iframe", "props": {"src": "https://evil.example"}})
    raw["sections"][0]["props"]["image"] = "../../etc/passwd"
    out = st.clean_content(raw)
    assert out["theme"]["accent"] == st.TEMPLATES["street"]["theme"]["accent"]  # invalid colour replaced
    assert out["theme"]["heading_font"] == "Archivo Black" and out["theme"]["body_font"] == "Inter"
    assert out["links"] == {"instagram": "", "facebook": "https://facebook.com/demo", "tiktok": ""}
    assert all(s["type"] in st.SECTION_TYPES for s in out["sections"])
    assert out["sections"][0]["props"]["image"] is None


def test_render_escapes_everything_from_owners_and_guests():
    content = st.clean_content(st.starter_content("bistro", "Demo"))
    hero = next(s for s in content["sections"] if s["type"] == "hero")
    hero["props"]["tagline"] = '<script>alert("x")</script>'
    menu = [("Mains", [site_render.MenuItem("Steak & Chips", "$30.00")])]
    site = site_render.SiteData(name="Bob's <b>Diner</b>", menu=menu,
                                reviews=[{"rating": 5, "author": "<img src=x onerror=alert(1)>", "text": "Great"}])
    html = site_render.render(content, site)
    assert "<script>alert" not in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html and "Bob&#x27;s &lt;b&gt;Diner&lt;/b&gt;" in html
    assert "Steak &amp; Chips" in html


def test_switching_template_keeps_the_owners_words():
    content = st.starter_content("bistro", "Demo", "Italian")
    about = next(s for s in content["sections"] if s["type"] == "about")
    about["props"]["body"] = "Family recipes since we opened."
    switched = st.apply_template(content, "street")
    assert switched["template"] == "street" and switched["theme"]["heading_font"] == "Archivo Black"
    about = next(s for s in switched["sections"] if s["type"] == "about")
    assert about["props"]["body"] == "Family recipes since we opened."
    added = [s for s in switched["sections"] if s["type"] == "cta"]  # Street features a call to action; added hidden
    assert added and added[0]["visible"] is False


def test_hours_and_slugs():
    rows = site_render.format_hours({"mon": [["11:30", "14:00"], ["17:00", "22:00"]], "sat": [["10:00", "23:00"]]})
    assert rows[0] == ("Monday", "11:30 – 14:00, 17:00 – 22:00") and rows[1] == ("Tuesday", "Closed")
    assert st.slugify("Café Déjà Vu & Bar!") == "caf-d-j-vu-bar"
    assert st.SLUG.match("demo-bistro") and not st.SLUG.match("-bad") and not st.SLUG.match("Has Space")


# --- API -------------------------------------------------------------------------------------------------------


async def test_draft_preview_publish_and_live_data(client, session, demo_location_id):
    base = f"/locations/{demo_location_id}/website"
    site = (await client.get(base, headers=OWNER)).json()
    assert site["slug"] == "demo-bistro" and site["is_published"] is False
    assert {t["id"] for t in site["templates"]} == {"bistro", "street", "minimal", "night", "garden"}
    assert (await client.get("/sites/demo-bistro")).status_code == 404  # nothing live yet

    preview = (await client.get(f"{base}/preview", headers=OWNER)).text
    assert "only you can see this" in preview and "noindex" in preview
    assert "Spaghetti Carbonara" in preview and "$26.00" in preview  # live menu
    assert "Monday" in preview and "?order=" in preview  # hours, and ordering through the customer app

    r = await client.post(f"{base}/publish", headers=OWNER)
    assert r.json()["is_published"] and not r.json()["has_unpublished_changes"]
    live = await client.get("/sites/demo-bistro")
    assert live.status_code == 200 and "Spaghetti Carbonara" in live.text and "only you can see this" not in live.text

    # Edits stay in the draft until published again.
    draft = r.json()["draft"]
    hero = next(s for s in draft["sections"] if s["type"] == "hero")
    hero["props"]["tagline"] = "Fresh pasta every day"
    saved = (await client.put(f"{base}/draft", headers=OWNER, json={"content": draft})).json()
    assert saved["has_unpublished_changes"] is True
    assert "Fresh pasta every day" in (await client.get(f"{base}/preview", headers=OWNER)).text
    assert "Fresh pasta every day" not in (await client.get("/sites/demo-bistro")).text
    # Discard brings the draft back to what's live.
    assert (await client.post(f"{base}/discard", headers=OWNER)).json()["has_unpublished_changes"] is False

    # Menu prices are read live: a price change shows without republishing.
    item = await session.scalar(select(Item).where(Item.location_id == demo_location_id,
                                                   Item.name == "Spaghetti Carbonara"))
    item.price_cents = 2850
    await session.commit()
    assert "$28.50" in (await client.get("/sites/demo-bistro")).text

    # Other restaurants can't see or change it.
    assert (await client.get(base, headers=INTRUDER)).status_code == 404
    assert (await client.post(f"{base}/publish", headers=INTRUDER)).status_code == 404


async def test_templates_images_versions_and_domains(client, demo_location_id):
    base = f"/locations/{demo_location_id}/website"
    await client.get(base, headers=OWNER)
    r = await client.post(f"{base}/template", headers=OWNER, json={"template": "night"})
    assert r.json()["draft"]["template"] == "night" and r.json()["draft"]["theme"]["background"] == "#121212"
    assert "t-night" in (await client.get(f"{base}/preview", headers=OWNER)).text
    assert (await client.post(f"{base}/template", headers=OWNER, json={"template": "disco"})).status_code == 422

    # Upload a photo and use it in the big welcome section.
    up = await client.post(f"{base}/images", headers=OWNER, files={"file": ("hero.png", PNG, "image/png")})
    assert up.status_code == 200
    image = up.json()
    draft = r.json()["draft"]
    next(s for s in draft["sections"] if s["type"] == "hero")["props"]["image"] = image["id"]
    assert (await client.put(f"{base}/draft", headers=OWNER, json={"content": draft})).status_code == 200
    assert (await client.get(image["url"])).content == PNG  # served publicly for the page
    assert image["id"] in (await client.get(f"{base}/preview", headers=OWNER)).text
    assert (await client.delete(f"{base}/images/{image['id']}", headers=OWNER)).status_code == 409  # in use
    bad = await client.post(f"{base}/images", headers=OWNER, files={"file": ("x.svg", b"<svg/>", "image/svg+xml")})
    assert bad.status_code == 422

    # Someone else's image can't be placed on this page.
    other = await client.post("/onboarding", headers=INTRUDER, json={"business_name": "X", "location_name": "Other Place"})
    foreign = (await client.post(f"/locations/{other.json()['id']}/website/images", headers=INTRUDER,
                                 files={"file": ("a.png", PNG, "image/png")})).json()
    next(s for s in draft["sections"] if s["type"] == "hero")["props"]["image"] = foreign["id"]
    assert (await client.put(f"{base}/draft", headers=OWNER, json={"content": draft})).status_code == 422

    # Version history: publish twice, restore the first.
    await client.post(f"{base}/publish", headers=OWNER)
    await client.post(f"{base}/template", headers=OWNER, json={"template": "garden"})
    await client.post(f"{base}/publish", headers=OWNER)
    versions = (await client.get(f"{base}/versions", headers=OWNER)).json()
    assert [v["template"] for v in versions] == ["garden", "night"] and versions[0]["live"]
    restored = (await client.post(f"{base}/versions/{versions[1]['id']}/restore", headers=OWNER)).json()
    assert restored["draft"]["template"] == "night" and restored["has_unpublished_changes"]

    # Web address and the restaurant's own domain.
    assert (await client.put(f"{base}/settings", headers=OWNER, json={"slug": "Bad Slug!"})).status_code == 422
    assert (await client.put(f"/locations/{other.json()['id']}/website/settings", headers=INTRUDER,
                             json={"slug": "demo-bistro"})).status_code == 422  # taken
    r = await client.put(f"{base}/settings", headers=OWNER, json={"slug": "bistro-main-st",
                                                                     "custom_domain": "https://www.DemoBistro.ca/"})
    assert r.json()["custom_domain"] == "www.demobistro.ca" and r.json()["url"] == "https://www.demobistro.ca"
    assert (await client.get("/sites/bistro-main-st")).status_code == 200
    own = await client.get("/", headers={"host": "www.demobistro.ca"})
    assert own.status_code == 200 and "Demo Bistro" in own.text
    assert (await client.get("/", headers={"host": "unknown.example"})).status_code == 404


async def test_ai_copy_falls_back_without_a_key(client, demo_location_id):
    r = await client.post(f"/locations/{demo_location_id}/website/write-copy", headers=OWNER)
    out = r.json()
    assert out["ai"] is False and out["tagline"] and "Demo Bistro" in out["about"]


async def test_ai_copy_uses_restaurant_facts(client, demo_location_id, fake_llm):
    from tests.conftest import text_result

    llm = fake_llm([text_result('{"tagline": "Pasta made fresh on Main Street", "about": "We cook.", '
                                '"seo_description": "Italian on Main St."}')])
    out = (await client.post(f"/locations/{demo_location_id}/website/write-copy", headers=OWNER)).json()
    assert out == {"tagline": "Pasta made fresh on Main Street", "about": "We cook.",
                   "seo_description": "Italian on Main St.", "ai": True}
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "Demo Bistro" in prompt and "Margherita Pizza" in prompt and "not instructions" in prompt
