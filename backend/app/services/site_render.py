"""Render a restaurant website to a single HTML page. Pure functions, no database.

Every value from the owner or the database is HTML-escaped. Styles come only from the template and the
validated theme (hex colours, whitelisted fonts), so a site can't carry scripts or arbitrary CSS. The page
uses no JavaScript at all: menu tabs, the review slider and the phone menu are plain HTML and CSS.
"""

from dataclasses import dataclass, field
from html import escape
from urllib.parse import quote

from app.services.geo import WEEKDAY_KEYS
from app.services.site_icons import svg
from app.services.site_templates import FONTS, SECTIONS, clean_content

DAY_NAMES = {"mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday", "fri": "Friday",
             "sat": "Saturday", "sun": "Sunday"}
DAY_SHORT = {k: v[:3] for k, v in DAY_NAMES.items()}
TAG_LABELS = {"vegetarian": "V", "vegan": "VG", "gluten_free": "GF", "pescatarian": "P", "halal": "H",
              "nut_free": "NF", "dairy_free": "DF"}


@dataclass
class MenuItem:
    name: str
    price: str
    description: str | None = None
    tags: list[str] = field(default_factory=list)
    id: str = ""


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
    best_sellers: list[str] = field(default_factory=list)  # item ids, most popular first
    reviews: list[dict] = field(default_factory=list)  # {"rating", "author", "text"}
    rating: float | None = None
    order_url: str | None = None
    book_url: str | None = None
    book_form_url: str | None = None  # where the booking form sends date, time and party size
    location_id: str = ""
    today: str = ""  # YYYY-MM-DD at the restaurant
    asset_base: str = ""  # images are served at {asset_base}/{asset_id}
    preview: bool = False
    edit: bool = False  # the visual editor: mark editable text, photos and sections

    def item(self, item_id: str | None) -> MenuItem | None:
        for _, items in self.menu:
            for it in items:
                if it.id and it.id == item_id:
                    return it
        return None


def e(value) -> str:
    return escape(str(value or ""), quote=True)


def _paragraphs(text: str) -> str:
    return "".join(f"<p>{e(p)}</p>" for p in (text or "").split("\n\n") if p.strip())


# --- Hours ---------------------------------------------------------------------------------------------------


def _t12(hhmm: str) -> str:
    """'17:00' -> '5 PM', '11:30' -> '11:30 AM'."""
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
    except ValueError:
        return str(hhmm)
    suffix = "AM" if h % 24 < 12 else "PM"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}" if m else f"{h12} {suffix}"


def _ranges(ranges) -> str:
    return ", ".join(f"{_t12(a)} – {_t12(b)}" for a, b in ranges)


def format_hours(hours: dict) -> list[tuple[str, str]]:
    """[("Monday", "11:30 AM – 10 PM"), ("Tuesday", "Closed"), ...] in week order."""
    return [(DAY_NAMES[k], _ranges(hours.get(k) or []) or "Closed") for k in WEEKDAY_KEYS]


def hours_groups(hours: dict) -> list[tuple[str, str]]:
    """Consecutive days with the same hours together: [("Mon – Thu", "11:30 AM – 10 PM"), ("Sun", "Closed")]."""
    groups: list[list] = []
    for k in WEEKDAY_KEYS:
        r = tuple(tuple(x) for x in hours.get(k) or [])
        if groups and groups[-1][2] == r:
            groups[-1][1] = k
        else:
            groups.append([k, k, r])
    return [(DAY_SHORT[a] if a == b else f"{DAY_SHORT[a]} – {DAY_SHORT[b]}", _ranges(r) or "Closed")
            for a, b, r in groups]


def _hours_short(hours: dict) -> str:
    groups = hours_groups(hours) if hours else []
    if len(groups) == 1 and groups[0][1] != "Closed":
        return f"Open daily {groups[0][1]}"
    if 1 < len(groups) <= 2:
        return " · ".join(f"{d} {h}" for d, h in groups)
    return ""


def _time_options(hours: dict) -> list[str]:
    starts = [a for r in hours.values() for a, _ in (r or [])]
    ends = [b for r in hours.values() for _, b in (r or [])]

    def mins(x: str) -> int:
        h, m = (int(v) for v in x.split(":")[:2])
        return h * 60 + m

    try:
        lo, hi = (min(map(mins, starts)), max(map(mins, ends)) - 60) if starts and ends else (11 * 60, 21 * 60 + 30)
    except ValueError:
        lo, hi = 11 * 60, 21 * 60 + 30
    lo = (lo + 29) // 30 * 30
    hi = min(hi, 23 * 60 + 30)
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(lo, hi + 1, 30)] or ["19:00"]


# --- Building blocks -----------------------------------------------------------------------------------------


@dataclass
class Ctx:
    site: SiteData
    template: str
    anchors: set[str]
    edit: bool = False  # the owner's editor: mark what can be clicked and edited
    sid: str = ""  # the section being rendered


# --- Edit-mode markers (only in the editor's preview; never on the public site) ---------------------------------


def _ed(ctx: "Ctx | None", field: str, multiline: bool = False) -> str:
    """Marks an element whose text the owner can edit in place: data-e="<section id>:<field>"."""
    if not (ctx and ctx.edit):
        return ""
    target = field if ":" in field else f"{ctx.sid}:{field}"
    return f' data-e="{e(target)}"' + (' data-ml="1"' if multiline else "")


def _ei(ctx: "Ctx | None", field: str) -> str:
    """Marks a photo the owner can click to change: data-img="<section id>:<field>"."""
    if not (ctx and ctx.edit):
        return ""
    target = field if ":" in field else f"{ctx.sid}:{field}"
    return f' data-img="{e(target)}"'


def _txt(ctx: "Ctx | None", p: dict, field: str, tag: str, cls: str = "", ph: str = "", path: str = "") -> str:
    """A text element from a section field. Empty fields only appear in the editor, as a prompt to type.
    path: where the text lives when it isn't a top-level field (e.g. "items.2.title")."""
    value = p.get(field)
    klass = f' class="{cls}"' if cls else ""
    target = path or field
    if value:
        return f"<{tag}{klass}{_ed(ctx, target)}>{e(value)}</{tag}>"
    if ctx and ctx.edit:
        return f'<{tag} class="{cls} edit-empty"{_ed(ctx, target)} data-ph="{e(ph)}"></{tag}>'
    return ""


def _photo_chip(ctx: Ctx, field: str, label: str) -> str:
    """In the editor, a button on sections that can take a photo but don't have one."""
    return f'<button type="button" class="edit-chip"{_ei(ctx, field)}>{e(label)}</button>' if ctx.edit else ""


def _img(site: SiteData, asset_id: str | None, alt: str, cls: str = "", eager: bool = False, ed: str = "") -> str:
    if not asset_id:
        return ""
    lazy = "" if eager else ' loading="lazy"'
    return f'<img class="{cls}" src="{e(site.asset_base)}/{e(asset_id)}" alt="{e(alt)}"{lazy}{ed}>'


def _ph(ctx: Ctx, label: str, cls: str = "", ed: str = "") -> str:
    """A dashed 'add a photo' box, only in the owner's preview."""
    if not ctx.site.preview:
        return ""
    label = "Click to add a photo" if ctx.edit else label
    return f'<div class="ph {cls}"{ed}>{e(label)}</div>'


def _book_href(ctx: Ctx) -> str | None:
    if not ctx.site.book_url:
        return None
    return "#reserve" if "reserve" in ctx.anchors and ctx.site.book_form_url else ctx.site.book_url


def _button(kind: str, ctx: Ctx, style: str = "main") -> str:
    site = ctx.site
    cls = f"btn btn-{style}"
    if kind == "order" and site.order_url:
        return f'<a class="{cls}" href="{e(site.order_url)}">Order online</a>'
    if kind == "book" and _book_href(ctx):
        return f'<a class="{cls}" href="{e(_book_href(ctx))}">{svg("calendar")}Book a table</a>'
    if kind == "call" and site.phone:
        return f'<a class="{cls}" href="tel:{e(site.phone)}">{svg("phone")}Call {e(site.phone)}</a>'
    if kind == "menu" and "menu" in ctx.anchors:
        return f'<a class="{cls}" href="#menu">View the menu</a>'
    return ""


def _actions(*buttons: str) -> str:
    inner = "".join(b for b in buttons if b)
    return f'<div class="actions">{inner}</div>' if inner else ""


def _head(p: dict, align: str = "center", key: str = "heading", extra: str = "", ctx: Ctx | None = None) -> str:
    eb = _txt(ctx, p, "eyebrow", "p", "eyebrow", "Add a small line above the heading")
    h2 = _txt(ctx, p, key, "h2", "", "Add a heading")
    if not (eb or h2 or extra):
        return ""
    if align == "split":
        return f'<div class="sec-head split"><div>{eb}{h2}</div>{extra}</div>'
    return f'<div class="sec-head {align}">{eb}{h2}{extra}</div>'


def _sec(kind: str, p: dict, inner: str, anchor: str | None, cls: str = "", pre: str = "") -> str:
    aid = f' id="{anchor}"' if anchor else ""
    return (f'<section class="sec sec-{kind} bg-{e(p.get("bg", "page"))} {cls}"{aid}>{pre}'
            f'<div class="wrap">{inner}</div></section>')


def _stars(n: int, tag: str = "div") -> str:
    n = max(0, min(5, int(n or 0)))
    return f'<{tag} class="stars" aria-label="{n} out of 5">{"★" * n}{"☆" * (5 - n)}</{tag}>'


def _good_reviews(site: SiteData, min_rating: int, count: int) -> list[dict]:
    return [r for r in site.reviews if (r.get("rating") or 0) >= min_rating and r.get("text")][:count]


def _slider(name: str, reviews: list[dict]) -> str:
    """Reviews one at a time, with dots. Radio buttons and CSS only."""
    radios = "".join(f'<input type="radio" name="{name}" id="{name}-{i}" aria-label="Review {i + 1}"'
                     f'{" checked" if i == 0 else ""}>' for i in range(len(reviews)))
    slides = "".join(
        f'<div class="slide">{_stars(r["rating"])}<blockquote>“{e(r["text"][:420])}”</blockquote>'
        f'<div class="who"><span class="avatar">{e((r.get("author") or "G")[:1].upper())}</span>'
        f'<cite>{e(r.get("author") or "Guest")}</cite></div></div>' for r in reviews)
    dots = "".join(f'<label for="{name}-{i}" title="Review {i + 1}"></label>' for i in range(len(reviews)))
    dots_html = f'<div class="dots">{dots}</div>' if len(reviews) > 1 else ""
    return f'<div class="slider">{radios}<div class="slides">{slides}</div>{dots_html}</div>'


def _tags(it: MenuItem, show: bool = True) -> str:
    if not show:
        return ""
    return "".join(f'<abbr class="tag" title="{e(t.replace("_", " "))}">{e(TAG_LABELS[t])}</abbr>'
                   for t in it.tags if t in TAG_LABELS)


def _dish_rows(items: list[MenuItem], p: dict) -> str:
    rows = []
    for it in items:
        price = (f'<span class="leader"></span><span class="price">{e(it.price)}</span>'
                 if p.get("show_prices", True) else "")
        desc = f'<p class="desc">{e(it.description)}</p>' if p.get("show_descriptions", True) and it.description else ""
        rows.append(f'<li class="dish"><div class="dish-line"><span class="dish-name">{e(it.name)}</span>'
                    f'{_tags(it, p.get("show_dietary", True))}{price}</div>{desc}</li>')
    return "".join(rows)


def _has_content(kind: str, p: dict, site: SiteData) -> bool:
    if kind == "menu":
        return bool(site.menu)
    if kind == "gallery":
        return bool(p.get("images")) or site.preview
    if kind == "reviews":
        return bool(_good_reviews(site, p.get("min_rating", 4), 1))
    if kind == "about":
        return bool(p.get("body") or p.get("image")) or site.preview
    if kind == "chef":
        return bool(p.get("name") or p.get("body") or p.get("image")) or site.preview
    if kind == "visit":
        return bool(site.hours or site.address or site.phone)
    if kind == "reserve":
        return bool(site.book_form_url or site.phone)
    if kind == "dishes":
        return bool(site.menu)
    return True


ANCHORS = {"hero": "top", "about": "about", "dishes": "dishes", "menu": "menu", "occasions": "events",
           "reserve": "reserve", "chef": "chef", "gallery": "gallery", "reviews": "reviews", "visit": "visit"}
NAV = [("about", "About"), ("menu", "Menu"), ("chef", "Our chef"), ("gallery", "Gallery"), ("events", "Events"),
       ("reviews", "Reviews"), ("reserve", "Reservations"), ("visit", "Contact")]


# --- Sections ------------------------------------------------------------------------------------------------


def s_hero(p: dict, ctx: Ctx, anchor: str | None) -> str:
    site = ctx.site
    layout = p.get("layout", "split")
    title = p.get("title") or site.name
    eb = _txt(ctx, p, "eyebrow", "p", "eyebrow", "Add a small line, e.g. Good food, good mood")
    tagline = _txt(ctx, p, "tagline", "p", "lead", "Add a tagline")
    overlay = layout in ("overlay", "centered")
    buttons = _actions(_button("order", ctx) if p.get("order_button", True) else "",
                       _button("book", ctx, "light" if overlay else "alt") if p.get("booking_button", True) else "")
    if not buttons and site.phone:
        buttons = _actions(_button("call", ctx))
    copy_html = f'<div class="hero-copy">{eb}<h1{_ed(ctx, "title")}>{e(title)}</h1>{tagline}{buttons}</div>'
    img_id = p.get("image")
    aid = f' id="{anchor}"' if anchor else ""
    if overlay:
        bg = _img(site, img_id, title, "hero-bg", eager=True, ed=_ei(ctx, "image"))
        chip = "" if bg else _photo_chip(ctx, "image", "+ Add a background photo")
        return (f'<section class="hero hero-{layout} {"has-img" if bg else "no-img"}"{aid}>{bg}'
                f'<div class="hero-shade"></div>{chip}<div class="wrap hero-in">{copy_html}</div></section>')
    media = (_img(site, img_id, title, "hero-img", eager=True, ed=_ei(ctx, "image"))
             or _ph(ctx, "Add a photo in Big welcome", "ph-hero", _ei(ctx, "image")))
    grid = "hero-grid" if media else "hero-grid no-media"
    media_html = f'<div class="hero-media">{media}</div>' if media else ""
    return (f'<section class="sec hero hero-{layout} bg-{e(p.get("bg", "page"))}"{aid}><div class="wrap">'
            f'<div class="{grid}">{copy_html}{media_html}</div></div></section>')


def s_announcement(p: dict, ctx: Ctx, anchor: str | None) -> str:
    if not p.get("text") and not ctx.edit:
        return ""
    text = _txt(ctx, p, "text", "span", "", "Write an announcement, e.g. Now open for brunch on Sundays")
    return f'<div class="announce bg-{e(p.get("bg", "accent"))}"><div class="wrap">{text}</div></div>'


def s_features(p: dict, ctx: Ctx, anchor: str | None) -> str:
    rows = [(i, r) for i, r in enumerate(p.get("items") or []) if ctx.edit or r.get("title") or r.get("text")]
    if not rows:
        return ""
    layout = p.get("layout", "icons")
    cells = "".join(
        f'<div class="feat-item"><span class="feat-ic">{svg(r.get("icon") or "star")}</span><div>'
        f'{_txt(ctx, r, "title", "h3", "", "Title", f"items.{i}.title")}'
        f'{_txt(ctx, r, "text", "p", "", "A short line", f"items.{i}.text")}</div></div>' for i, r in rows)
    head = _head(p, ctx=ctx) if (p.get("eyebrow") or p.get("heading")) else ""
    return _sec("features", p, f'{head}<div class="feat feat-{layout}">{cells}</div>', anchor,
                "has-head" if head else "")


def _badge(text: str, uid: str) -> str:
    ring = f"{text} · " * (2 if len(text) < 22 else 1)
    return (f'<div class="badge"><svg class="ring" viewBox="0 0 120 120" aria-hidden="true"><defs><path id="bp-{uid}" '
            f'd="M60,60 m-45,0 a45,45 0 1,1 90,0 a45,45 0 1,1 -90,0"/></defs><text><textPath href="#bp-{uid}" '
            f'textLength="280" lengthAdjust="spacing">{e(ring)}</textPath></text></svg>{svg("utensils")}</div>')


def _body(ctx: Ctx, p: dict, field: str, cls: str, ph: str, tag: str = "div") -> str:
    """Paragraphs (a blank line between them). Edited as one block in the editor."""
    paras = _paragraphs(p.get(field))
    if ctx.edit:
        empty = "" if paras else " edit-empty"
        return f'<{tag} class="{cls}{empty}"{_ed(ctx, field, multiline=True)} data-ph="{e(ph)}">{paras}</{tag}>'
    return f'<{tag} class="{cls}">{paras}</{tag}>' if paras else ""


def s_about(p: dict, ctx: Ctx, anchor: str | None, uid: str = "") -> str:
    site = ctx.site
    if not (p.get("body") or p.get("image") or site.preview):
        return ""
    main = _img(site, p.get("image"), p.get("heading") or site.name, "about-img", ed=_ei(ctx, "image"))
    second = _img(site, p.get("image2"), site.name, "about-img2", ed=_ei(ctx, "image2")) if main else ""
    badge = _badge(p["badge"], uid) if p.get("badge") and main else ""
    if not main:
        main = _ph(ctx, "Add photos in Our story", "ph-about", _ei(ctx, "image"))
    media = (f'<div class="about-media {"pair" if second else "single"}">{main}{second}{badge}</div>'
             if main else "")
    signoff = _txt(ctx, p, "signoff", "p", "signoff", "Add a handwritten line, e.g. Come hungry, leave happy!")
    if ctx.edit:
        body = _body(ctx, p, "body", "about-body", "Tell guests your story: who you are and what you cook")
    else:
        body = _paragraphs(p.get("body")) or ('<p class="ph-text">Tell guests who you are: add your story in '
                                              'Sections → Our story.</p>' if site.preview else "")
    copy_html = (f'<div class="about-copy">{_head(p, "left", ctx=ctx)}{body}{signoff}'
                 f'{_actions(_button(p.get("button", "none"), ctx))}</div>')
    layout = e(p.get("layout", "images-right"))
    return _sec("about", p, f'<div class="about-grid {layout} {"" if media else "no-media"}">{media}{copy_html}</div>',
                anchor)


def s_dishes(p: dict, ctx: Ctx, anchor: str | None) -> str:
    site = ctx.site
    chosen = []  # (item, photo, label, index in the owner's list or None for best sellers)
    for i, row in enumerate(p.get("items") or []):
        it = site.item(row.get("item"))
        if it:
            chosen.append((it, row.get("image"), row.get("label"), i))
    if not chosen:  # nothing picked yet: the best sellers
        chosen = [(it, None, "", None) for it in (site.item(i) for i in site.best_sellers[:4]) if it]
    if not chosen:
        return ""
    cards = []
    for it, img, label, idx in chosen:
        ed = _ei(ctx, f"items.{idx}.image") if idx is not None else ""
        media = (_img(site, img, it.name, ed=ed)
                 or f'<div class="mono"{ed}><span>{e(it.name[:1].upper())}</span></div>')
        tag = f'<span class="dish-label">{e(label)}</span>' if label else ""
        desc = f'<p class="dish-desc">{e(it.description)}</p>' if it.description else ""
        cards.append(f'<article class="dish-card"><div class="dish-media">{media}{tag}</div><div class="dish-body">'
                     f'<h3>{e(it.name)}</h3>{desc}<div class="dish-foot"><span class="dish-price">{e(it.price)}</span>'
                     f'{_tags(it)}</div></div></article>')
    link = ""
    if p.get("button") == "menu" and "menu" in ctx.anchors:
        link = f'<a class="link-arrow" href="#menu">View full menu {svg("arrow")}</a>'
    elif p.get("button") == "order" and site.order_url:
        link = f'<a class="link-arrow" href="{e(site.order_url)}">Order online {svg("arrow")}</a>'
    layout = e(p.get("layout", "cards"))
    return _sec("dishes", p, f'{_head(p, "split", extra=link, ctx=ctx)}<div class="dish-grid dishes-{layout}">'
                f'{"".join(cards)}</div>', anchor)


def s_menu(p: dict, ctx: Ctx, anchor: str | None, uid: str = "") -> str:
    wanted = set(p.get("categories") or [])
    cats = [(c, items) for c, items in ctx.site.menu if not wanted or c in wanted]
    if not cats:
        return ""
    layout = p.get("layout", "columns")
    note = " V vegetarian · VG vegan · GF gluten free" if p.get("show_dietary", True) else ""
    foot = f'<p class="menu-note">Prices before tax.{note}</p>{_actions(_button("order", ctx))}'
    if layout == "tabs" and len(cats) > 1:
        name = f"mt-{uid}"
        radios = "".join(f'<input type="radio" name="{name}" id="{name}-{i}" aria-label="{e(c)}"'
                         f'{" checked" if i == 0 else ""}>' for i, (c, _) in enumerate(cats))
        labels = "".join(f'<label for="{name}-{i}">{e(c)}</label>' for i, (c, _) in enumerate(cats))
        panels = "".join(f'<ul class="tp">{_dish_rows(items, p)}</ul>' for _, items in cats)
        body = f'<div class="tabs">{radios}<div class="tab-labels">{labels}</div><div class="tab-panels">{panels}</div></div>'
    else:
        blocks = "".join(f'<div class="menu-cat"><h3>{e(c)}</h3><ul>{_dish_rows(items, p)}</ul></div>'
                         for c, items in cats)
        body = f'<div class="menu-grid menu-{e(layout)}">{blocks}</div>'
    return _sec("menu", p, f"{_head(p, ctx=ctx)}{body}{foot}", anchor)


def _band_image(ctx: Ctx, p: dict) -> str:
    img = _img(ctx.site, p.get("image"), "", "band-bg", ed=_ei(ctx, "image"))
    if img:
        return f'{img}<div class="band-shade"></div>'
    return _photo_chip(ctx, "image", "+ Add a background photo")


def s_occasions(p: dict, ctx: Ctx, anchor: str | None) -> str:
    rows = [(i, r) for i, r in enumerate(p.get("items") or []) if ctx.edit or r.get("title")]
    if not (rows or p.get("heading") or ctx.edit):
        return ""
    bg = _band_image(ctx, p)
    has_img = bool(p.get("image"))
    cells = "".join(f'<div class="occ-item">{svg(r.get("icon") or "star")}'
                    f'{_txt(ctx, r, "title", "span", "", "Occasion", f"items.{i}.title")}</div>' for i, r in rows)
    text = _txt(ctx, p, "text", "p", "muted", "Add a sentence about the occasions you host")
    inner = (f'<div class="occ-grid"><div class="occ-copy">{_head(p, "left", ctx=ctx)}{text}'
             f'{_actions(_button(p.get("button", "none"), ctx))}</div>'
             f'{f"<div class=occ-items>{cells}</div>" if cells else ""}</div>')
    return _sec("occasions", p, inner, anchor, "has-img" if has_img else "", pre=bg)


def s_reserve(p: dict, ctx: Ctx, anchor: str | None, uid: str = "") -> str:
    site = ctx.site
    text = _txt(ctx, p, "text", "p", "muted", "Add a line, e.g. We'll confirm by text within the hour")
    if site.book_form_url:
        times = _time_options(site.hours)
        default = "19:00" if "19:00" in times else times[len(times) // 2]
        opts = "".join(f'<option value="{t}"{" selected" if t == default else ""}>{_t12(t)}</option>' for t in times)
        party = "".join(f'<option value="{n}"{" selected" if n == 2 else ""}>{n} {"guest" if n == 1 else "guests"}'
                        f"</option>" for n in range(1, 13))
        form = (f'<form class="res-form" action="{e(site.book_form_url)}" method="get">'
                f'<input type="hidden" name="book" value="{e(site.location_id)}">'
                f'<label>Date<input type="date" name="date" required min="{e(site.today)}" value="{e(site.today)}"></label>'
                f'<label>Time<select name="time">{opts}</select></label>'
                f'<label class="wide">Guests<select name="party">{party}</select></label>'
                f'<button class="btn btn-main" type="submit">{svg("calendar")}Find a table</button></form>')
    elif site.phone:
        form = _actions(_button("call", ctx))
    else:
        return ""
    card = f'<div class="res-card">{_head(p, "left", ctx=ctx)}{text}{form}</div>'
    reviews = _good_reviews(site, p.get("min_rating", 4), 6) if p.get("show_reviews", True) else []
    side = ""
    if reviews:
        eb = _txt(ctx, p, "reviews_heading", "p", "eyebrow", "Add a small heading")
        side = f'<div class="res-reviews">{eb}{svg("quote", "quote-ic", 44)}{_slider(f"rs-{uid}", reviews)}</div>'
    return _sec("reserve", p, f'<div class="res-grid {"" if side else "single"}">{card}{side}</div>', anchor)


def s_chef(p: dict, ctx: Ctx, anchor: str | None) -> str:
    site = ctx.site
    if not (p.get("name") or p.get("body") or p.get("image") or site.preview):
        return ""
    img = (_img(site, p.get("image"), p.get("name") or "Our chef", ed=_ei(ctx, "image"))
           or _ph(ctx, "Add a portrait in Meet the chef", "ph-chef", _ei(ctx, "image")))
    media = f'<div class="chef-media">{img}</div>' if img else ""
    role = _txt(ctx, p, "role", "p", "chef-role", "Role, e.g. Head chef and owner")
    if ctx.edit:
        body = _body(ctx, p, "body", "chef-quote", "Add a few words from your chef", "blockquote")
        head = (f'<div class="sec-head left">{_txt(ctx, p, "eyebrow", "p", "eyebrow", "Add a small line")}'
                f'{_txt(ctx, p, "name", "h2", "", "Your chef’s name")}</div>')
    else:
        body = (f'<blockquote class="chef-quote">{_paragraphs(p.get("body"))}</blockquote>' if p.get("body") else
                '<p class="ph-text">Add a few words from your chef.</p>' if site.preview else "")
        head = _head({"eyebrow": p.get("eyebrow"), "heading": p.get("name") or ("Your chef" if site.preview else "")},
                     "left")
    return _sec("chef", p, f'<div class="chef-grid {e(p.get("layout", "image-left"))} {"" if media else "no-media"}">'
                f'{media}<div class="chef-copy">{head}{role}{body}</div></div>', anchor)


def s_gallery(p: dict, ctx: Ctx, anchor: str | None) -> str:
    imgs = p.get("images") or []
    pick = f' data-imgs="{e(ctx.sid)}:images"' if ctx.edit else ""
    if not imgs:
        if not ctx.site.preview:
            return ""
        label = "Click to add photos to your gallery" if ctx.edit else "Add photos in Photo gallery"
        return _sec("gallery", p, f'{_head(p, ctx=ctx)}<div class="ph ph-wide"{pick}>{e(label)}</div>', anchor)
    tiles = "".join(f"<figure>{_img(ctx.site, i, ctx.site.name + ' photo')}</figure>" for i in imgs)
    return _sec("gallery", p, f'{_head(p, ctx=ctx)}<div class="gal gal-{e(p.get("layout", "grid"))}"{pick}>{tiles}</div>',
                anchor)


def s_reviews(p: dict, ctx: Ctx, anchor: str | None, uid: str = "") -> str:
    site = ctx.site
    good = _good_reviews(site, p.get("min_rating", 4), p.get("count", 6))
    if not good:
        return ""
    avg = (f'<p class="rating"><strong>{site.rating:.1f}</strong> {_stars(round(site.rating), 'span')} on Google</p>'
           if site.rating else "")
    if p.get("layout") == "slider":
        body = f'{svg("quote", "quote-ic", 44)}{_slider(f"rv-{uid}", good)}'
        return _sec("reviews", p, f'{_head(p, extra=avg, ctx=ctx)}<div class="reviews-slider">{body}</div>', anchor)
    cards = "".join(f'<blockquote class="review">{_stars(r["rating"])}<p>“{e(r["text"][:400])}”</p>'
                    f'<cite>{e(r.get("author") or "Guest")}</cite></blockquote>' for r in good)
    return _sec("reviews", p, f'{_head(p, extra=avg, ctx=ctx)}<div class="review-grid">{cards}</div>', anchor)


def s_visit(p: dict, ctx: Ctx, anchor: str | None) -> str:
    site = ctx.site
    rows = "".join(f"<tr><th>{d}</th><td>{e(h)}</td></tr>" for d, h in format_hours(site.hours)) if site.hours else ""
    hours = f'<table class="hours">{rows}</table>' if rows else ""
    contact = []
    if site.address:
        q = quote(f"{site.lat},{site.lng}" if site.lat is not None else site.address)
        contact.append(f'<p class="contact-line">{svg("pin")}<span>{e(site.address)}<br><a href="https://www.google.com/'
                       f'maps/search/?api=1&amp;query={q}" target="_blank" rel="noopener">Get directions</a></span></p>')
    if site.phone:
        contact.append(f'<p class="contact-line">{svg("phone")}<a href="tel:{e(site.phone)}">{e(site.phone)}</a></p>')
    map_html = ""
    if p.get("show_map", True) and site.lat is not None and site.lng is not None:
        d = 0.006
        bbox = f"{site.lng - d},{site.lat - d / 2},{site.lng + d},{site.lat + d / 2}"
        map_html = (f'<iframe class="map" title="Map" loading="lazy" src="https://www.openstreetmap.org/export/embed.html?'
                    f'bbox={quote(bbox)}&amp;layer=mapnik&amp;marker={site.lat},{site.lng}"></iframe>')
    if not (hours or contact or map_html):
        return ""
    return _sec("visit", p, f'<div class="visit-grid {"" if map_html else "no-map"}"><div>{_head(p, "left", ctx=ctx)}'
                f'{hours}{"".join(contact)}</div>{map_html}</div>', anchor)


def s_cta(p: dict, ctx: Ctx, anchor: str | None) -> str:
    btn = _actions(_button(p.get("button", "order"), ctx))
    if not btn and not p.get("text") and not ctx.edit:
        return ""
    bg = _band_image(ctx, p)
    text = _txt(ctx, p, "text", "p", "muted", "Add a short line")
    return _sec("cta", p, f"{_head(p, ctx=ctx)}{text}{btn}", anchor, "has-img" if p.get("image") else "", pre=bg)


BLANK_REASONS = {
    "menu": "shows your menu once it comes in from your POS",
    "dishes": "shows your dishes once your menu comes in from your POS",
    "reviews": "shows your Google reviews once you have some (connect Google in Settings)",
    "visit": "shows your hours and address (add them in Settings)",
    "reserve": "turn on bookings or add a phone number in Settings",
    "features": "add highlights in this section's settings",
}

RENDERERS = {"hero": s_hero, "announcement": s_announcement, "features": s_features, "about": s_about,
             "dishes": s_dishes, "menu": s_menu, "occasions": s_occasions, "reserve": s_reserve, "chef": s_chef,
             "gallery": s_gallery, "reviews": s_reviews, "visit": s_visit, "cta": s_cta}
NEEDS_UID = {"about", "menu", "reserve", "reviews"}


# --- Colours ---------------------------------------------------------------------------------------------------


def _rgb(h: str) -> list[int]:
    return [int(h[i:i + 2], 16) for i in (1, 3, 5)]


def _rel_lum(h: str) -> float:
    def lin(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (lin(c) for c in _rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a: str, b: str) -> float:
    la, lb = sorted((_rel_lum(a), _rel_lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _on(color: str) -> str:
    """Readable text colour on a background."""
    return "#ffffff" if _contrast(color, "#ffffff") >= _contrast(color, "#141414") else "#141414"


def _mix(a: str, b: str, t: float) -> str:
    """Blend two hex colours: t=0 gives a, t=1 gives b."""
    ca, cb = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(ca, cb, strict=True))


def _band(bg: str, text: str, accent: str) -> str:
    a = accent if _contrast(accent, bg) >= 1.7 else text
    on_a = bg if a == text else _on(a)
    dark = _rel_lum(bg) < 0.2
    return (f"--c-bg:{bg};--c-text:{text};--c-muted:{_mix(text, bg, 0.3)};--c-line:{_mix(bg, text, 0.16)};"
            f"--c-card:{_mix(bg, text, 0.08 if dark else 0.045)};--a:{a};--on-a:{on_a}")


HEAD_WEIGHT = {"Bebas Neue": 400, "Archivo Black": 400, "Yeseva One": 400, "DM Serif Display": 400, "Marcellus": 400,
               "Libre Baskerville": 400, "Playfair Display": 500, "Cormorant Garamond": 500, "Fraunces": 500,
               "Bodoni Moda": 500, "Shippori Mincho": 500, "Cinzel": 500, "Jost": 500, "Lora": 600}
CORNERS = {"square": ("0px", "0px", "0px"), "soft": ("6px", "12px", "6px"), "round": ("14px", "26px", "999px")}


def theme_css(th: dict) -> str:
    on_accent = _on(th["accent"])
    on_dark = th["background"] if _contrast(th["background"], th["dark"]) >= 7 else "#f4f0ea"
    surface = _mix(th["background"], th["text"], 0.07 if _rel_lum(th["background"]) < 0.2 else 0.045)
    r, rl, rb = CORNERS.get(th.get("corners", "soft"), CORNERS["soft"])
    root = (f":root{{--bg:{th['background']};--text:{th['text']};--accent:{th['accent']};--on-accent:{on_accent};"
            f"--dark:{th['dark']};--on-dark:{on_dark};--head:'{th['heading_font']}';--body:'{th['body_font']}';"
            f"--script:'{th['script_font']}';--hw:{HEAD_WEIGHT.get(th['heading_font'], 700)};--r:{r};--rl:{rl};"
            f"--rb:{rb};--glow:{_mix(th['dark'], th['accent'], 0.35)}}}")
    bands = {"page": (th["background"], th["text"]), "alt": (surface, th["text"]), "dark": (th["dark"], on_dark),
             "accent": (th["accent"], on_accent)}
    return root + "".join(f".bg-{k}{{{_band(b, t, th['accent'])}}}" for k, (b, t) in bands.items())


# --- Page ------------------------------------------------------------------------------------------------------

BASE_CSS = """
*{box-sizing:border-box}html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--text);font-family:var(--body),system-ui,sans-serif;font-size:16.5px;
line-height:1.65;-webkit-font-smoothing:antialiased}
img{max-width:100%;display:block}figure{margin:0}a{color:inherit}p{margin:0 0 1em}
h1,h2,h3{font-family:var(--head),Georgia,serif;font-weight:var(--hw);line-height:1.12;margin:0 0 .45em}
h1{font-size:clamp(2.6rem,6vw,4.6rem)}h2{font-size:clamp(2rem,3.8vw,2.9rem)}h3{font-size:1.2rem}
.wrap{max-width:1200px;margin:0 auto;padding:0 24px}
.sec{background:var(--c-bg);color:var(--c-text);padding:clamp(64px,9vw,112px) 0;position:relative;overflow:hidden}
.sec a:not(.btn){color:var(--a)}.muted{color:var(--c-muted)}
.sec-head{max-width:760px;margin:0 auto 48px;text-align:center}.sec-head.left{margin:0 0 22px;text-align:left}
.sec-head.split{max-width:none;display:flex;justify-content:space-between;align-items:flex-end;gap:20px;text-align:left;flex-wrap:wrap}
.sec-head h2{margin:0}.sec-head .rating{margin:14px 0 0;display:flex;gap:8px;justify-content:center;align-items:center}
.eyebrow{color:var(--a);margin:0 0 12px}
.eb-caps .eyebrow{font:700 .78rem/1.3 var(--body),sans-serif;letter-spacing:.22em;text-transform:uppercase}
.eb-script .eyebrow{font-family:var(--script),cursive;font-size:clamp(1.55rem,2.6vw,2.1rem);line-height:1.1;font-weight:400}
.lead{font-size:1.13rem;color:var(--c-muted);max-width:46ch}
.btn{display:inline-flex;align-items:center;gap:10px;padding:14px 26px;border-radius:var(--rb);font:700 .92rem/1 var(--body),sans-serif;
text-decoration:none;border:1.5px solid var(--a);cursor:pointer;transition:transform .15s,background .15s}
.btn:hover{transform:translateY(-2px)}.btn .ic{width:18px;height:18px}
.btn-main{background:var(--a);color:var(--on-a)}.btn-alt{background:transparent;color:var(--a)}
.btn-light{background:transparent;color:#fff;border-color:rgba(255,255,255,.8)}
.actions{display:flex;gap:14px;flex-wrap:wrap;margin-top:30px}
.link-arrow{display:inline-flex;align-items:center;gap:8px;font-weight:700;text-decoration:none;font-size:.95rem}
.link-arrow .ic{width:18px;transition:transform .2s}.link-arrow:hover .ic{transform:translateX(4px)}
.ph{display:grid;place-items:center;border:2px dashed var(--c-line);border-radius:var(--rl);color:var(--c-muted);
font:600 .9rem/1.4 var(--body),sans-serif;aspect-ratio:4/3;background:var(--c-card);text-align:center;padding:16px;width:100%}
.ph-text{border:2px dashed var(--c-line);padding:14px;border-radius:var(--r);color:var(--c-muted)}
.ph-wide{aspect-ratio:auto;min-height:180px}
.preview-flag{position:sticky;top:0;z-index:99;background:#222;color:#fff;font:600 12px system-ui;text-align:center;padding:4px}
/* Header */
.infobar{font-size:.82rem}.ib{display:flex;gap:26px;align-items:center;min-height:42px;flex-wrap:wrap;padding-top:4px;padding-bottom:4px}
.ib span,.ib a{display:inline-flex;align-items:center;gap:7px;text-decoration:none}.ib .ic{width:15px;height:15px;color:var(--a)}
.ib-social{margin-left:auto;display:flex;gap:14px}.ib-social .ic{color:inherit}
.site-head{position:sticky;top:0;z-index:30}
.site-head[class*=bg-],.infobar[class*=bg-]{background:var(--c-bg);color:var(--c-text)}.is-preview .site-head{top:22px}
.site-head.bg-page{box-shadow:0 1px 0 var(--c-line)}
.hd{display:flex;align-items:center;gap:28px;min-height:82px}
.logo{display:flex;flex-direction:column;align-items:flex-start;text-decoration:none;color:inherit;line-height:1}
.logo-name{font-family:var(--head),serif;font-size:1.6rem;font-weight:var(--hw)}
.logo-sub{font-size:.64rem;letter-spacing:.32em;text-transform:uppercase;margin-top:7px;opacity:.8}
.logo img{max-height:58px;width:auto}
.nav{display:flex;gap:28px;margin-left:auto;align-items:center}
.nav a{text-decoration:none;font-weight:600;font-size:.82rem;letter-spacing:.1em;text-transform:uppercase;padding:6px 0;border-bottom:1.5px solid transparent}
.nav a:hover{border-color:var(--a)}.hd>.btn{padding:12px 20px;font-size:.82rem}
.mnav{display:none;margin-left:auto}.mnav summary{list-style:none;cursor:pointer;padding:8px;display:flex}
.mnav summary::-webkit-details-marker{display:none}
.mnav-panel{position:absolute;left:0;right:0;top:100%;padding:8px 24px 24px;display:flex;flex-direction:column;
background:var(--c-bg);color:var(--c-text);box-shadow:0 16px 30px rgba(0,0,0,.2)}
.mnav-panel a{padding:14px 0;text-decoration:none;font-weight:600;border-bottom:1px solid var(--c-line)}
.mnav-panel .btn{margin-top:16px;justify-content:center;border-bottom:1.5px solid var(--a)}
.top-over{position:absolute;top:auto;left:0;right:0;z-index:30;color:#fff}
.top-over .site-head{position:relative;top:0}.top-over .infobar{border-bottom:1px solid rgba(255,255,255,.16)}
.top-over .hd>.btn-main{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}
.top-over .ib .ic{color:var(--accent)}
.page{position:relative}
/* Hero */
.hero-overlay,.hero-centered{position:relative;min-height:min(92vh,880px);display:flex;align-items:center;color:#fff;
background:var(--dark);overflow:hidden;padding:120px 0 90px;--a:var(--accent);--on-a:var(--on-accent)}
.has-over .hero-overlay,.has-over .hero-centered{padding-top:190px}
.hero-bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.hero-shade{position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.8),rgba(0,0,0,.45) 55%,rgba(0,0,0,.15))}
.hero-centered .hero-shade{background:rgba(0,0,0,.55)}
.no-img .hero-shade{background:radial-gradient(circle at 80% 25%,var(--glow),transparent 55%),radial-gradient(circle at 5% 95%,var(--glow),transparent 45%)}
.hero-in{position:relative;width:100%}.hero-copy{max-width:660px}
.hero-centered .hero-copy{margin:0 auto;text-align:center}.hero-centered .actions{justify-content:center}.hero-centered .lead{margin:0 auto}
.hero-overlay .lead,.hero-centered .lead{color:rgba(255,255,255,.86)}
.hero-grid{display:grid;grid-template-columns:1.05fr 1fr;gap:clamp(32px,6vw,80px);align-items:center}
.hero-grid.no-media{grid-template-columns:1fr;text-align:center}.no-media .hero-copy{margin:0 auto}
.no-media .actions{justify-content:center}.no-media .lead{margin:0 auto}
.hero-media{position:relative}
.hero-split .hero-img{width:100%;aspect-ratio:4/3.3;object-fit:cover;border-radius:var(--rl)}
.hero-arch .hero-media{max-width:440px;width:100%;justify-self:center}
.hero-arch .hero-img,.hero-arch .ph{width:100%;aspect-ratio:3/4;object-fit:cover;border-radius:9999px 9999px var(--rl) var(--rl);position:relative;z-index:1}
.hero-arch .hero-media::before{content:"";position:absolute;inset:-16px 16px 16px -16px;border:1.5px solid var(--a);border-radius:9999px 9999px var(--rl) var(--rl)}
.announce{padding:11px 0;text-align:center;font-weight:600;font-size:.92rem;background:var(--c-bg);color:var(--c-text)}
/* Highlights */
.sec-features{padding:46px 0}.sec-features.has-head{padding:clamp(64px,8vw,100px) 0}
.feat{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.feat-icons .feat-item{display:flex;gap:16px;align-items:flex-start;padding:8px 26px;border-left:1px solid var(--c-line)}
.feat-icons .feat-item:first-child{border-left:0;padding-left:0}
.feat-ic{width:54px;height:54px;border-radius:50%;border:1.5px solid var(--a);color:var(--a);display:grid;place-items:center;flex:none}
.feat-item h3{font:700 .8rem/1.3 var(--body),sans-serif;letter-spacing:.12em;text-transform:uppercase;margin:6px 0 6px}
.feat-item p{margin:0;font-size:.9rem;color:var(--c-muted)}
.feat-cards{gap:20px}.feat-cards .feat-item{background:var(--c-card);border-radius:var(--rl);padding:36px 26px;text-align:center;
display:flex;flex-direction:column;align-items:center;gap:10px}
.feat-cards .feat-ic{width:66px;height:66px;border:0;background:var(--a);color:var(--on-a)}.feat-cards .ic{width:28px;height:28px}
/* Our story */
.about-grid{display:grid;grid-template-columns:1fr 1fr;gap:clamp(40px,7vw,96px);align-items:center}
.about-grid.images-right .about-media{order:2}
.about-grid.no-media{grid-template-columns:minmax(0,760px);justify-content:center;text-align:center}
.no-media .sec-head.left{text-align:center}.no-media .actions{justify-content:center}
.about-media{position:relative}.about-media.pair{padding-bottom:70px}
.about-img{width:100%;aspect-ratio:4/3.4;object-fit:cover;border-radius:var(--rl)}.pair .about-img{width:82%}
.images-right .pair .about-img{margin-left:auto}
.about-img2{position:absolute;right:0;bottom:0;width:50%;aspect-ratio:1;object-fit:cover;border-radius:var(--rl);
border:8px solid var(--c-bg);box-shadow:0 18px 40px rgba(0,0,0,.18)}.images-right .about-img2{right:auto;left:0}
.badge{position:absolute;left:60%;top:55%;width:136px;height:136px;transform:translate(-50%,-50%);border-radius:50%;
background:var(--c-bg);color:var(--c-text);display:grid;place-items:center;box-shadow:0 10px 30px rgba(0,0,0,.2);z-index:2}
.images-right .badge{left:40%}.single .badge{left:auto;right:-14px;top:auto;bottom:-14px;transform:none}
.badge .ring{position:absolute;inset:6px;width:calc(100% - 12px);height:calc(100% - 12px);animation:spin 40s linear infinite}
.badge text{font:700 9px var(--body),sans-serif;letter-spacing:.18em;text-transform:uppercase;fill:currentColor}
.badge>.ic{color:var(--a);width:30px;height:30px}
@keyframes spin{to{transform:rotate(360deg)}}@media (prefers-reduced-motion:reduce){.badge .ring{animation:none}}
.signoff{font-family:var(--script),cursive;font-size:2rem;color:var(--a);margin:6px 0 0;line-height:1.2}
/* Signature dishes */
.dish-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:24px}
.dish-card{background:var(--c-card);border-radius:var(--rl);overflow:hidden;display:flex;flex-direction:column;position:relative}
.dish-media{position:relative;aspect-ratio:4/3;overflow:hidden}
.dish-media img{width:100%;height:100%;object-fit:cover;transition:transform .6s}.dish-card:hover .dish-media img{transform:scale(1.06)}
.mono{width:100%;height:100%;display:grid;place-items:center;color:var(--a);font:400 4.2rem/1 var(--head),serif;
background:radial-gradient(circle at 30% 25%,color-mix(in srgb,var(--a) 26%,var(--c-card)),color-mix(in srgb,var(--a) 8%,var(--c-card)))}
.dish-label{position:absolute;top:12px;left:12px;background:var(--a);color:var(--on-a);font:700 .68rem/1 var(--body),sans-serif;
letter-spacing:.08em;text-transform:uppercase;padding:7px 10px;border-radius:var(--rb);z-index:2}
.dish-body{padding:18px 20px 20px;display:flex;flex-direction:column;flex:1}.dish-body h3{font-size:1.15rem;margin:0 0 4px}
.dish-desc{font-size:.88rem;color:var(--c-muted);margin:0 0 14px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.dish-foot{margin-top:auto;display:flex;align-items:center;gap:6px}.dish-price{color:var(--a);font-weight:700;font-size:1.05rem;margin-right:auto}
.dishes-overlay .dish-card{aspect-ratio:3/4;color:#fff;justify-content:flex-end}
.dishes-overlay .dish-media{position:absolute;inset:0;aspect-ratio:auto}
.dishes-overlay .dish-media::after{content:"";position:absolute;inset:0;background:linear-gradient(180deg,transparent 35%,rgba(0,0,0,.85))}
.dishes-overlay .dish-body{position:relative;z-index:1;flex:0}.dishes-overlay .dish-desc{color:rgba(255,255,255,.82)}
.dishes-overlay .dish-price{color:#fff}.dishes-overlay .tag{color:#fff}
/* Menu */
.menu-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px 56px}
.menu-cat ul,.tp{list-style:none;margin:0;padding:0}.menu-cat h3{color:var(--a);margin:12px 0 6px}
.dish{padding:10px 0}.dish-line{display:flex;align-items:baseline;gap:8px}.dish-name{font-weight:700}
.leader{flex:1;border-bottom:2px dotted var(--c-line);transform:translateY(-4px);min-width:16px}.price{font-weight:700;white-space:nowrap}
.desc{margin:3px 0 0;color:var(--c-muted);font-size:.92rem}
.tag{font-size:.64rem;font-weight:800;border:1px solid currentColor;border-radius:4px;padding:0 4px;text-decoration:none;opacity:.75}
.menu-note{color:var(--c-muted);font-size:.85rem;margin-top:28px;text-align:center}.sec-menu .actions{justify-content:center}
.menu-cards{gap:24px}.menu-cards .menu-cat{background:var(--c-card);border-radius:var(--rl);padding:24px 28px}
.tabs>input,.slider>input{position:absolute;opacity:0;pointer-events:none}
.tab-labels{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-bottom:36px}
.tab-labels label{cursor:pointer;padding:10px 20px;border:1.5px solid var(--c-line);border-radius:var(--rb);font-weight:600;font-size:.9rem}
.tp{display:none;grid-template-columns:1fr 1fr;gap:4px 56px;max-width:980px;margin:0 auto}
/* Events */
.has-img{--c-text:#fff;--c-muted:rgba(255,255,255,.8);--c-line:rgba(255,255,255,.28);--a:var(--accent);--on-a:var(--on-accent);color:#fff}
.band-bg{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.band-shade{position:absolute;inset:0;background:linear-gradient(90deg,rgba(0,0,0,.84),rgba(0,0,0,.5))}
.has-img .wrap{position:relative}
.occ-grid{display:grid;grid-template-columns:1fr 1.3fr;gap:48px;align-items:center}
.occ-items{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr))}
.occ-item{text-align:center;padding:10px 16px;border-left:1px solid var(--c-line);display:flex;flex-direction:column;
align-items:center;gap:14px;font-weight:600;font-size:.95rem}.occ-item:first-child{border-left:0}.occ-item .ic{width:42px;height:42px;color:var(--a)}
/* Booking */
.res-grid{display:grid;grid-template-columns:1.1fr 1fr;gap:clamp(32px,6vw,80px);align-items:center}
.res-grid.single{grid-template-columns:minmax(0,640px);justify-content:center}
.res-card{background:var(--c-card);border-radius:var(--rl);padding:clamp(28px,4vw,46px)}
.res-form{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:26px}
.res-form label{display:flex;flex-direction:column;gap:7px;font-size:.74rem;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:var(--c-muted)}
.res-form label.wide,.res-form .btn{grid-column:1/-1}.res-form .btn{justify-content:center;margin-top:6px;padding:16px}
.res-form input,.res-form select{font:500 1rem var(--body),sans-serif;letter-spacing:0;text-transform:none;color:var(--c-text);
background:var(--c-bg);border:1px solid var(--c-line);border-radius:var(--r);padding:12px 14px;min-height:50px;width:100%}
.quote-ic{color:var(--a);margin-bottom:6px}
.slider{position:relative;overflow:hidden}.slides{display:flex;transition:transform .6s ease}.slide{flex:0 0 100%;padding:2px}
.slide blockquote{margin:12px 0 0;font-family:var(--head),serif;font-size:clamp(1.15rem,2vw,1.45rem);line-height:1.5}
.who{display:flex;align-items:center;gap:12px;margin-top:22px}.who cite{font-style:normal;font-weight:700}
.avatar{width:46px;height:46px;border-radius:50%;background:var(--a);color:var(--on-a);display:grid;place-items:center;font-weight:700}
.dots{display:flex;gap:8px;margin-top:26px}.dots label{width:10px;height:10px;border-radius:50%;background:var(--c-line);cursor:pointer;transition:width .3s}
.reviews-slider{max-width:800px;margin:0 auto;text-align:center}.reviews-slider .quote-ic{margin:0 auto 6px}
.reviews-slider .who,.reviews-slider .dots{justify-content:center}
.stars{color:var(--a);letter-spacing:2px}
/* Chef */
.chef-grid{display:grid;grid-template-columns:.9fr 1.1fr;gap:clamp(40px,7vw,96px);align-items:center}
.chef-grid.image-right .chef-media{order:2}.chef-grid.no-media{grid-template-columns:minmax(0,720px);justify-content:center}
.chef-media{position:relative;max-width:460px;width:100%;justify-self:center}
.chef-media img,.chef-media .ph{width:100%;aspect-ratio:4/5;object-fit:cover;border-radius:var(--rl);position:relative;z-index:1}
.chef-media::before{content:"";position:absolute;inset:22px -22px -22px 22px;border:1.5px solid var(--a);border-radius:var(--rl)}
.chef-role{font:700 .78rem var(--body),sans-serif;letter-spacing:.2em;text-transform:uppercase;color:var(--c-muted);margin:-8px 0 24px}
.chef-quote{font-family:var(--head),serif;font-size:1.18rem;font-style:italic;border-left:2px solid var(--a);padding-left:24px;margin:0}
/* Gallery */
.gal-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px}
.gal-grid img{width:100%;aspect-ratio:1;object-fit:cover;border-radius:var(--r)}
.gal-mosaic{display:grid;grid-template-columns:repeat(3,1fr);grid-auto-rows:clamp(150px,18vw,230px);gap:14px;grid-auto-flow:dense}
.gal-mosaic figure:nth-child(6n+1){grid-column:span 2;grid-row:span 2}.gal-mosaic figure:nth-child(12n+7){grid-column:2/span 2}
.gal-mosaic img{width:100%;height:100%;object-fit:cover;border-radius:var(--r)}
.gal-strip{display:flex;gap:14px;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:12px;scrollbar-width:thin}
.gal-strip figure{flex:0 0 clamp(220px,27vw,330px);scroll-snap-align:start}
.gal-strip img{width:100%;aspect-ratio:3/4;object-fit:cover;border-radius:var(--r)}
.gal img:hover{filter:brightness(1.05)}
/* Reviews */
.review-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:20px}
.review{margin:0;padding:26px;background:var(--c-card);border-radius:var(--rl)}.review p{margin:12px 0 16px}
.review cite{font-style:normal;font-weight:700;color:var(--c-muted)}
/* Visit */
.visit-grid{display:grid;grid-template-columns:1fr 1.25fr;gap:clamp(32px,6vw,72px);align-items:stretch}
.visit-grid.no-map{grid-template-columns:minmax(0,560px)}
.hours{border-collapse:collapse;width:100%;max-width:440px;margin:4px 0 28px}
.hours th,.hours td{padding:9px 0;border-bottom:1px solid var(--c-line);text-align:left;font-weight:600}
.hours td{text-align:right;font-weight:400;color:var(--c-muted)}
.contact-line{display:flex;gap:12px;align-items:flex-start;margin:0 0 14px}.contact-line .ic{color:var(--a);margin-top:2px}
.map{width:100%;height:100%;min-height:380px;border:0;border-radius:var(--rl)}
/* Banner */
.sec-cta{text-align:center}.sec-cta .actions{justify-content:center}.sec-cta p{max-width:56ch;margin:0 auto}
.sec-cta.has-img .band-shade{background:rgba(0,0,0,.6)}
/* Footer */
.foot{background:var(--c-bg);color:var(--c-text);font-size:.93rem}
.foot-cols{padding:76px 0 0}.foot-grid{display:grid;grid-template-columns:1.4fr 1fr 1fr 1.3fr;gap:40px}
.foot h4{font:700 .76rem var(--body),sans-serif;letter-spacing:.2em;text-transform:uppercase;color:var(--a);margin:6px 0 18px}
.foot ul{list-style:none;padding:0;margin:0;display:grid;gap:10px}.foot li{color:var(--c-muted)}
.foot a{text-decoration:none;color:var(--c-muted)}.foot a:hover{color:var(--c-text)}
.foot-about p{color:var(--c-muted);max-width:34ch;margin-top:18px}
.social{display:flex;gap:10px;margin-top:20px}
.social a{width:40px;height:40px;border-radius:50%;border:1px solid var(--c-line);display:grid;place-items:center;color:var(--c-text)}
.social .ic{width:18px;height:18px}
.foot-bottom{border-top:1px solid var(--c-line);margin-top:60px;padding:22px 0;display:flex;justify-content:space-between;gap:12px;
flex-wrap:wrap;color:var(--c-muted);font-size:.82rem}
.foot-simple{padding:44px 0;text-align:center;border-top:1px solid var(--c-line)}.foot-simple .social{justify-content:center;margin:0 0 14px}
.foot-simple p{margin:0;color:var(--c-muted)}
@media (max-width:900px){
.nav,.hd>.btn{display:none}.mnav{display:block}.hd{min-height:68px}.ib-hide{display:none!important}
.hero-grid,.about-grid,.occ-grid,.res-grid,.chef-grid,.visit-grid{grid-template-columns:1fr}
.hero-grid .hero-media{order:-1}.about-grid .about-media,.chef-grid .chef-media{order:2}
.tp{grid-template-columns:1fr}.gal-mosaic{grid-template-columns:1fr 1fr;grid-auto-rows:150px}
.gal-mosaic figure:nth-child(6n+1),.gal-mosaic figure:nth-child(12n+7){grid-column:span 2;grid-row:span 1}
.feat-icons .feat-item{border-left:0;padding:12px 0}.occ-item{border-left:0}.map{min-height:300px}
.foot-grid{grid-template-columns:1fr 1fr}.has-over .hero-overlay,.has-over .hero-centered{padding-top:150px}
.feat{grid-template-columns:1fr 1fr;gap:16px}}
@media (max-width:560px){body{font-size:16px}.wrap{padding:0 18px}.res-form{grid-template-columns:1fr}
.dish-grid{grid-template-columns:1fr 1fr;gap:12px}.dish-body{padding:12px 14px 14px}.dish-body h3{font-size:1rem}.dish-desc{display:none}
.mono{font-size:3rem}.foot-grid{grid-template-columns:1fr}.feat{grid-template-columns:1fr}
.hero-overlay,.hero-centered{min-height:86vh}.badge{width:110px;height:110px}.badge text{font-size:10px}.ib{gap:14px}}
"""


# Only in the visual editor: what can be clicked, typed into, or added.
EDIT_CSS = """
[data-e]{cursor:text;border-radius:4px;transition:box-shadow .12s,background .12s}
[data-e]:hover{box-shadow:0 0 0 2px rgba(124,92,255,.55)}
[data-e]:focus,[data-e].editing{outline:none;box-shadow:0 0 0 2px #7c5cff;background:rgba(124,92,255,.08)}
.edit-empty{min-height:1.2em;min-width:6em}span.edit-empty{display:inline-block}
.edit-empty:empty::before{content:attr(data-ph);opacity:.45;font-style:italic}
[data-img],[data-imgs]{cursor:pointer}
[data-img]:hover,[data-imgs]:hover{outline:3px solid #7c5cff;outline-offset:-3px}
.edit-chip{position:absolute;top:18px;right:18px;z-index:6;background:#fff;color:#2b2140;border:0;border-radius:999px;
padding:10px 16px;font:600 14px system-ui,sans-serif;box-shadow:0 4px 16px rgba(0,0,0,.25);cursor:pointer}
.edit-chip:hover{background:#f1eeff}
.edit-bar{padding:16px 20px;text-align:center;font:600 14px/1.4 system-ui,sans-serif;color:#5b5470;cursor:pointer;
background:repeating-linear-gradient(45deg,#f7f5fc 0 12px,#efecf7 12px 24px);border-top:2px dashed #cbc2e8;
border-bottom:2px dashed #cbc2e8}
.edit-hidden{color:#8b84a0}
.edit-add{margin:28px auto;max-width:1152px;width:calc(100% - 48px);padding:30px;text-align:center;
border:2px dashed #b9aef0;border-radius:14px;color:#6a55e0;font:600 16px system-ui,sans-serif;cursor:pointer;background:#faf9ff}
.edit-add:hover{background:#f1eeff}
.edit-sec{position:relative}
.badge .ring{animation:none}
"""


def _nth_rules() -> str:
    """Tabs and slider: which panel/slide shows for each checked radio (CSS only)."""
    out = []
    for n in range(1, 31):
        out.append(f".tabs>input:nth-of-type({n}):checked~.tab-labels label:nth-child({n})"
                   f"{{background:var(--a);color:var(--on-a);border-color:var(--a)}}")
        out.append(f".tabs>input:nth-of-type({n}):focus-visible~.tab-labels label:nth-child({n}){{outline:2px solid var(--a);"
                   f"outline-offset:2px}}")
        out.append(f".tabs>input:nth-of-type({n}):checked~.tab-panels .tp:nth-child({n}){{display:grid}}")
    for n in range(1, 13):
        out.append(f".slider>input:nth-of-type({n}):checked~.slides{{transform:translateX(-{(n - 1) * 100}%)}}")
        out.append(f".slider>input:nth-of-type({n}):checked~.dots label:nth-child({n}){{background:var(--a);width:26px;"
                   f"border-radius:6px}}")
    return "".join(out)


NTH_CSS = _nth_rules()

TEMPLATE_CSS = {
    "gourmet": """
.t-gourmet .sec-head h2::after{content:"";display:block;width:60px;height:2px;margin:18px auto 0;background:var(--a)}
.t-gourmet .sec-head.left h2::after,.t-gourmet .sec-head.split h2::after{margin-left:0}
.t-gourmet .btn{text-transform:uppercase;letter-spacing:.1em;font-size:.8rem}
.t-gourmet .dish-card{background:transparent}.t-gourmet .dish-media{border-radius:var(--rl)}.t-gourmet .dish-body{padding:16px 2px 0}
.t-gourmet .hero h1{font-weight:500}.t-gourmet .feat-icons .feat-ic{border-width:1px}
""",
    "trattoria": """
.t-trattoria .hero::after{content:"";position:absolute;left:0;right:0;bottom:0;height:12px;
background:repeating-linear-gradient(90deg,var(--accent) 0 24px,var(--bg) 24px 48px)}
.t-trattoria .menu-cat h3{font-family:var(--script),cursive;font-size:2rem;font-weight:700}
.t-trattoria .leader{border-bottom-style:dashed}.t-trattoria .about-img{border-radius:9999px 9999px var(--rl) var(--rl)}
.t-trattoria .pair .about-img{aspect-ratio:3/3.6}
""",
    "smokehouse": """
.t-smokehouse h1,.t-smokehouse h2{text-transform:uppercase;letter-spacing:.02em;line-height:.95}
.t-smokehouse h1{font-size:clamp(3.6rem,10vw,7.5rem)}.t-smokehouse h2{font-size:clamp(2.6rem,5vw,4rem)}
.t-smokehouse .btn{text-transform:uppercase;letter-spacing:.12em;font-size:.82rem}
.t-smokehouse .feat-cards .feat-item,.t-smokehouse .menu-cards .menu-cat,.t-smokehouse .review{border-top:4px solid var(--a)}
.t-smokehouse .dish-name,.t-smokehouse .dish-body h3{text-transform:uppercase;letter-spacing:.04em}
.t-smokehouse .menu-cat h3,.t-smokehouse .dish-body h3{font-size:1.5rem}
.t-smokehouse .leader{border-bottom-style:solid;border-bottom-width:1px}
""",
    "zen": """
.t-zen .eb-caps .eyebrow,.t-zen .eyebrow{display:flex;align-items:center;gap:12px}
.t-zen .eyebrow::before{content:"";width:34px;height:1px;background:currentColor}
.t-zen .sec-head:not(.left):not(.split) .eyebrow{justify-content:center}
.t-zen .hero-split .hero-media::before{content:"";position:absolute;width:44%;aspect-ratio:1;border-radius:50%;
background:var(--accent);top:-7%;right:-6%}.t-zen .hero-split .hero-img{position:relative}
.t-zen .tab-labels label{border-width:0 0 2px;border-radius:0;padding:8px 4px;margin:0 10px}
.t-zen .tabs>input:checked~.tab-labels label{background:transparent}
.t-zen .leader{border-bottom:1px solid var(--c-line);transform:translateY(-6px)}
""",
    "cafe": """
.t-cafe .hero-split .hero-img{border-radius:46% 54% 50% 50% / 52% 44% 56% 48%}
.t-cafe .dish-label{transform:rotate(-4deg)}.t-cafe .dish-card,.t-cafe .review,.t-cafe .feat-cards .feat-item{box-shadow:0 10px 30px rgba(60,40,20,.08)}
.t-cafe h1 ,.t-cafe h2{font-weight:600}.t-cafe .menu-cat h3{font-family:var(--script),cursive;font-size:1.9rem}
""",
    "coastal": """
.t-coastal .hero-overlay::after,.t-coastal .hero-centered::after,.t-coastal .sec.bg-dark::after{content:"";position:absolute;left:0;right:0;
bottom:-1px;height:44px;background:var(--bg);-webkit-mask:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 44'><path d='M0 22 Q150 0 300 22 T600 22 V44 H0z'/></svg>") repeat-x bottom/600px 44px;
mask:url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 600 44'><path d='M0 22 Q150 0 300 22 T600 22 V44 H0z'/></svg>") repeat-x bottom/600px 44px}
.t-coastal .sec.bg-dark{padding-bottom:clamp(96px,11vw,140px)}
.t-coastal .eyebrow{letter-spacing:.3em}.t-coastal .menu-cat h3{font-style:italic}
""",
    "spice": """
.t-spice .about-img,.t-spice .chef-media img{border-radius:9999px 9999px var(--rl) var(--rl)}
.t-spice .pair .about-img{aspect-ratio:3/3.7}
.t-spice .dish-media{border-radius:9999px 9999px 0 0;margin:12px 12px 0}
.t-spice .sec-features{border-top:6px solid transparent;border-image:repeating-linear-gradient(90deg,var(--accent) 0 14px,var(--dark) 14px 28px) 6}
.t-spice .feat-ic{border-style:double;border-width:4px}
.t-spice .dish-label,.t-patisserie .dish-label{top:auto;bottom:10px;left:50%;transform:translateX(-50%);white-space:nowrap}
""",
    "patisserie": """
.t-patisserie h1,.t-patisserie h2{font-style:italic;font-weight:500}
.t-patisserie .about-img,.t-patisserie .dish-media{border-radius:9999px 9999px var(--rl) var(--rl)}
.t-patisserie .dish-card{background:transparent;text-align:center}.t-patisserie .dish-foot{justify-content:center}
.t-patisserie .dish-price{margin:0}.t-patisserie .menu-cards .menu-cat{border:1px solid var(--c-line);outline:1px solid var(--c-line);outline-offset:-7px}
""",
    "bistro": """
.t-bistro .sec-menu{border-top:1px solid var(--c-line);border-bottom:1px solid var(--c-line)}
.t-bistro .menu-cat h3{font-style:italic}.t-bistro .hero-split .hero-img{box-shadow:14px 14px 0 var(--c-card)}
""",
    "street": """
.t-street h1,.t-street h2,.t-street h3,.t-street .logo-name{text-transform:uppercase;letter-spacing:.01em}
.t-street h1{font-size:clamp(3rem,9vw,6.4rem);line-height:.95}
.t-street .btn{border:3px solid var(--c-text,var(--text));box-shadow:5px 5px 0 var(--text);border-radius:0}
.t-street .hero .btn-light{background:var(--bg);color:var(--text)}
.t-street .menu-cat{background:var(--c-card);border:3px solid var(--c-text);box-shadow:8px 8px 0 var(--c-text);padding:18px 22px}
.t-street .leader{border-bottom-style:solid;border-bottom-width:3px}
.t-street .review,.t-street .dish-card{border:3px solid var(--c-text);box-shadow:6px 6px 0 var(--accent)}
.t-street .announce{text-transform:uppercase;letter-spacing:.08em}
""",
    "minimal": """
.t-minimal h1{font-weight:700;letter-spacing:-.03em}.t-minimal h2{font-weight:700;letter-spacing:-.02em;font-size:clamp(1.6rem,3vw,2.2rem)}
.t-minimal .leader{border-bottom:1px solid var(--c-line);transform:translateY(-6px)}
.t-minimal .menu-cat h3{color:var(--c-text);text-transform:uppercase;letter-spacing:.12em;font-size:.85rem}
.t-minimal .review{background:transparent;border-left:2px solid var(--a);border-radius:0}
""",
    "night": """
.t-night h1,.t-night h2{font-weight:500;letter-spacing:.02em}.t-night h1{font-size:clamp(3rem,8vw,5.6rem)}
.t-night .menu-cat h3{text-align:center;font-size:1.6rem;border-bottom:1px solid var(--a);padding-bottom:8px}
.t-night .btn{letter-spacing:.12em;text-transform:uppercase;font-size:.8rem}
.t-night .review{border:1px solid var(--c-line);background:transparent}
""",
    "garden": """
.t-garden .hero-split .hero-img{border-radius:48% 52% 42% 58% / 55% 45% 55% 45%}.t-garden .leader{border-bottom-style:dashed}
.t-garden .menu-cat{background:var(--c-card);border-radius:var(--rl);padding:20px 24px}
.t-garden .tag{background:var(--a);color:var(--on-a);border:0;opacity:1;padding:1px 6px;border-radius:999px}
""",
}


def _social_links(links: dict, cls: str = "social") -> str:
    inner = "".join(f'<a href="{e(u)}" target="_blank" rel="noopener" aria-label="{e(k.title())}">{svg(k)}</a>'
                    for k, u in links.items() if u)
    return f'<div class="{cls}">{inner}</div>' if inner else ""


def _header(content: dict, ctx: Ctx, nav: list[tuple[str, str]], over: bool) -> str:
    site, lay = ctx.site, content["layout"]
    logo_img = _img(site, lay.get("logo"), site.name, eager=True, ed=_ei(ctx, "layout:logo"))
    sub = "" if logo_img else _txt(ctx, lay, "logo_sub", "span", "logo-sub", "Add a small line", "layout:logo_sub")
    logo = f'<a class="logo" href="#top">{logo_img or f"<span class=logo-name>{e(site.name)}</span>"}{sub}</a>'
    links = "".join(f'<a href="#{a}">{e(label)}</a>' for a, label in nav)
    kind = lay.get("header_button", "order")
    btn = ""
    if kind != "none":
        other = "order" if kind == "book" else "book"
        btn = _button(kind, ctx) or _button(other, ctx) or _button("call", ctx)
    menu_icon = svg("menu")
    mobile = (f'<details class="mnav"><summary aria-label="Open menu">{menu_icon}</summary>'
              f'<div class="mnav-panel bg-page">{links}{btn}</div></details>') if (links or btn) else ""
    style = "dark" if lay.get("header") == "dark" else "page"
    head_cls = "" if over else f"bg-{style}"
    zone = ' data-zone="header"' if ctx.edit else ""
    header = f'<header class="site-head {head_cls}"{zone}><div class="wrap hd">{logo}<nav class="nav">{links}</nav>{btn}{mobile}</div></header>'
    info = ""
    if lay.get("info_bar"):
        parts = []
        if site.address:
            parts.append(f'<span class="ib-hide">{svg("pin")}{e(site.address)}</span>')
        if site.phone:
            parts.append(f'<a href="tel:{e(site.phone)}">{svg("phone")}{e(site.phone)}</a>')
        if _hours_short(site.hours):
            parts.append(f'<span class="ib-hide">{svg("clock")}{e(_hours_short(site.hours))}</span>')
        social = _social_links(content.get("links") or {}, "ib-social")
        if parts or social:
            info = f'<div class="infobar {"" if over else "bg-dark"}"{zone}><div class="wrap ib">{"".join(parts)}{social}</div></div>'
    if over:
        return f'<div class="top-over">{info}{header}</div>'
    return info + header


def _footer(content: dict, ctx: Ctx, nav: list[tuple[str, str]]) -> str:
    site, lay = ctx.site, content["layout"]
    social = _social_links(content.get("links") or {})
    year = (site.today or "")[:4]
    zone = ' data-zone="footer"' if ctx.edit else ""
    blurb = _txt(ctx, lay, "footer_text", "p", "", "Add a short line about your restaurant", "layout:footer_text")
    if lay.get("footer") != "columns":
        return (f'<footer class="foot foot-simple bg-page"{zone}><div class="wrap">{social}{blurb}<p>© {e(year)} '
                f'{e(site.name)}{(" · " + e(site.address)) if site.address else ""}</p></div></footer>')
    about = f'<div class="foot-about"><span class="logo-name">{e(site.name)}</span>{blurb}{social}</div>'
    cols = [about]
    if nav:
        cols.append("<div><h4>Explore</h4><ul>" + "".join(f'<li><a href="#{a}">{e(label)}</a></li>' for a, label in nav)
                    + "</ul></div>")
    if "menu" in ctx.anchors:
        cols.append("<div><h4>Our menu</h4><ul>" + "".join(f'<li><a href="#menu">{e(c)}</a></li>'
                                                          for c, _ in site.menu[:6]) + "</ul></div>")
    contact = []
    if site.address:
        contact.append(f"<li>{e(site.address)}</li>")
    if site.phone:
        contact.append(f'<li><a href="tel:{e(site.phone)}">{e(site.phone)}</a></li>')
    contact += [f"<li>{e(d)}: {e(h)}</li>" for d, h in hours_groups(site.hours)] if site.hours else []
    if contact:
        cols.append(f'<div><h4>Visit us</h4><ul>{"".join(contact)}</ul></div>')
    return (f'<footer class="foot foot-cols bg-dark"{zone}><div class="wrap"><div class="foot-grid">{"".join(cols)}</div>'
            f'<div class="foot-bottom"><span>© {e(year)} {e(site.name)}. All rights reserved.</span>'
            f'<span>{e(site.cuisine or "")}</span></div></div></footer>')


def render(content: dict, site: SiteData, page_url: str = "") -> str:
    content = clean_content(content)
    template = content["template"]
    th = content["theme"]
    visible = [s for s in content["sections"] if s.get("visible")]
    anchors: set[str] = set()
    for s in visible:
        a = ANCHORS.get(s["type"])
        if a and _has_content(s["type"], s["props"], site):
            anchors.add(a)
    edit = site.edit and site.preview
    ctx = Ctx(site=site, template=template, anchors=anchors, edit=edit)
    used: set[str] = set()
    parts, first = [], None
    for s in content["sections"] if edit else visible:
        sid, kind = s["id"], s["type"]
        label = e(SECTIONS[kind]["label"])
        if not s.get("visible"):  # editor only: hidden sections stay findable
            parts.append(f'<div class="edit-bar edit-hidden" data-sid="{e(sid)}" data-type="{kind}">'
                         f'<span>Hidden: {label}</span></div>')
            continue
        a = ANCHORS.get(kind)
        anchor = a if a in anchors and a not in used else None
        if anchor:
            used.add(anchor)
        fn = RENDERERS[kind]
        ctx.sid = sid
        uid = sid.split("-")[-1]
        html = fn(s["props"], ctx, anchor, uid) if kind in NEEDS_UID else fn(s["props"], ctx, anchor)
        if html and first is None:
            first = s
        if edit:
            html = (f'<div class="edit-sec" data-sid="{e(sid)}" data-type="{kind}">{html}</div>' if html else
                    f'<div class="edit-bar edit-blank" data-sid="{e(sid)}" data-type="{kind}"><span>{label}: '
                    f'{e(BLANK_REASONS.get(kind, "nothing to show yet"))}</span></div>')
        parts.append(html)
    if edit:
        parts.append('<div class="edit-add" data-add="end"><span>+ Add a section</span></div>')
    body = "".join(parts)
    nav = [(a, label) for a, label in NAV if a in anchors]
    if "menu" not in anchors and "dishes" in anchors:
        nav.insert(1 if nav and nav[0][0] == "about" else 0, ("dishes", "Menu"))
    nav = nav[:6]
    over = (content["layout"].get("header") == "transparent" and first is not None and first["type"] == "hero"
            and first["props"].get("layout") in ("overlay", "centered"))
    fonts = "&".join(f"family={FONTS[f]}" for f in dict.fromkeys([th["heading_font"], th["body_font"], th["script_font"]]))
    seo = content.get("seo") or {}
    title = seo.get("title") or site.name
    desc = seo.get("description") or ""
    flag = ('<div class="preview-flag">Preview: only you can see this until you publish</div>'
            if site.preview and not edit else "")
    robots = '<meta name="robots" content="noindex">' if site.preview else ""
    canonical = f'<link rel="canonical" href="{e(page_url)}">' if page_url and not site.preview else ""
    body_cls = (f"t-{e(template)} eb-{e(th.get('eyebrow', 'caps'))}{' is-preview' if site.preview and not edit else ''}"
                f"{' is-editing' if edit else ''}")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">{robots}{canonical}
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?{fonts}&display=swap" rel="stylesheet">
<style>{theme_css(th)}{BASE_CSS}{NTH_CSS}{TEMPLATE_CSS.get(template, "")}{EDIT_CSS if edit else ""}</style></head>
<body class="{body_cls}">{flag}<div class="page {"has-over" if over else ""}">
{_header(content, ctx, nav, over)}
<main>{body}</main>
{_footer(content, ctx, nav)}
</div></body></html>"""


def not_found_page(message: str = "This restaurant's website isn't published yet.") -> str:
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, '
            f'initial-scale=1"><title>Not found</title><style>body{{font:18px system-ui;display:grid;place-items:center;'
            f'min-height:90vh;color:#444;text-align:center;padding:20px}}</style></head><body><p>{e(message)}</p></body></html>')
