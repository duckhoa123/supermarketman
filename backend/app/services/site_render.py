"""Render a restaurant website to a single HTML page. Pure functions, no database.

Every value from the owner or the database is HTML-escaped. Styles come only from the template and the
validated theme (hex colours, whitelisted fonts), so a site can't carry scripts or arbitrary CSS.
"""

from dataclasses import dataclass, field
from html import escape
from urllib.parse import quote

from app.services.geo import WEEKDAY_KEYS
from app.services.site_templates import FONTS

DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
             "sat": "Saturday", "sun": "Sunday"}
TAG_LABELS = {"vegetarian": "V", "vegan": "VG", "gluten_free": "GF", "pescatarian": "P", "halal": "H",
              "nut_free": "NF", "dairy_free": "DF"}


@dataclass
class MenuItem:
    name: str
    price: str
    description: str | None = None
    tags: list[str] = field(default_factory=list)


@dataclass
class SiteData:
    """The restaurant's live data, as of the moment the page is served."""
    name: str
    cuisine: str | None = None
    address: str | None = None
    phone: str | None = None
    lat: float | None = None
    lng: float | None = None
    hours: dict = field(default_factory=dict)
    menu: list[tuple[str, list[MenuItem]]] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)  # {"rating", "author", "text"}
    rating: float | None = None
    order_url: str | None = None
    book_url: str | None = None
    asset_base: str = ""  # images are served at {asset_base}/{asset_id}
    preview: bool = False


def e(value) -> str:
    return escape(str(value or ""), quote=True)


def _paragraphs(text: str) -> str:
    return "".join(f"<p>{e(p)}</p>" for p in (text or "").split("\n\n") if p.strip())


def _img(site: SiteData, asset_id: str | None, alt: str, cls: str = "") -> str:
    if not asset_id:
        return ""
    return f'<img class="{cls}" src="{e(site.asset_base)}/{e(asset_id)}" alt="{e(alt)}" loading="lazy">'


def format_hours(hours: dict) -> list[tuple[str, str]]:
    """[("Monday", "11:30 – 22:00"), ("Tuesday", "Closed"), ...] in week order."""
    out = []
    for key in WEEKDAY_KEYS:
        ranges = hours.get(key) or []
        out.append((DAY_NAMES[key], ", ".join(f"{a} – {b}" for a, b in ranges) if ranges else "Closed"))
    return out


def _buttons(site: SiteData, order: bool, book: bool, cls: str = "") -> str:
    parts = []
    if order and site.order_url:
        parts.append(f'<a class="btn btn-main {cls}" href="{e(site.order_url)}">Order online</a>')
    if book and site.book_url:
        parts.append(f'<a class="btn btn-alt {cls}" href="{e(site.book_url)}">Book a table</a>')
    if not parts and site.phone:
        parts.append(f'<a class="btn btn-main {cls}" href="tel:{e(site.phone)}">Call {e(site.phone)}</a>')
    return f'<div class="actions">{"".join(parts)}</div>' if parts else ""


# --- Sections ----------------------------------------------------------------------------------------------


def s_hero(p: dict, site: SiteData, template: str) -> str:
    title = p.get("title") or site.name
    img = _img(site, p.get("image"), title, "hero-img")
    text = (f'<div class="hero-text"><p class="eyebrow">{e(site.cuisine or "")}</p><h1>{e(title)}</h1>'
            f'<p class="tagline">{e(p.get("tagline"))}</p>'
            f'{_buttons(site, p.get("order_button", True), p.get("booking_button", True))}</div>')
    style = "overlay" if template in ("street", "night") and img else "split" if img else "plain"
    return f'<section class="hero hero-{style}">{img if style != "plain" else ""}{text}</section>'


def s_announcement(p: dict, site: SiteData, template: str) -> str:
    return f'<div class="announcement">{e(p.get("text"))}</div>' if p.get("text") else ""


def s_about(p: dict, site: SiteData, template: str) -> str:
    if not p.get("body") and not p.get("image"):
        return ""
    img = _img(site, p.get("image"), p.get("heading") or "", "about-img")
    return (f'<section class="about {"with-img" if img else ""}"><div class="about-text"><h2>{e(p.get("heading"))}</h2>'
            f'{_paragraphs(p.get("body"))}</div>{img}</section>')


def s_menu(p: dict, site: SiteData, template: str) -> str:
    wanted = set(p.get("categories") or [])
    cats = [(c, items) for c, items in site.menu if not wanted or c in wanted]
    if not cats:
        return ""
    blocks = []
    for cat, items in cats:
        rows = []
        for it in items:
            tags = "".join(f'<abbr class="tag" title="{e(t.replace("_", " "))}">{e(TAG_LABELS.get(t, t[:2].upper()))}</abbr>'
                           for t in it.tags if p.get("show_dietary", True) and t in TAG_LABELS)
            price = f'<span class="leader"></span><span class="price">{e(it.price)}</span>' if p.get("show_prices", True) else ""
            desc = f'<p class="desc">{e(it.description)}</p>' if p.get("show_descriptions", True) and it.description else ""
            rows.append(f'<li class="dish"><div class="dish-line"><span class="dish-name">{e(it.name)}</span>{tags}{price}'
                        f'</div>{desc}</li>')
        blocks.append(f'<div class="menu-cat"><h3>{e(cat)}</h3><ul>{"".join(rows)}</ul></div>')
    note = ' <span class="small">V vegetarian · VG vegan · GF gluten free</span>' if p.get("show_dietary", True) else ""
    order = _buttons(site, True, False)
    return f'<section class="menu" id="menu"><h2>{e(p.get("heading"))}</h2><div class="menu-grid">{"".join(blocks)}</div>' \
           f'<p class="menu-note">Prices before tax.{note}</p>{order}</section>'


def s_gallery(p: dict, site: SiteData, template: str) -> str:
    imgs = [i for i in p.get("images") or []]
    if not imgs:
        return ""
    heading = f'<h2>{e(p.get("heading"))}</h2>' if p.get("heading") else ""
    tiles = "".join(f'<figure>{_img(site, i, site.name + " photo")}</figure>' for i in imgs)
    return f'<section class="gallery">{heading}<div class="gallery-grid">{tiles}</div></section>'


def s_reviews(p: dict, site: SiteData, template: str) -> str:
    good = [r for r in site.reviews if r.get("rating", 0) >= p.get("min_rating", 4) and r.get("text")][: p.get("count", 6)]
    if not good:
        return ""
    cards = "".join(f'<blockquote class="review"><div class="stars" aria-label="{r["rating"]} out of 5">'
                    f'{"★" * r["rating"]}{"☆" * (5 - r["rating"])}</div><p>“{e(r["text"][:400])}”</p>'
                    f'<cite>{e(r.get("author") or "Guest")}</cite></blockquote>' for r in good)
    avg = f'<p class="rating">{site.rating:.1f} ★ on Google</p>' if site.rating else ""
    return f'<section class="reviews"><h2>{e(p.get("heading"))}</h2>{avg}<div class="review-grid">{cards}</div></section>'


def s_visit(p: dict, site: SiteData, template: str) -> str:
    rows = "".join(f"<tr><th>{d}</th><td>{e(h)}</td></tr>" for d, h in format_hours(site.hours)) if site.hours else ""
    hours = f'<table class="hours">{rows}</table>' if rows else ""
    contact = []
    if site.address:
        q = quote(f"{site.lat},{site.lng}" if site.lat is not None else site.address)
        contact.append(f'<p class="address">{e(site.address)}<br><a href="https://www.google.com/maps/search/?api=1&amp;query={q}" '
                       f'target="_blank" rel="noopener">Directions</a></p>')
    if site.phone:
        contact.append(f'<p><a href="tel:{e(site.phone)}">{e(site.phone)}</a></p>')
    map_html = ""
    if p.get("show_map", True) and site.lat is not None and site.lng is not None:
        d = 0.006
        bbox = f"{site.lng - d},{site.lat - d / 2},{site.lng + d},{site.lat + d / 2}"
        map_html = (f'<iframe class="map" title="Map" loading="lazy" src="https://www.openstreetmap.org/export/embed.html?'
                    f'bbox={quote(bbox)}&amp;layer=mapnik&amp;marker={site.lat},{site.lng}"></iframe>')
    if not (hours or contact or map_html):
        return ""
    return (f'<section class="visit" id="visit"><div><h2>{e(p.get("heading"))}</h2>{hours}{"".join(contact)}</div>'
            f'{map_html}</section>')


def s_cta(p: dict, site: SiteData, template: str) -> str:
    kind = p.get("button", "order")
    btn = _buttons(site, kind == "order", kind == "book") if kind != "call" else (
        f'<div class="actions"><a class="btn btn-main" href="tel:{e(site.phone)}">Call us</a></div>' if site.phone else "")
    if not btn and not p.get("text"):
        return ""
    return f'<section class="cta"><h2>{e(p.get("heading"))}</h2><p>{e(p.get("text"))}</p>{btn}</section>'


RENDERERS = {"hero": s_hero, "announcement": s_announcement, "about": s_about, "menu": s_menu, "gallery": s_gallery,
             "reviews": s_reviews, "visit": s_visit, "cta": s_cta}


# --- Page --------------------------------------------------------------------------------------------------

BASE_CSS = """
*{box-sizing:border-box}html{scroll-behavior:smooth}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--body),system-ui,sans-serif;font-size:17px;line-height:1.6}
h1,h2,h3{font-family:var(--head),Georgia,serif;line-height:1.15;margin:0 0 .5em}
h1{font-size:clamp(2.4rem,6vw,4.4rem)}h2{font-size:clamp(1.8rem,4vw,2.6rem)}h3{font-size:1.25rem}
a{color:var(--accent)}img{max-width:100%;display:block}
.wrap{max-width:1120px;margin:0 auto;padding:0 20px}
section{max-width:1120px;margin:0 auto;padding:72px 20px}
.topbar{display:flex;justify-content:space-between;align-items:center;max-width:1120px;margin:0 auto;padding:18px 20px;gap:12px}
.brand{font-family:var(--head),serif;font-size:1.4rem;font-weight:700;text-decoration:none;color:var(--text)}
.topnav{display:flex;gap:18px;flex-wrap:wrap;align-items:center}.topnav a{text-decoration:none;color:var(--text);font-weight:600;font-size:.95rem}
.topnav a.btn{padding:9px 18px}.topnav a.btn-main{color:var(--on-accent)}
.btn{display:inline-block;padding:13px 24px;border-radius:var(--radius);font-weight:700;text-decoration:none;border:2px solid var(--accent)}
.btn-main{background:var(--accent);color:var(--on-accent)}.btn-alt{color:var(--accent);background:transparent}
.actions{display:flex;gap:12px;flex-wrap:wrap;margin-top:22px}
.announcement{background:var(--accent);color:var(--on-accent);text-align:center;padding:10px 16px;font-weight:600}
.hero{display:grid;gap:40px;align-items:center}
.hero-split{grid-template-columns:1.05fr 1fr}.hero-split .hero-img{order:2;width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:var(--radius)}
.hero-plain{text-align:center;padding:110px 20px}.hero-plain .actions{justify-content:center}
.hero-overlay{position:relative;max-width:none;padding:0;min-height:72vh;display:flex;align-items:flex-end}
.hero-overlay .hero-img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.hero-overlay .hero-text{position:relative;max-width:1120px;width:100%;margin:0 auto;padding:120px 20px 64px;color:#fff}
.hero-overlay::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.05),rgba(0,0,0,.7))}
.hero-overlay .hero-text{z-index:1}
.eyebrow{text-transform:uppercase;letter-spacing:.16em;font-size:.8rem;font-weight:700;color:var(--accent);margin:0 0 10px}
.tagline{font-size:1.25rem;opacity:.85;max-width:36ch}.hero-plain .tagline{margin:0 auto}
.about{display:grid;gap:44px;align-items:center}.about.with-img{grid-template-columns:1fr 1fr}
.about-img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:var(--radius)}.about p{max-width:60ch}
.menu-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px 56px;margin-top:18px}
.menu-cat ul{list-style:none;margin:0;padding:0}.menu-cat h3{color:var(--accent);margin-top:12px}
.dish{padding:10px 0}.dish-line{display:flex;align-items:baseline;gap:8px}.dish-name{font-weight:700}
.leader{flex:1;border-bottom:2px dotted var(--line);transform:translateY(-4px);min-width:16px}.price{font-weight:700;white-space:nowrap}
.desc{margin:4px 0 0;opacity:.75;font-size:.95rem}.tag{font-size:.68rem;font-weight:800;border:1px solid currentColor;border-radius:4px;padding:0 4px;text-decoration:none;opacity:.7}
.menu-note{opacity:.6;font-size:.85rem;margin-top:24px}.small{font-size:.85rem}
.gallery-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px}
.gallery figure{margin:0}.gallery img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:var(--radius)}
.review-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:18px;margin-top:16px}
.review{margin:0;padding:22px;background:var(--surface);border-radius:var(--radius)}.review cite{font-style:normal;font-weight:700;opacity:.8}
.stars{color:var(--accent);letter-spacing:2px}.rating{font-weight:700}
.visit{display:grid;grid-template-columns:1fr 1.2fr;gap:44px;align-items:start}
.hours{border-collapse:collapse;margin:8px 0 20px}.hours th{text-align:left;padding:4px 24px 4px 0;font-weight:600}.hours td{padding:4px 0}
.map{width:100%;height:340px;border:0;border-radius:var(--radius)}
.cta{text-align:center;background:var(--surface);border-radius:var(--radius);margin:40px auto;max-width:1080px}.cta .actions{justify-content:center}
footer{border-top:1px solid var(--line);padding:32px 20px;text-align:center;font-size:.9rem;opacity:.8}
footer .social{display:flex;gap:16px;justify-content:center;margin-bottom:10px}
.preview-flag{position:sticky;top:0;z-index:99;background:#222;color:#fff;font:600 12px system-ui;text-align:center;padding:4px}
@media (max-width:760px){section{padding:52px 18px}.hero-split,.about.with-img,.visit{grid-template-columns:1fr}
.hero-split .hero-img{order:0}.topnav a:not(.btn){display:none}body{font-size:16px}}
"""

TEMPLATE_CSS = {
    "bistro": """
.t-bistro h1,.t-bistro h2{font-weight:700}.t-bistro .menu{border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.t-bistro .menu h2,.t-bistro .reviews h2{text-align:center}.t-bistro .menu-cat h3{font-style:italic;font-family:var(--head),serif}
.t-bistro .hero-split .hero-img{box-shadow:14px 14px 0 var(--surface)}
""",
    "street": """
.t-street h1,.t-street h2,.t-street h3,.t-street .brand{text-transform:uppercase;letter-spacing:.01em}
.t-street h1{font-size:clamp(3rem,9vw,6.4rem);line-height:.95}.t-street .btn{border:3px solid var(--text);box-shadow:5px 5px 0 var(--text);border-radius:0}
.t-street .btn-alt{background:var(--bg);color:var(--text)}.t-street .menu-cat{background:var(--surface);border:3px solid var(--text);
box-shadow:8px 8px 0 var(--text);padding:18px 22px}.t-street .leader{border-bottom-style:solid;border-bottom-width:3px}
.t-street .review{border:3px solid var(--text);box-shadow:6px 6px 0 var(--accent)}.t-street .announcement{text-transform:uppercase;letter-spacing:.08em}
.t-street .hero-split .hero-img{border:4px solid var(--text);box-shadow:12px 12px 0 var(--accent)}
""",
    "minimal": """
.t-minimal h1{font-weight:700;letter-spacing:-.03em}.t-minimal h2{font-weight:700;letter-spacing:-.02em;font-size:clamp(1.5rem,3vw,2rem)}
.t-minimal .leader{border-bottom:1px solid var(--line);transform:translateY(-6px)}.t-minimal .menu-cat h3{color:var(--text);
text-transform:uppercase;letter-spacing:.12em;font-size:.85rem}.t-minimal .btn{border-radius:999px}.t-minimal section{padding:88px 20px}
.t-minimal .review{background:transparent;border-left:2px solid var(--accent);border-radius:0}
""",
    "night": """
.t-night h1,.t-night h2{font-weight:500;letter-spacing:.02em}.t-night h1{font-size:clamp(3rem,8vw,5.6rem)}
.t-night .menu h2,.t-night .reviews h2,.t-night .menu-cat h3{text-align:center}.t-night .menu-cat h3{font-family:var(--head),serif;
font-size:1.6rem;border-bottom:1px solid var(--accent);padding-bottom:8px}.t-night .btn{border-radius:0;letter-spacing:.12em;text-transform:uppercase;font-size:.8rem}
.t-night .eyebrow{color:var(--accent)}.t-night body,.t-night{font-weight:300}.t-night .review{border:1px solid var(--line);background:transparent}
""",
    "garden": """
.t-garden h1,.t-garden h2{font-weight:700}.t-garden .btn{border-radius:999px}.t-garden .menu-cat{background:var(--surface);border-radius:var(--radius);padding:20px 24px}
.t-garden .hero-split .hero-img{border-radius:48% 52% 42% 58% / 55% 45% 55% 45%}.t-garden .leader{border-bottom-style:dashed}
.t-garden .tag{background:var(--accent);color:var(--on-accent);border:0;opacity:1;padding:1px 6px;border-radius:999px}
""",
}

RADIUS = {"bistro": "6px", "street": "0px", "minimal": "4px", "night": "0px", "garden": "20px"}


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _mix(a: str, b: str, t: float) -> str:
    """Blend two hex colours: t=0 gives a, t=1 gives b."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb, strict=True))


def render(content: dict, site: SiteData, page_url: str = "") -> str:
    template = content.get("template", "bistro")
    th = content["theme"]
    on_accent = "#111111" if _luminance(th["accent"]) > 0.6 else "#ffffff"
    vars_css = (f":root{{--bg:{th['background']};--text:{th['text']};--accent:{th['accent']};--on-accent:{on_accent};"
                f"--surface:{_mix(th['background'], th['text'], 0.06)};--line:{_mix(th['background'], th['text'], 0.22)};"
                f"--head:'{th['heading_font']}';--body:'{th['body_font']}';--radius:{RADIUS.get(template, '6px')}}}")
    fonts = "&".join(f"family={FONTS[f]}" for f in {th["heading_font"], th["body_font"]})
    visible = [s for s in content.get("sections", []) if s.get("visible")]
    body = "".join(RENDERERS[s["type"]](s["props"], site, template) for s in visible)
    has_menu = any(s["type"] == "menu" for s in visible) and site.menu
    has_visit = any(s["type"] == "visit" for s in visible)
    nav = "".join([
        '<a href="#menu">Menu</a>' if has_menu else "",
        '<a href="#visit">Visit</a>' if has_visit else "",
        f'<a class="btn btn-main" href="{e(site.order_url)}">Order</a>' if site.order_url else "",
    ])
    links = content.get("links") or {}
    social = "".join(f'<a href="{e(u)}" target="_blank" rel="noopener">{e(k.title() if k != "tiktok" else "TikTok")}</a>'
                     for k, u in links.items() if u)
    seo = content.get("seo") or {}
    title = seo.get("title") or site.name
    desc = seo.get("description") or ""
    flag = '<div class="preview-flag">Preview: only you can see this until you publish</div>' if site.preview else ""
    robots = '<meta name="robots" content="noindex">' if site.preview else ""
    canonical = f'<link rel="canonical" href="{e(page_url)}">' if page_url and not site.preview else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">{robots}{canonical}
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?{fonts}&display=swap" rel="stylesheet">
<style>{vars_css}{BASE_CSS}{TEMPLATE_CSS.get(template, "")}</style></head>
<body class="t-{e(template)}">{flag}
<header class="topbar"><a class="brand" href="#">{e(site.name)}</a><nav class="topnav">{nav}</nav></header>
<main>{body}</main>
<footer><div class="social">{social}</div><div>© {e(site.name)}{(" · " + e(site.address)) if site.address else ""}</div></footer>
</body></html>"""


def not_found_page(message: str = "This restaurant's website isn't published yet.") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><title>Not found</title><style>body{{font:18px system-ui;display:grid;place-items:center;'
            f'min-height:90vh;color:#444;text-align:center;padding:20px}}</style></head><body><p>{e(message)}</p></body></html>')
