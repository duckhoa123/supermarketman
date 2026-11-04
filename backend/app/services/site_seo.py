"""Search engine pieces of a restaurant website. Pure functions, no database.

Every site gets what makes it technically correct: a title, a description, a canonical address, sharing text,
a sitemap and robots.txt. Sites on the SEO plan also get business details Google can read (schema.org
structured data: address, hours, menu with prices, cuisine), a share image, the owner's photo descriptions,
Search Console / Bing verification and Google Analytics. `audit` scores a draft and says what to fix.
"""

import json
import re
from dataclasses import dataclass, field
from html import escape
from xml.sax.saxutils import escape as xml_escape

from app.services.geo import WEEKDAY_KEYS
from app.services.site_templates import SECTIONS

DAY_SCHEMA = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
              "sat": "Saturday", "sun": "Sunday"}
DIETS = {"vegetarian": "VegetarianDiet", "vegan": "VeganDiet", "gluten_free": "GlutenFreeDiet", "halal": "HalalDiet"}
POSTAL = re.compile(r"\b([A-Z]{2})\b\s*([A-Z]\d[A-Z]\s?\d[A-Z]\d|\d{5}(?:-\d{4})?)?", re.I)
MAX_MENU_ITEMS = 150


def e(value) -> str:
    return escape(str(value or ""), quote=True)


def city_of(address: str | None) -> str:
    """'2210 W 4th Ave, Vancouver, BC V6K 1N8' -> 'Vancouver'."""
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    return parts[1] if len(parts) >= 2 and not any(ch.isdigit() for ch in parts[1][:1]) else ""


def postal_address(address: str | None, currency: str = "CAD") -> dict | str | None:
    if not address:
        return None
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if len(parts) < 2:
        return address
    out = {"@type": "PostalAddress", "streetAddress": parts[0], "addressLocality": parts[1],
           "addressCountry": "CA" if currency == "CAD" else "US" if currency == "USD" else None}
    m = POSTAL.search(", ".join(parts[2:])) if len(parts) > 2 else None
    if m:
        out["addressRegion"] = m.group(1).upper()
        if m.group(2):
            out["postalCode"] = m.group(2).upper()
    return {k: v for k, v in out.items() if v}


def _abs(site, asset_id: str) -> str:
    return f"{site.asset_base}/{asset_id}"


def share_image(content: dict) -> str | None:
    """The photo shown when the site is shared on Facebook, WhatsApp or iMessage: the owner's pick, else the
    first big photo on the page."""
    seo = content.get("seo") or {}
    if seo.get("share_image"):
        return seo["share_image"]
    for s in content.get("sections", []):
        if s.get("visible") and s["props"].get("image") and s["type"] in ("hero", "about", "occasions", "cta", "chef"):
            return s["props"]["image"]
    return None


def opening_hours(hours: dict) -> list[dict]:
    out = []
    for k in WEEKDAY_KEYS:
        for opens, closes in hours.get(k) or []:
            out.append({"@type": "OpeningHoursSpecification", "dayOfWeek": DAY_SCHEMA[k], "opens": opens,
                        "closes": closes})
    return out


def structured_data(content: dict, site, page_url: str, anchors: set[str]) -> dict:
    """schema.org Restaurant, built only from what the page itself shows (Google's rule for structured data)."""
    seo = content.get("seo") or {}
    links = [v for v in (content.get("links") or {}).values() if v]
    data: dict = {"@context": "https://schema.org", "@type": "Restaurant", "name": site.name}
    if page_url:
        data["url"] = page_url
    if seo.get("description"):
        data["description"] = seo["description"]
    if site.cuisine:
        data["servesCuisine"] = site.cuisine
    if seo.get("price_range"):
        data["priceRange"] = seo["price_range"]
    if site.phone:
        data["telephone"] = site.phone
    addr = postal_address(site.address, site.currency)
    if addr:
        data["address"] = addr
    if site.lat is not None and site.lng is not None:
        data["geo"] = {"@type": "GeoCoordinates", "latitude": site.lat, "longitude": site.lng}
    if seo.get("area"):
        data["areaServed"] = seo["area"]
    if site.hours:
        data["openingHoursSpecification"] = opening_hours(site.hours)
    logo = (content.get("layout") or {}).get("logo")
    images = [_abs(site, i) for i in dict.fromkeys(filter(None, [share_image(content), logo]))]
    if images:
        data["image"] = images
    if logo:
        data["logo"] = _abs(site, logo)
    if links:
        data["sameAs"] = links
    if "menu" in anchors and site.menu:
        sections, n = [], 0
        for cat, items in site.menu:
            rows = []
            for it in items:
                if n >= MAX_MENU_ITEMS:
                    break
                n += 1
                row = {"@type": "MenuItem", "name": it.name}
                if it.description:
                    row["description"] = it.description
                if it.price_value:
                    row["offers"] = {"@type": "Offer", "price": it.price_value, "priceCurrency": site.currency}
                diets = [f"https://schema.org/{DIETS[t]}" for t in it.tags if t in DIETS]
                if diets:
                    row["suitableForDiet"] = diets
                rows.append(row)
            if rows:
                sections.append({"@type": "MenuSection", "name": cat, "hasMenuItem": rows})
        data["hasMenu"] = {"@type": "Menu", "url": f"{page_url}#menu" if page_url else "#menu",
                           "hasMenuSection": sections}
    data["acceptsReservations"] = bool(site.book_url)
    actions = []
    if site.order_url:
        actions.append({"@type": "OrderAction", "target": site.order_url})
    if site.book_url:
        actions.append({"@type": "ReserveAction", "target": site.book_url})
    if actions:
        data["potentialAction"] = actions
    return data


def _json_script(data: dict) -> str:
    # "<" escaped so nothing in the data can close the script tag.
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return f'<script type="application/ld+json">{body}</script>'


def head_tags(content: dict, site, page_url: str, anchors: set[str], title: str, desc: str) -> str:
    """Everything search engines and social apps read from the <head>."""
    seo = content.get("seo") or {}
    tags = []
    if site.preview or not seo.get("index", True):
        tags.append('<meta name="robots" content="noindex">')
    if page_url and not site.preview:
        tags.append(f'<link rel="canonical" href="{e(page_url)}">')
    tags += [f'<meta property="og:type" content="website"><meta property="og:site_name" content="{e(site.name)}">',
             f'<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">',
             '<meta property="og:locale" content="en_CA">' if site.currency == "CAD" else ""]
    if page_url:
        tags.append(f'<meta property="og:url" content="{e(page_url)}">')
    img = share_image(content) if site.seo_pro else None
    if img:
        tags.append(f'<meta property="og:image" content="{e(_abs(site, img))}"><meta name="twitter:card" '
                    f'content="summary_large_image"><meta name="twitter:image" content="{e(_abs(site, img))}">')
    else:
        tags.append('<meta name="twitter:card" content="summary">')
    if not site.seo_pro:
        return "".join(tags)
    if not site.preview:
        if seo.get("google_verify"):
            tags.append(f'<meta name="google-site-verification" content="{e(seo["google_verify"])}">')
        if seo.get("bing_verify"):
            tags.append(f'<meta name="msvalidate.01" content="{e(seo["bing_verify"])}">')
    tags.append(_json_script(structured_data(content, site, page_url, anchors)))
    if seo.get("ga_id") and not site.preview:  # the only script a site can have, and only when the owner asks
        ga = e(seo["ga_id"])
        tags.append(f'<script async src="https://www.googletagmanager.com/gtag/js?id={ga}"></script><script>'
                    f"window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}"
                    f"gtag('js',new Date());gtag('config','{ga}');</script>")
    return "".join(tags)


# --- Sitemap and robots.txt ----------------------------------------------------------------------------------


@dataclass
class SitemapEntry:
    loc: str
    lastmod: str | None = None  # YYYY-MM-DD
    images: list[str] = field(default_factory=list)


def sitemap(entries: list[SitemapEntry]) -> str:
    rows = []
    for en in entries:
        imgs = "".join(f"<image:image><image:loc>{xml_escape(u)}</image:loc></image:image>" for u in en.images[:100])
        mod = f"<lastmod>{en.lastmod}</lastmod>" if en.lastmod else ""
        rows.append(f"<url><loc>{xml_escape(en.loc)}</loc>{mod}{imgs}</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">' + "".join(rows) + "</urlset>\n")


def robots(sitemap_url: str, only_sites: bool) -> str:
    """only_sites: this host also serves the app's API, so only the websites under /sites/ are crawlable."""
    rules = "User-agent: *\nAllow: /sites/\nDisallow: /\n" if only_sites else "User-agent: *\nAllow: /\n"
    return f"{rules}\nSitemap: {sitemap_url}\n"


# --- Photos on the page ------------------------------------------------------------------------------------------


def page_photos(content: dict, site) -> list[dict]:
    """Every photo shown on the page, where it is, and the description it gets (the owner's or an automatic one).
    `needs_alt`: the automatic one says nothing about the photo, so search engines and screen readers learn
    nothing from it."""
    alts = (content.get("seo") or {}).get("alts") or {}
    out: dict[str, dict] = {}
    generic = {"", site.name, f"{site.name} photo"}

    def add(asset_id, where, auto):
        if not asset_id or asset_id in out:
            return
        out[asset_id] = {"id": asset_id, "where": where, "auto_alt": auto, "alt": alts.get(asset_id, ""),
                         "needs_alt": not alts.get(asset_id) and auto in generic}

    add((content.get("layout") or {}).get("logo"), "Logo", site.name)
    for s in content.get("sections", []):
        if not s.get("visible"):
            continue
        p, kind = s["props"], s["type"]
        label = SECTIONS[kind]["label"]
        if kind == "hero":
            add(p.get("image"), label, p.get("title") or site.name)
        elif kind == "about":
            add(p.get("image"), label, p.get("heading") or site.name)
            add(p.get("image2"), label, site.name)
        elif kind == "chef":
            add(p.get("image"), label, p.get("name") or "Our chef")
        elif kind == "dishes":
            for row in p.get("items") or []:
                it = site.item(row.get("item")) if isinstance(row, dict) else None
                add(row.get("image") if isinstance(row, dict) else None, label, it.name if it else "")
        else:
            add(p.get("image"), label, "")
            add(p.get("image2"), label, "")
            for i in p.get("images") or []:
                add(i, label, f"{site.name} photo")
            for row in p.get("items") or []:
                if isinstance(row, dict):
                    add(row.get("image"), label, row.get("title") or "")
    share = (content.get("seo") or {}).get("share_image")
    if share and share not in out:
        out[share] = {"id": share, "where": "Share image", "auto_alt": "", "alt": alts.get(share, ""),
                      "needs_alt": False}
    return list(out.values())


# --- Health check ---------------------------------------------------------------------------------------------


@dataclass
class Facts:
    """What the check needs beyond the page itself."""
    published: bool = False
    custom_domain: bool = False
    google_profile: bool = False
    big_photos: int = 0  # photos over 1 MB, which slow the page on phones


def _page_text(content: dict, site) -> str:
    bits = [site.name, site.cuisine or "", site.address or ""]
    for s in content.get("sections", []):
        if not s.get("visible"):
            continue
        for v in s["props"].values():
            if isinstance(v, str) and len(v) > 1 and not re.match(r"^[0-9a-f-]{36}$", v):
                bits.append(v)
            elif isinstance(v, list):
                bits += [str(x) for row in v if isinstance(row, dict) for x in row.values() if isinstance(x, str)]
    bits += [it.name for _, items in site.menu for it in items]
    bits += [c for c, _ in site.menu]
    return " ".join(bits).lower()


def _has(text: str, phrase: str) -> bool:
    words = [w for w in re.split(r"\W+", phrase.lower()) if w]
    return bool(words) and all(w in text for w in words)


def keyword_coverage(content: dict, site) -> list[dict]:
    seo = content.get("seo") or {}
    title = (seo.get("title") or site.name).lower()
    desc = (seo.get("description") or "").lower()
    hero = next((s for s in content.get("sections", []) if s["type"] == "hero" and s.get("visible")), None)
    headline = " ".join(str(hero["props"].get(k) or "") for k in ("title", "tagline", "eyebrow")).lower() if hero else ""
    page = _page_text(content, site)
    return [{"phrase": k, "title": _has(title, k), "description": _has(desc, k), "headline": _has(headline, k),
             "page": _has(page, k)} for k in seo.get("keywords") or []]


def _check(cid, label, status, detail, fix=None, weight=1, pro=False) -> dict:
    return {"id": cid, "label": label, "status": status, "detail": detail, "fix": fix, "weight": weight, "pro": pro}


def audit(content: dict, site, facts: Facts) -> dict:
    """Score the page out of 100 and list what to fix, most important first."""
    seo = content.get("seo") or {}
    title, desc = seo.get("title") or "", seo.get("description") or ""
    city = city_of(site.address)
    area = seo.get("area") or ""
    vis = {s["type"]: s for s in content.get("sections", []) if s.get("visible")}
    pro = site.seo_pro
    checks = []

    if not facts.published:
        checks.append(_check("published", "Your website is live", "bad", "Google can only find it once you publish.",
                             "publish", 3))
    elif not seo.get("index", True):
        checks.append(_check("published", "Search engines can find your site", "bad",
                             "You've asked search engines not to show your site.", "search", 3))
    else:
        checks.append(_check("published", "Your website is live", "good", "Published and open to search engines.",
                             None, 3))

    n = len(title)
    if not title:
        checks.append(_check("title", "Page title", "bad", "Add a title: it's the blue link in Google.", "google", 3))
    elif n < 25 or n > 60:
        checks.append(_check("title", "Page title", "warn",
                             f"{n} characters. Aim for 30–60 so Google shows all of it.", "google", 3))
    else:
        checks.append(_check("title", "Page title", "good", f"{n} characters, a good length.", None, 3))

    n = len(desc)
    if not desc:
        checks.append(_check("description", "Search description", "bad",
                             "Add the short text Google shows under your name.", "google", 2))
    elif n < 70:
        checks.append(_check("description", "Search description", "warn",
                             f"{n} characters. Aim for 120–160 to say what makes you worth a visit.", "google", 2))
    else:
        checks.append(_check("description", "Search description", "good", f"{n} characters.", None, 2))

    place = [p for p in (area, city) if p]
    head = f"{title} {desc}".lower()
    if place and any(p.lower() in head for p in place):
        checks.append(_check("local", "Says where you are", "good", f"Mentions {place[0]} in your title or description.",
                             None, 2))
    else:
        where = place[0] if place else "your city or neighbourhood"
        checks.append(_check("local", "Says where you are", "warn",
                             f"Put {where} in your title or description: people search “… near {where}”.", "google", 2))

    if site.cuisine and site.cuisine.lower() in f"{head} {_page_text(content, site)[:2000]}":
        checks.append(_check("cuisine", "Says what food you serve", "good", f"Mentions {site.cuisine}.", None, 1))
    else:
        checks.append(_check("cuisine", "Says what food you serve", "warn",
                             "Name your cuisine (e.g. “Italian”, “ramen”) in the title or headline.", "google", 1))

    hero = vis.get("hero")
    checks.append(_check("headline", "Main headline", "good" if hero and hero["props"].get("title") else "bad",
                         "Your top banner has a headline." if hero and hero["props"].get("title")
                         else "Add a top banner with a headline: search engines read it first.", "section:hero", 1))

    if site.address and site.phone:
        status = "good" if "visit" in vis else "warn"
        checks.append(_check("nap", "Address and phone on the page", status,
                             "Shown in “Visit us”." if status == "good"
                             else "Add a “Visit us” section so the address, hours and map are on the page.",
                             None if status == "good" else "section:visit", 2))
    else:
        checks.append(_check("nap", "Address and phone on the page", "bad",
                             "Add your address and phone number in Pass settings.", "restaurant", 2))

    checks.append(_check("hours", "Opening hours", "good" if site.hours else "bad",
                         "Your hours are on the page." if site.hours else "Add your opening hours in Pass settings.",
                         None if site.hours else "restaurant", 1))

    items = sum(len(i) for _, i in site.menu)
    if "menu" in vis and items:
        checks.append(_check("menu", "Menu Google can read", "good", f"{items} dishes listed as text.", None, 2))
    else:
        checks.append(_check("menu", "Menu Google can read", "warn",
                             "Add the Menu section: a text menu ranks far better than a photo or PDF of one.",
                             "section:menu", 2))

    photos = page_photos(content, site)
    missing = [p for p in photos if p["needs_alt"]]
    if not photos:
        checks.append(_check("photos", "Photos", "warn", "Add a few photos of your food and room.", "photos", 1))
    elif missing:
        checks.append(_check("alt", "Photo descriptions", "warn",
                             f"{len(missing)} of {len(photos)} photos have no description for Google Images.",
                             "alt", 1, pro=True))
    else:
        checks.append(_check("alt", "Photo descriptions", "good", "Every photo is described.", None, 1, pro=True))

    if facts.big_photos:
        checks.append(_check("speed", "Fast on phones", "warn",
                             f"{facts.big_photos} photo{'s are' if facts.big_photos > 1 else ' is'} over 1 MB. "
                             "Upload smaller versions to load faster.", "photos", 1))
    else:
        checks.append(_check("speed", "Fast on phones", "good", "Light page, no scripts, small photos.", None, 1))

    checks.append(_check("schema", "Business details for Google", "good" if pro else "bad",
                         "Address, hours, menu and prices are sent to Google as structured data." if pro
                         else "Send Google your address, hours, menu and prices in the format it reads.",
                         None if pro else "upgrade", 2, pro=True))

    has_share = bool(share_image(content))
    checks.append(_check("share", "Share picture", "good" if pro and has_share else "warn",
                         "Links shared on Facebook, WhatsApp and iMessage show a photo." if pro and has_share
                         else "Show a photo when people share your link.", None if pro and has_share else "share", 1,
                         pro=True))

    cov = keyword_coverage(content, site)
    if not cov:
        checks.append(_check("keywords", "Search phrases", "warn",
                             "Pick 2–5 phrases you want to be found for, like “pizza kitsilano”.", "keywords", 2,
                             pro=True))
    else:
        weak = [c["phrase"] for c in cov if not (c["title"] or c["headline"] or c["description"])]
        checks.append(_check("keywords", "Search phrases", "warn" if weak else "good",
                             f"Not in your title, headline or description yet: {', '.join(weak[:3])}." if weak
                             else f"All {len(cov)} phrases appear where Google looks first.",
                             "keywords" if weak else None, 2, pro=True))

    links = [v for v in (content.get("links") or {}).values() if v]
    checks.append(_check("social", "Social profiles linked", "good" if links else "warn",
                         f"{len(links)} linked." if links else "Link your Instagram or Facebook page.",
                         None if links else "zone:footer", 1))

    checks.append(_check("google_profile", "Google Business Profile", "good" if facts.google_profile else "warn",
                         "Connected: your reviews and details stay in sync." if facts.google_profile
                         else "Connect it in Pass settings: it's what shows on Google Maps.",
                         None if facts.google_profile else "integrations", 2))

    verified = bool(seo.get("google_verify"))
    checks.append(_check("console", "Google Search Console", "good" if pro and verified else "warn",
                         "Verified: you can see what people searched to find you." if pro and verified
                         else "Verify your site to see the searches that bring guests.", None if pro and verified
                         else "search", 1, pro=True))

    checks.append(_check("domain", "Your own domain", "good" if facts.custom_domain else "warn",
                         "Connected." if facts.custom_domain else "A domain like yourrestaurant.ca is easier to "
                         "remember and to trust.", None if facts.custom_domain else "domain", 1))

    value = {"good": 1.0, "warn": 0.5, "bad": 0.0}
    total = sum(c["weight"] for c in checks)
    score = round(100 * sum(c["weight"] * value[c["status"]] for c in checks) / total)
    order = {"bad": 0, "warn": 1, "good": 2}
    checks.sort(key=lambda c: (order[c["status"]], -c["weight"]))
    return {"score": score, "checks": checks, "keywords": cov, "photos": photos}
