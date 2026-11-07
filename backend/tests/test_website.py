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
    added = [s for s in switched["sections"] if s["type"] == "cta"]  # Street features a call to action: added
    assert added and added[0]["visible"] is True
    hero = next(s for s in switched["sections"] if s["type"] == "hero")
    assert hero["props"]["layout"] == "overlay" and switched["sections"][0]["type"] == "hero"  # Street's look and order


def test_hours_and_slugs():
    rows = site_render.format_hours({"mon": [["11:30", "14:00"], ["17:00", "22:00"]], "sat": [["10:00", "23:00"]]})
    assert rows[0] == ("Monday", "11:30 AM – 2 PM, 5 PM – 10 PM") and rows[1] == ("Tuesday", "Closed")
    groups = site_render.hours_groups({k: [["11:30", "22:00"]] for k in ("mon", "tue", "wed", "thu", "fri")})
    assert groups == [("Mon – Fri", "11:30 AM – 10 PM"), ("Sat – Sun", "Closed")]
    assert st.slugify("Café Déjà Vu & Bar!") == "caf-d-j-vu-bar"
    assert st.SLUG.match("demo-bistro") and not st.SLUG.match("-bad") and not st.SLUG.match("Has Space")


# --- API -------------------------------------------------------------------------------------------------------


async def test_draft_preview_publish_and_live_data(client, session, demo_location_id):
    base = f"/locations/{demo_location_id}/website"
    site = (await client.get(base, headers=OWNER)).json()
    assert site["slug"] == "demo-bistro" and site["is_published"] is False
    assert {t["id"] for t in site["templates"]} >= {"gourmet", "trattoria", "zen", "bistro", "street", "night"}
    assert len(site["templates"]) == 13 and site["draft"]["template"] == "gourmet"
    assert site["menu_items"] and {"id", "name", "price"} <= set(site["menu_items"][0])
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


# --- Rich templates ----------------------------------------------------------------------------------------------


def _demo_site(**kw) -> site_render.SiteData:
    menu = [("Mains", [site_render.MenuItem("Steak Frites", "$34.00", "Hanger steak, fries.", ["gluten_free"], id="1" * 36),
                       site_render.MenuItem("Risotto", "$26.00", None, ["vegetarian"], id="2" * 36)]),
            ("Desserts", [site_render.MenuItem("Tiramisu", "$12.00", id="3" * 36)])]
    return site_render.SiteData(
        name="Demo", cuisine="Italian", address="1 Main St", phone="+16045550100", lat=49.2, lng=-123.1,
        hours={k: [["11:30", "22:00"]] for k in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}, menu=menu,
        best_sellers=["3" * 36, "1" * 36], reviews=[{"rating": 5, "author": "Ana", "text": "Lovely."},
                                                   {"rating": 4, "author": "Bo", "text": "Great pasta."}],
        rating=4.6, order_url="https://app.example/?order=L", book_url="https://app.example/?book=L",
        book_form_url="https://app.example/", location_id="L", today="2026-10-30", asset_base="/a", **kw)


def test_every_template_renders_with_and_without_photos():
    for name in st.TEMPLATES:
        content = st.clean_content(st.starter_content(name, "Demo", "Italian"))
        for preview in (False, True):
            html = site_render.render(content, _demo_site(preview=preview))
            assert f"t-{name}" in html and "<script" not in html.lower()
            assert "Steak Frites" in html  # menu or signature dishes from live data
        if any(s["type"] == "reserve" for s in content["sections"]):
            assert 'name="book" value="L"' in html and 'name="party"' in html and 'min="2026-10-30"' in html


def test_signature_dishes_use_picked_items_or_best_sellers():
    content = st.clean_content(st.starter_content("gourmet", "Demo"))
    html = site_render.render(content, _demo_site())
    dishes = html.split('id="dishes"')[1].split("</section>")[0]
    assert dishes.index("Tiramisu") < dishes.index("Steak Frites")  # best sellers first
    sec = next(s for s in content["sections"] if s["type"] == "dishes")
    sec["props"]["items"] = [{"item": "2" * 36, "image": None, "label": "New"}, {"item": "9" * 36}]
    dishes = site_render.render(st.clean_content(content), _demo_site()).split('id="dishes"')[1].split("</section>")[0]
    assert "Risotto" in dishes and "New" in dishes and "Tiramisu" not in dishes  # unknown dish skipped


def test_rich_fields_are_checked():
    raw = st.starter_content("gourmet", "Demo")
    feat = next(s for s in raw["sections"] if s["type"] == "features")
    feat["props"]["items"] = [{"icon": "<script>", "title": "x" * 99, "text": "ok", "extra": 1}] * 9
    feat["props"]["layout"] = "carousel"
    raw["layout"] = {"header": "floating", "logo": "not-an-id", "info_bar": "yes"}
    raw["theme"]["corners"] = "blob"
    raw["theme"]["script_font"] = "Wingdings"
    out = st.clean_content(raw)
    f = next(s for s in out["sections"] if s["type"] == "features")["props"]
    assert len(f["items"]) == 6 and f["items"][0] == {"icon": "leaf", "title": "x" * 40, "text": "ok"}
    assert f["layout"] == "icons"
    assert out["layout"]["header"] == "transparent" and out["layout"]["logo"] is None  # template defaults
    assert out["layout"]["info_bar"] is True
    assert out["theme"]["corners"] == "soft" and out["theme"]["script_font"] == "Great Vibes"
    content = {"layout": {"logo": "a" * 36}, "sections": [{"props": {"items": [{"image": "b" * 36}], "image2": "c" * 36}}]}
    assert st.image_ids(content) == {"a" * 36, "b" * 36, "c" * 36}


def test_old_content_still_renders():
    """Sites saved before the richer format (no layout, no dark colour) keep working."""
    old = {"template": "bistro", "theme": {"accent": "#b4532a", "background": "#f7f1e8", "text": "#2b211b",
                                         "heading_font": "Playfair Display", "body_font": "Source Sans 3"},
           "sections": [{"id": "hero-abcd1234", "type": "hero", "visible": True, "props": {"title": "Old"}}]}
    html = site_render.render(old, _demo_site())
    assert "Old" in html and "hero-split" in html


async def test_switching_template_through_the_api_adds_its_sections(client, demo_location_id):
    base = f"/locations/{demo_location_id}/website"
    await client.get(base, headers=OWNER)
    r = (await client.post(f"{base}/template", headers=OWNER, json={"template": "bistro", "start_fresh": True})).json()
    assert {s["type"] for s in r["draft"]["sections"]} == {"announcement", "hero", "about", "menu", "reviews",
                                                           "gallery", "visit"}
    r = (await client.post(f"{base}/template", headers=OWNER, json={"template": "zen"})).json()
    types = [s["type"] for s in r["draft"]["sections"]]
    assert "chef" in types and "reserve" in types and r["draft"]["layout"]["header_button"] == "book"
    preview = (await client.get(f"{base}/preview", headers=OWNER)).text
    assert "t-zen" in preview and 'id="reserve"' in preview


# --- The visual editor's markers -------------------------------------------------------------------------------


def test_edit_markers_only_in_the_editor():
    import re

    for name in st.TEMPLATES:
        content = st.clean_content(st.starter_content(name, "Demo", "Italian"))
        content["sections"][-1]["visible"] = False
        public = site_render.render(content, _demo_site())
        assert "data-e=" not in public and "data-sid" not in public and "edit-" not in public
        edit = site_render.render(content, _demo_site(preview=True, edit=True))
        ids = set(re.findall(r'data-sid="([^"]+)"', edit))
        assert ids == {s["id"] for s in content["sections"]}, name  # every section can be selected
        assert "Hidden: " in edit and 'data-add="end"' in edit and 'data-zone="header"' in edit
        assert 'data-zone="footer"' in edit and "Preview: only you" not in edit
        hero = next(s for s in content["sections"] if s["type"] == "hero")
        assert f'data-e="{hero["id"]}:title"' in edit and f'data-img="{hero["id"]}:image"' in edit
        # An edit preview of the public page isn't possible: the flag needs the private preview too.
        assert "data-e=" not in site_render.render(content, _demo_site(edit=True))


def test_edit_markers_point_at_real_fields():
    import re

    content = st.clean_content(st.starter_content("gourmet", "Demo"))
    html = site_render.render(content, _demo_site(preview=True, edit=True))
    by_id = {s["id"]: s for s in content["sections"]}
    for target in re.findall(r'data-(?:e|img|imgs)="([^"]+)"', html):
        where, path = target.split(":", 1)
        if where == "layout":
            assert path in st.LAYOUT_FIELDS
            continue
        fields = st.SECTIONS[by_id[where]["type"]]["fields"]
        top = path.split(".")[0]
        assert top in fields, target
        if "." in path:  # items.<n>.<field>
            _, n, sub = path.split(".")
            assert sub in fields[top]["fields"] and int(n) < len(by_id[where]["props"]["items"])
    features = next(s for s in content["sections"] if s["type"] == "features")
    assert f'data-e="{features["id"]}:items.0.title"' in html
    about = next(s for s in content["sections"] if s["type"] == "about")
    assert f'data-e="{about["id"]}:body" data-ml="1"' in html  # paragraphs edit as one block


async def test_preview_with_edit_markers(client, demo_location_id):
    base = f"/locations/{demo_location_id}/website"
    await client.get(base, headers=OWNER)
    plain = (await client.get(f"{base}/preview", headers=OWNER)).text
    edit = (await client.get(f"{base}/preview?edit=1", headers=OWNER)).text
    assert "data-e=" not in plain and "data-e=" in edit and "is-editing" in edit


# --- The separate menu page ------------------------------------------------------------------------------------

HOME = "https://api.example/sites/demo"


def _with_menu_page(template: str = "gourmet", **props) -> dict:
    content = st.clean_content(st.starter_content(template, "Demo", "Italian"))
    content["menu_page"].update({"enabled": True, **props})
    return st.clean_content(content)


def test_menu_page_settings_are_checked():
    out = st.clean_content({"template": "bistro", "menu_page": {
        "enabled": "yes", "heading": "", "intro": "<b>Fresh</b>" * 50, "layout": "carousel", "bg": "neon",
        "categories": ["Mains", 7, ""], "price_override": {"Steak Frites": 1}}})
    mp = out["menu_page"]
    assert mp["enabled"] is False and mp["heading"] == "Our menu"  # not a real yes; empty heading falls back
    assert len(mp["intro"]) == 300 and mp["layout"] == "columns" and mp["bg"] == "alt"
    assert mp["categories"] == ["Mains", "7"] and "price_override" not in mp  # only display settings are kept
    assert st.clean_content({"template": "bistro"})["menu_page"]["enabled"] is False  # old sites: off
    assert st.starter_content("bistro", "Demo")["menu_page"]["enabled"] is False


def test_menu_page_shows_the_live_menu_and_links_back_home():
    html = site_render.render(_with_menu_page(intro="Made fresh daily."), _demo_site(), HOME, page="menu")
    assert "<h1>Our menu</h1>" in html and "Made fresh daily." in html
    assert "Steak Frites" in html and "$34.00" in html and "Tiramisu" in html  # dishes and prices from the POS
    assert 'id="cat-mains"' in html and 'href="#cat-desserts"' in html  # jump links to each category
    assert f'<link rel="canonical" href="{HOME}/menu">' in html and "<title>Our menu | Demo</title>" in html
    assert f'href="{HOME}#visit"' in html and f'href="{HOME}#reserve"' in html  # back to the home page
    assert f'class="logo" href="{HOME}"' in html
    assert f'href="{HOME}/menu" aria-current=page>Menu</a>' in html
    assert "?order=L" in html and "data-e=" not in html and "edit-note" not in html
    assert "application/ld+json" not in html  # business details for Google are on the SEO plan
    pro = site_render.render(_with_menu_page(), _demo_site(seo_pro=True), HOME, page="menu")
    assert f'"url":"{HOME}/menu","hasMenuSection"' in pro and f'"url":"{HOME}",' in pro  # the menu; the restaurant


def test_home_page_links_to_the_menu_page_when_it_is_on():
    off = site_render.render(st.clean_content(st.starter_content("gourmet", "Demo")), _demo_site(), HOME)
    assert f"{HOME}/menu" not in off and 'href="#menu"' in off
    on = site_render.render(_with_menu_page(), _demo_site(), HOME)
    assert f'href="{HOME}/menu">Menu</a>' in on  # header
    assert f'href="{HOME}/menu">View the menu</a>' in on or f'href="{HOME}/menu">View full menu' in on
    assert f'href="{HOME}/menu#cat-mains">Mains</a>' in on  # footer
    assert 'id="menu"' in on  # the Menu section on the home page stays if the owner keeps it
    # A page with no Menu section still gets a "Menu" link when the menu page is on.
    content = _with_menu_page("minimal")
    content["sections"] = [s for s in content["sections"] if s["type"] != "menu"]
    assert f'href="{HOME}/menu">Menu</a>' in site_render.render(content, _demo_site(), HOME)


def test_menu_page_layouts_filters_and_editor_markers():
    tabs = site_render.render(_with_menu_page(layout="tabs"), _demo_site(), HOME, page="menu")
    assert 'class="tabs"' in tabs and 'class="mp-jump"' not in tabs  # tabs already list the categories
    only = site_render.render(_with_menu_page(categories=["Desserts"], show_prices=False), _demo_site(), HOME,
                              page="menu")
    assert "Tiramisu" in only and "Steak Frites" not in only and "$12.00" not in only
    edit = site_render.render(_with_menu_page(), _demo_site(preview=True, edit=True), HOME, page="menu")
    assert 'data-zone="menupage"' in edit and 'data-e="menu_page:heading"' in edit
    assert "edit the item in Stock" in edit  # tells the owner where dishes and prices are changed
    no_menu = _demo_site()
    no_menu.menu = []
    empty = site_render.render(_with_menu_page(), no_menu, HOME, page="menu")
    assert "Our menu will be here soon" in empty and "tel:+16045550100" in empty


async def test_menu_page_through_the_api(client, session, demo_location_id):
    base = f"/locations/{demo_location_id}/website"
    site = (await client.get(base, headers=OWNER)).json()
    assert {f["name"] for f in site["menu_page_fields"]} >= {"enabled", "heading", "layout", "categories"}
    # The owner can preview it before turning it on; visitors can't see it until it's on and published.
    preview = await client.get(f"{base}/preview?page=menu&edit=1", headers=OWNER)
    assert preview.status_code == 200 and "Spaghetti Carbonara" in preview.text and "menu_page:heading" in preview.text
    assert (await client.get(f"{base}/preview?page=admin", headers=OWNER)).status_code == 422
    await client.post(f"{base}/publish", headers=OWNER)
    off = await client.get("/sites/demo-bistro/menu", follow_redirects=False)
    assert off.status_code == 302 and off.headers["location"].endswith("/sites/demo-bistro")

    draft = site["draft"]
    draft["menu_page"] = {**draft.get("menu_page", {}), "enabled": True, "intro": "Pasta made every morning."}
    saved = (await client.put(f"{base}/draft", headers=OWNER, json={"content": draft})).json()
    assert saved["draft"]["menu_page"]["enabled"] is True and saved["has_unpublished_changes"]
    assert (await client.get("/sites/demo-bistro/menu", follow_redirects=False)).status_code == 302  # not yet live
    await client.post(f"{base}/publish", headers=OWNER)
    live = await client.get("/sites/demo-bistro/menu")
    assert live.status_code == 200 and "Pasta made every morning." in live.text and "Spaghetti Carbonara" in live.text
    assert "/sites/demo-bistro/menu" in (await client.get("/sites/demo-bistro")).text  # the home page links to it
    assert "/sites/demo-bistro/menu" in (await client.get("/sites/demo-bistro/sitemap.xml")).text

    # Option 1: the menu itself is changed in the POS / Stock, and the page shows it without republishing.
    item = await session.scalar(select(Item).where(Item.location_id == demo_location_id,
                                                   Item.name == "Spaghetti Carbonara"))
    item.price_cents = 2950
    await session.commit()
    assert "$29.50" in (await client.get("/sites/demo-bistro/menu")).text

    # On the restaurant's own domain it's at /menu; unknown hosts and slugs get nothing.
    await client.put(f"{base}/settings", headers=OWNER, json={"custom_domain": "www.demobistro.ca"})
    own = await client.get("/menu", headers={"host": "www.demobistro.ca"})
    assert own.status_code == 200 and "https://www.demobistro.ca/menu" in own.text
    assert (await client.get("/menu", headers={"host": "unknown.example"})).status_code == 404
    assert (await client.get("/sites/nobody-here/menu")).status_code == 404
