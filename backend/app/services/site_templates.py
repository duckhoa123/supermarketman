"""Website templates and the content format. Pure functions, no database.

A site's content is JSON the owner edits in the app:
  {"template", "theme": {accent, background, text, heading_font, body_font}, "seo": {title, description},
   "links": {instagram, facebook, tiktok}, "sections": [{"id", "type", "visible", "props"}]}
Everything is checked by clean_content(): no HTML or CSS from the owner ever reaches the page, colours must be
hex, fonts come from a fixed list, links must be http(s), images must be the restaurant's own uploads.
"""

import copy
import re
import uuid

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
ASSET_ID = re.compile(r"^[0-9a-f-]{36}$")
MAX_SECTIONS = 20

FONTS = {
    "Playfair Display": "Playfair+Display:wght@500;700",
    "Source Sans 3": "Source+Sans+3:wght@400;600",
    "Archivo Black": "Archivo+Black",
    "Space Grotesk": "Space+Grotesk:wght@400;600",
    "Inter": "Inter:wght@400;600;700",
    "Cormorant Garamond": "Cormorant+Garamond:wght@500;700",
    "Montserrat": "Montserrat:wght@300;400;600",
    "Nunito": "Nunito:wght@400;700;800",
    "Fraunces": "Fraunces:wght@500;700",
    "DM Serif Display": "DM+Serif+Display",
    "Lora": "Lora:wght@400;600",
    "Poppins": "Poppins:wght@400;600;700",
}

SECTION_TYPES = ("hero", "announcement", "about", "menu", "gallery", "reviews", "visit", "cta")
SECTION_LABELS = {
    "hero": "Big welcome", "announcement": "Announcement bar", "about": "Our story", "menu": "Menu",
    "gallery": "Photo gallery", "reviews": "Guest reviews", "visit": "Hours and location", "cta": "Order or book",
}

TEMPLATES: dict[str, dict] = {
    "bistro": {
        "label": "Bistro", "description": "Warm and classic: cream paper, serif headings, a menu with dotted price lines.",
        "theme": {"accent": "#b4532a", "background": "#f7f1e8", "text": "#2b211b",
                  "heading_font": "Playfair Display", "body_font": "Source Sans 3"},
        "order": ["announcement", "hero", "about", "menu", "reviews", "gallery", "visit"],
    },
    "street": {
        "label": "Street", "description": "Loud and fun: big block letters, bold borders, menu cards that pop.",
        "theme": {"accent": "#ff4d00", "background": "#fff3cf", "text": "#111111",
                  "heading_font": "Archivo Black", "body_font": "Space Grotesk"},
        "order": ["hero", "announcement", "menu", "cta", "gallery", "reviews", "visit"],
    },
    "minimal": {
        "label": "Minimal", "description": "Clean and calm: lots of white space and simple lines. Lets the food speak.",
        "theme": {"accent": "#2f5d50", "background": "#ffffff", "text": "#151515",
                  "heading_font": "Inter", "body_font": "Inter"},
        "order": ["hero", "menu", "about", "visit", "reviews"],
    },
    "night": {
        "label": "Night", "description": "Dark and elegant: gold details for wine bars, fine dining and date nights.",
        "theme": {"accent": "#c9a45c", "background": "#121212", "text": "#f3ece0",
                  "heading_font": "Cormorant Garamond", "body_font": "Montserrat"},
        "order": ["hero", "about", "menu", "gallery", "reviews", "cta", "visit"],
    },
    "garden": {
        "label": "Garden", "description": "Fresh and friendly: soft greens and rounded cards for cafes and family places.",
        "theme": {"accent": "#3f8f5a", "background": "#f3f8f1", "text": "#1f2d24",
                  "heading_font": "Fraunces", "body_font": "Nunito"},
        "order": ["hero", "about", "menu", "cta", "gallery", "reviews", "visit"],
    },
}


def default_props(kind: str, name: str = "", cuisine: str | None = None) -> dict:
    return {
        "hero": {"title": name, "tagline": f"{cuisine} in the neighbourhood" if cuisine else "Good food, close to home",
                 "image": None, "order_button": True, "booking_button": True},
        "announcement": {"text": ""},
        "about": {"heading": "Our story", "body": "", "image": None},
        "menu": {"heading": "Menu", "categories": [], "show_prices": True, "show_descriptions": True,
                 "show_dietary": True},
        "gallery": {"heading": "Gallery", "images": []},
        "reviews": {"heading": "What guests say", "min_rating": 4, "count": 6},
        "visit": {"heading": "Hours and location", "show_map": True},
        "cta": {"heading": "Hungry?", "text": "Order online for pickup or delivery, or book a table.", "button": "order"},
    }[kind]


def new_section(kind: str, name: str = "", cuisine: str | None = None) -> dict:
    return {"id": f"{kind}-{uuid.uuid4().hex[:8]}", "type": kind, "visible": kind != "announcement",
            "props": default_props(kind, name, cuisine)}


def starter_content(template: str, name: str, cuisine: str | None = None) -> dict:
    t = TEMPLATES.get(template) or TEMPLATES["bistro"]
    return {
        "template": template if template in TEMPLATES else "bistro",
        "theme": dict(t["theme"]),
        "seo": {"title": name,
                "description": f"{name}: {cuisine or 'restaurant'}. Menu, hours, online ordering and bookings."},
        "links": {"instagram": "", "facebook": "", "tiktok": ""},
        "sections": [new_section(k, name, cuisine) for k in t["order"]],
    }


def apply_template(content: dict, template: str) -> dict:
    """Switch template: the look (colours, fonts) changes, the owner's words, photos and sections stay.
    Sections the new template features but the site doesn't have yet are added, hidden."""
    if template not in TEMPLATES:
        raise ValueError("Unknown template")
    out = copy.deepcopy(content)
    out["template"] = template
    out["theme"] = dict(TEMPLATES[template]["theme"])
    have = {s["type"] for s in out.get("sections", [])}
    for kind in TEMPLATES[template]["order"]:
        if kind not in have:
            s = new_section(kind)
            s["visible"] = False
            out.setdefault("sections", []).append(s)
    return out


# --- Validation ------------------------------------------------------------------------------------------


def _text(value, limit: int) -> str:
    return str(value or "").replace("\r", "").strip()[:limit]


def _bool(value, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _image(value) -> str | None:
    return value if isinstance(value, str) and ASSET_ID.match(value) else None


def _url(value) -> str:
    v = _text(value, 300)
    return v if v.startswith(("https://", "http://")) and " " not in v and '"' not in v else ""


def clean_content(raw: dict) -> dict:
    """Return a safe, complete copy of site content, dropping anything unknown or invalid."""
    if not isinstance(raw, dict):
        raise ValueError("Site content must be an object")
    template = raw.get("template") if raw.get("template") in TEMPLATES else "bistro"
    base = TEMPLATES[template]["theme"]
    theme_in = raw.get("theme") if isinstance(raw.get("theme"), dict) else {}
    theme = {}
    for key in ("accent", "background", "text"):
        v = theme_in.get(key)
        theme[key] = v if isinstance(v, str) and HEX.match(v) else base[key]
    for key in ("heading_font", "body_font"):
        v = theme_in.get(key)
        theme[key] = v if v in FONTS else base[key]
    seo_in = raw.get("seo") if isinstance(raw.get("seo"), dict) else {}
    links_in = raw.get("links") if isinstance(raw.get("links"), dict) else {}
    sections = []
    for s in (raw.get("sections") or [])[:MAX_SECTIONS]:
        if not isinstance(s, dict) or s.get("type") not in SECTION_TYPES:
            continue
        kind = s["type"]
        p_in = s.get("props") if isinstance(s.get("props"), dict) else {}
        d = default_props(kind)
        if kind == "hero":
            props = {"title": _text(p_in.get("title"), 80), "tagline": _text(p_in.get("tagline"), 160),
                     "image": _image(p_in.get("image")),
                     "order_button": _bool(p_in.get("order_button"), True),
                     "booking_button": _bool(p_in.get("booking_button"), True)}
        elif kind == "announcement":
            props = {"text": _text(p_in.get("text"), 160)}
        elif kind == "about":
            props = {"heading": _text(p_in.get("heading"), 80) or d["heading"], "body": _text(p_in.get("body"), 2000),
                     "image": _image(p_in.get("image"))}
        elif kind == "menu":
            cats = p_in.get("categories") if isinstance(p_in.get("categories"), list) else []
            props = {"heading": _text(p_in.get("heading"), 80) or d["heading"],
                     "categories": [_text(c, 100) for c in cats if _text(c, 100)][:30],
                     "show_prices": _bool(p_in.get("show_prices"), True),
                     "show_descriptions": _bool(p_in.get("show_descriptions"), True),
                     "show_dietary": _bool(p_in.get("show_dietary"), True)}
        elif kind == "gallery":
            imgs = p_in.get("images") if isinstance(p_in.get("images"), list) else []
            props = {"heading": _text(p_in.get("heading"), 80), "images": [i for i in map(_image, imgs) if i][:12]}
        elif kind == "reviews":
            try:
                min_rating = max(1, min(5, int(p_in.get("min_rating", 4))))
                count = max(1, min(12, int(p_in.get("count", 6))))
            except (TypeError, ValueError):
                min_rating, count = 4, 6
            props = {"heading": _text(p_in.get("heading"), 80) or d["heading"], "min_rating": min_rating, "count": count}
        elif kind == "visit":
            props = {"heading": _text(p_in.get("heading"), 80) or d["heading"],
                     "show_map": _bool(p_in.get("show_map"), True)}
        else:  # cta
            props = {"heading": _text(p_in.get("heading"), 80), "text": _text(p_in.get("text"), 300),
                     "button": p_in.get("button") if p_in.get("button") in ("order", "book", "call") else "order"}
        sid = s.get("id") if isinstance(s.get("id"), str) and re.match(r"^[a-z]+-[0-9a-f]{4,12}$", s["id"]) else None
        sections.append({"id": sid or new_section(kind)["id"], "type": kind,
                         "visible": _bool(s.get("visible"), True), "props": props})
    return {
        "template": template, "theme": theme,
        "seo": {"title": _text(seo_in.get("title"), 70), "description": _text(seo_in.get("description"), 160)},
        "links": {k: _url(links_in.get(k)) for k in ("instagram", "facebook", "tiktok")},
        "sections": sections,
    }


def image_ids(content: dict) -> set[str]:
    out = set()
    for s in content.get("sections", []):
        p = s.get("props", {})
        if p.get("image"):
            out.add(p["image"])
        out.update(p.get("images") or [])
    return out


SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,58}[a-z0-9])$")
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:50]
    return s if len(s) >= 3 else f"restaurant-{s}".strip("-")
