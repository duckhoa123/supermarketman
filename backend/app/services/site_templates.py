"""Website templates and the content format. Pure functions, no database.

A site's content is JSON the owner edits in the app:
  {"template", "theme": {colours, fonts, corners, eyebrow}, "layout": {header, footer, logo...},
   "seo": {title, description}, "links": {instagram, facebook, tiktok},
   "sections": [{"id", "type", "visible", "props"}]}

Every section type declares its fields once (SECTIONS). The same declaration checks what the owner saves
(clean_content) and builds the editor's forms in the app. No HTML or CSS from the owner ever reaches the page:
colours must be hex, fonts and icons come from fixed lists, links must be http(s), and images must be the
restaurant's own uploads.
"""

import copy
import re
import uuid

from app.services.site_icons import ICONS

HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
UUID = re.compile(r"^[0-9a-f-]{36}$")
ASSET_ID = UUID
MAX_SECTIONS = 24

FONTS = {
    # Headings and text
    "Playfair Display": "Playfair+Display:ital,wght@0,500;0,700;1,500",
    "Source Sans 3": "Source+Sans+3:wght@400;600",
    "Archivo Black": "Archivo+Black",
    "Space Grotesk": "Space+Grotesk:wght@400;600",
    "Inter": "Inter:wght@400;600;700",
    "Cormorant Garamond": "Cormorant+Garamond:ital,wght@0,500;0,700;1,500",
    "Montserrat": "Montserrat:wght@300;400;600",
    "Nunito": "Nunito:wght@400;700;800",
    "Fraunces": "Fraunces:ital,wght@0,500;0,700;1,500",
    "DM Serif Display": "DM+Serif+Display:ital@0;1",
    "Lora": "Lora:ital,wght@0,400;0,600;1,400",
    "Poppins": "Poppins:wght@400;500;600",
    "Lato": "Lato:wght@400;700",
    "DM Sans": "DM+Sans:wght@400;500;700",
    "Work Sans": "Work+Sans:wght@400;500;600",
    "Jost": "Jost:wght@400;500;600",
    "Barlow": "Barlow:wght@400;500;700",
    "Oswald": "Oswald:wght@400;600",
    "Bebas Neue": "Bebas+Neue",
    "Libre Baskerville": "Libre+Baskerville:ital,wght@0,400;0,700;1,400",
    "Bodoni Moda": "Bodoni+Moda:ital,wght@0,500;0,700;1,500",
    "Yeseva One": "Yeseva+One",
    "Shippori Mincho": "Shippori+Mincho:wght@500;700",
    "Noto Sans": "Noto+Sans:wght@400;600",
    "Marcellus": "Marcellus",
    "Cinzel": "Cinzel:wght@500;700",
    "Raleway": "Raleway:wght@400;600;700",
    "Josefin Sans": "Josefin+Sans:wght@400;600",
    # Script, for small decorative lines
    "Great Vibes": "Great+Vibes",
    "Dancing Script": "Dancing+Script:wght@500;700",
    "Caveat": "Caveat:wght@500;700",
    "Allura": "Allura",
    "Pinyon Script": "Pinyon+Script",
    "Kaushan Script": "Kaushan+Script",
    "Satisfy": "Satisfy",
}
SCRIPT_FONTS = ["Great Vibes", "Dancing Script", "Caveat", "Allura", "Pinyon Script", "Kaushan Script", "Satisfy"]


# --- Field declarations --------------------------------------------------------------------------------------


def text(label: str, limit: int, default: str = "", *, long: bool = False, fallback: bool = False,
         placeholder: str = "") -> dict:
    """fallback=True: an empty value goes back to the default (for headings that shouldn't vanish)."""
    return {"kind": "text", "label": label, "limit": limit, "default": default, "long": long,
            "fallback": fallback, "placeholder": placeholder}


def flag(label: str, default: bool) -> dict:
    return {"kind": "bool", "label": label, "default": default}


def image(label: str) -> dict:
    return {"kind": "image", "label": label, "default": None}


def images(label: str, limit: int) -> dict:
    return {"kind": "images", "label": label, "limit": limit, "default": []}


def choice(label: str, options: list[tuple[str, str]], default: str) -> dict:
    return {"kind": "choice", "label": label, "options": [{"value": v, "label": lbl} for v, lbl in options],
            "default": default}


def number(label: str, lo: int, hi: int, default: int) -> dict:
    return {"kind": "int", "label": label, "min": lo, "max": hi, "default": default}


def icon(label: str, default: str) -> dict:
    return {"kind": "icon", "label": label, "default": default}


def items(label: str, limit: int, fields: dict, default: list | None = None, add_label: str = "Add") -> dict:
    return {"kind": "items", "label": label, "limit": limit, "fields": fields, "default": default or [],
            "add_label": add_label}


BACKGROUNDS = [("page", "Page colour"), ("alt", "Soft tint"), ("dark", "Dark"), ("accent", "Accent colour")]


def bg(default: str = "page") -> dict:
    return choice("Background", BACKGROUNDS, default)


def eyebrow(default: str = "") -> dict:
    return text("Small line above the heading", 60, default)


BUTTONS = [("none", "No button"), ("menu", "View the menu"), ("order", "Order online"), ("book", "Book a table"),
           ("call", "Call us")]

SECTIONS: dict[str, dict] = {
    "hero": {"label": "Big welcome", "icon": "★", "ai": True, "fields": {
        "layout": choice("Layout", [("overlay", "Photo behind the text"), ("centered", "Photo behind, text centred"),
                                    ("split", "Text and photo side by side"), ("arch", "Photo in an arch")], "split"),
        "eyebrow": text("Small line above the title", 60, placeholder="Good food, good mood"),
        "title": text("Headline", 80),
        "tagline": text("Tagline", 200),
        "image": image("Photo"),
        "order_button": flag("“Order online” button", True),
        "booking_button": flag("“Book a table” button", True),
        "bg": bg(),
    }},
    "announcement": {"label": "Announcement bar", "icon": "📣", "fields": {
        "text": text("Message", 160, placeholder="Now open for brunch on Sundays"),
        "bg": choice("Background", [("accent", "Accent colour"), ("dark", "Dark")], "accent"),
    }},
    "features": {"label": "Highlights", "icon": "✦", "fields": {
        "eyebrow": eyebrow(),
        "heading": text("Heading (optional)", 80),
        "items": items("Highlights", 6, {
            "icon": icon("Icon", "leaf"), "title": text("Title", 40), "text": text("Text", 100),
        }, add_label="Add a highlight"),
        "layout": choice("Layout", [("icons", "Icons in a row"), ("cards", "Cards")], "icons"),
        "bg": bg("dark"),
    }},
    "about": {"label": "Our story", "icon": "📖", "ai": True, "fields": {
        "eyebrow": eyebrow(),
        "heading": text("Heading", 80, "Our story", fallback=True),
        "body": text("Text (leave a blank line between paragraphs)", 2000, long=True),
        "image": image("Photo"),
        "image2": image("Second, smaller photo (optional)"),
        "badge": text("Round badge text (optional)", 40, placeholder="Made with love · Since 2015"),
        "signoff": text("Handwritten line (optional)", 60, placeholder="Come hungry, leave happy!"),
        "button": choice("Button", BUTTONS, "none"),
        "layout": choice("Layout", [("images-left", "Photos on the left"), ("images-right", "Photos on the right")],
                         "images-right"),
        "bg": bg(),
    }},
    "dishes": {"label": "Signature dishes", "icon": "🍝",
               "help": "Leave the list empty to show your four best sellers. Prices always come from your menu.",
               "fields": {
                   "eyebrow": eyebrow(),
                   "heading": text("Heading", 80, "Signature dishes", fallback=True),
                   "items": items("Dishes", 8, {
                       "item": {"kind": "dish", "label": "Dish from your menu", "default": None},
                       "image": image("Photo"),
                       "label": text("Label (optional)", 20, placeholder="Chef's pick"),
                   }, add_label="Add a dish"),
                   "button": choice("Button", [("none", "No button"), ("menu", "View the full menu"),
                                               ("order", "Order online")], "menu"),
                   "layout": choice("Layout", [("cards", "Cards"), ("overlay", "Text over the photo")], "cards"),
                   "bg": bg(),
               }},
    "menu": {"label": "Menu", "icon": "🍽",
             "help": "Dishes, prices and descriptions come from your POS automatically. To change them, edit the "
                     "item in Stock (or in your POS); the site updates within a minute, no need to publish. "
                     "The “Add” buttons show once online ordering is on (Settings → Customer app).",
             "fields": {
                 "eyebrow": eyebrow(),
                 "heading": text("Heading", 80, "Menu", fallback=True),
                 "categories": {"kind": "categories", "label": "Categories to show (none ticked = all)",
                                "default": []},
                 "show_prices": flag("Show prices", True),
                 "show_descriptions": flag("Show descriptions", True),
                 "show_dietary": flag("Show dietary labels (V, VG, GF)", True),
                 "order_buttons": flag("“Add” button next to each dish, to order online", True),
                 "layout": choice("Layout", [("columns", "Columns with price lines"),
                                             ("tabs", "A tab for each category"),
                                             ("cards", "A card for each category")], "columns"),
                 "bg": bg(),
             }},
    "occasions": {"label": "Events and occasions", "icon": "🎉", "fields": {
        "eyebrow": eyebrow("Perfect place for"),
        "heading": text("Heading", 80, "Every occasion"),
        "text": text("Text", 300),
        "image": image("Background photo (optional)"),
        "items": items("Occasions", 6, {"icon": icon("Icon", "heart"), "title": text("Title", 40)},
                       add_label="Add an occasion"),
        "button": choice("Button", BUTTONS, "book"),
        "bg": bg("dark"),
    }},
    "reserve": {"label": "Booking form", "icon": "📅",
                "help": "Guests pick a date, time and party size, then confirm in the Pass app. Turn on bookings "
                        "in Settings → Customer app; until then the form shows your phone number.",
                "fields": {
                    "eyebrow": eyebrow("Reserve your table"),
                    "heading": text("Heading", 80, "Book a table", fallback=True),
                    "text": text("Text", 300),
                    "show_reviews": flag("Show guest reviews beside the form", True),
                    "reviews_heading": text("Reviews heading", 60, "What our guests say"),
                    "min_rating": number("Only reviews with at least this many stars", 3, 5, 4),
                    "bg": bg("alt"),
                }},
    "chef": {"label": "Meet the chef", "icon": "👩‍🍳", "fields": {
        "eyebrow": eyebrow("Meet the chef"),
        "name": text("Name", 60),
        "role": text("Role", 60, placeholder="Head chef and owner"),
        "body": text("A few words from the chef", 1200, long=True),
        "image": image("Portrait"),
        "layout": choice("Layout", [("image-left", "Photo on the left"), ("image-right", "Photo on the right")],
                         "image-left"),
        "bg": bg(),
    }},
    "gallery": {"label": "Photo gallery", "icon": "🖼", "fields": {
        "eyebrow": eyebrow(),
        "heading": text("Heading", 80, "Gallery"),
        "images": images("Photos (tap to add or remove)", 12),
        "layout": choice("Layout", [("grid", "Even grid"), ("mosaic", "Mosaic"), ("strip", "Scrolling strip")],
                         "grid"),
        "bg": bg(),
    }},
    "reviews": {"label": "Guest reviews", "icon": "💬", "help": "Your latest Google reviews, shown automatically.",
                "fields": {
                    "eyebrow": eyebrow(),
                    "heading": text("Heading", 80, "What guests say", fallback=True),
                    "min_rating": number("Only reviews with at least this many stars", 3, 5, 4),
                    "count": number("Show up to", 1, 12, 6),
                    "layout": choice("Layout", [("cards", "Cards"), ("slider", "One at a time")], "cards"),
                    "bg": bg(),
                }},
    "visit": {"label": "Hours and location", "icon": "📍",
              "help": "Hours, address and phone come from Settings → Customer app and delivery.", "fields": {
                  "eyebrow": eyebrow(),
                  "heading": text("Heading", 80, "Hours and location", fallback=True),
                  "show_map": flag("Show a map", True),
                  "bg": bg(),
              }},
    "cta": {"label": "Order or book banner", "icon": "👉", "fields": {
        "eyebrow": eyebrow(),
        "heading": text("Heading", 80, "Hungry?"),
        "text": text("Text", 300, "Order online for pickup or delivery, or book a table."),
        "button": choice("Button", [("order", "Order online"), ("book", "Book a table"), ("call", "Call us"),
                                    ("menu", "View the menu")], "order"),
        "image": image("Background photo (optional)"),
        "bg": bg("alt"),
    }},
}
SECTION_TYPES = tuple(SECTIONS)
SECTION_LABELS = {k: v["label"] for k, v in SECTIONS.items()}

THEME_FIELDS = {
    "corners": choice("Corners", [("square", "Square"), ("soft", "Slightly rounded"), ("round", "Round")], "soft"),
    "eyebrow": choice("Small lines above headings", [("script", "Handwritten"), ("caps", "Small capitals")], "caps"),
}

LAYOUT_FIELDS = {
    "header": choice("Header", [("solid", "Page colour"), ("dark", "Dark"),
                                ("transparent", "See-through, over the welcome photo")], "solid"),
    "info_bar": flag("Top strip with address, phone and hours", False),
    "header_button": choice("Header button", [("order", "Order online"), ("book", "Book a table"),
                                              ("none", "No button")], "order"),
    "logo": image("Logo (optional; your name is shown if there's none)"),
    "logo_sub": text("Small line under your name", 30, placeholder="Kitchen & Bar"),
    "footer": choice("Footer", [("columns", "Columns: links, menu, hours and contact"), ("simple", "Simple")],
                     "columns"),
    "footer_text": text("Footer text", 200),
}

# The separate menu page (/menu). Like the Menu section, it only decides how the menu looks: dishes, prices,
# descriptions and dietary labels always come from the POS (or Stock in Pass), read live when the page is served.
_MENU = SECTIONS["menu"]["fields"]
MENU_PAGE_FIELDS = {
    "enabled": flag("Show a separate menu page", False),
    "eyebrow": eyebrow(),
    "heading": text("Heading", 80, "Our menu", fallback=True),
    "intro": text("Short line under the heading (optional)", 300,
                  placeholder="Everything is made fresh in our kitchen."),
    "categories": copy.deepcopy(_MENU["categories"]),
    "jump_links": flag("Category links at the top", True),
    "show_prices": copy.deepcopy(_MENU["show_prices"]),
    "show_descriptions": copy.deepcopy(_MENU["show_descriptions"]),
    "show_dietary": copy.deepcopy(_MENU["show_dietary"]),
    "order_buttons": copy.deepcopy(_MENU["order_buttons"]),
    "layout": choice("Layout", [("columns", "Columns with price lines"), ("cards", "A card for each category"),
                                ("tabs", "A tab for each category")], "columns"),
    "bg": choice("Heading background", BACKGROUNDS, "alt"),
}


def menu_page_on(content: dict | None) -> bool:
    return bool(((content or {}).get("menu_page") or {}).get("enabled") is True)


# --- Templates ------------------------------------------------------------------------------------------------

FEATURES_FINE = [
    {"icon": "leaf", "title": "Fresh ingredients", "text": "Locally sourced and carefully prepared."},
    {"icon": "chef", "title": "Expert chefs", "text": "Passionate cooks crafting every plate."},
    {"icon": "heart", "title": "Warm ambience", "text": "A cozy place for friends, family and celebrations."},
    {"icon": "star", "title": "Great service", "text": "We serve happiness on every plate."},
]
OCCASIONS = [
    {"icon": "heart", "title": "Romantic dinners"}, {"icon": "users", "title": "Family gatherings"},
    {"icon": "gift", "title": "Birthday parties"}, {"icon": "briefcase", "title": "Corporate events"},
]


def _simple(order: list[str], hero_layout: str) -> list:
    return [(k, {"layout": hero_layout} if k == "hero" else {}) for k in order]


SIMPLE_LAYOUT = {"header": "solid", "info_bar": False, "header_button": "order", "footer": "simple"}

TEMPLATES: dict[str, dict] = {
    # Photo-led templates -----------------------------------------------------------------------------------
    "gourmet": {
        "label": "Gourmet", "group": "photo", "best_for": "Fine dining, steakhouses, date nights",
        "description": "Dark and golden, with a full-screen photo, handwritten touches and elegant serif type.",
        "theme": {"accent": "#c8a24a", "background": "#f7f2ea", "text": "#1d1a16", "dark": "#15120f",
                  "heading_font": "Playfair Display", "body_font": "Lato", "script_font": "Great Vibes",
                  "corners": "soft", "eyebrow": "script"},
        "layout": {"header": "transparent", "info_bar": True, "header_button": "book", "footer": "columns"},
        "sections": [
            ("hero", {"layout": "overlay", "eyebrow": "Good food, good mood", "title": "Delicious food, great experience",
                      "tagline": "Savour a perfect blend of great flavours, a warm room and unforgettable moments."}),
            ("features", {"items": FEATURES_FINE, "bg": "dark"}),
            ("about", {"eyebrow": "About us", "heading": "Where every meal tells a story", "layout": "images-left",
                       "badge": "Made with love · Served with joy", "signoff": "Come hungry, leave happy!",
                       "button": "menu"}),
            ("dishes", {"eyebrow": "Our signature dishes", "heading": "Chef's recommendations"}),
            ("occasions", {"text": "From intimate dinners to grand celebrations, we make every moment special.",
                           "items": OCCASIONS}),
            ("menu", {"eyebrow": "Taste the difference", "heading": "Our menu", "layout": "tabs", "bg": "alt"}),
            ("reserve", {"heading": "Book a table for a great dining experience", "bg": "page"}),
            ("gallery", {"eyebrow": "Inside", "heading": "Moments at our table", "layout": "mosaic", "bg": "alt"}),
            ("visit", {"eyebrow": "Find us"}),
        ],
    },
    "trattoria": {
        "label": "Trattoria", "group": "photo", "best_for": "Italian, pizza, family kitchens",
        "description": "Warm and rustic: terracotta, cream and olive, photos in arches, handwritten notes.",
        "theme": {"accent": "#b5442b", "background": "#faf4ea", "text": "#2c241d", "dark": "#2f3524",
                  "heading_font": "DM Serif Display", "body_font": "Lato", "script_font": "Dancing Script",
                  "corners": "soft", "eyebrow": "script"},
        "layout": {"header": "solid", "info_bar": True, "header_button": "order", "footer": "columns"},
        "sections": [
            ("announcement", {}),
            ("hero", {"layout": "arch", "eyebrow": "Benvenuti!"}),
            ("features", {"bg": "alt", "items": [
                {"icon": "pizza", "title": "From our oven", "text": "Baked to order, every time."},
                {"icon": "leaf", "title": "Fresh every day", "text": "Simple ingredients, cooked with care."},
                {"icon": "wine", "title": "Wine and aperitivo", "text": "A glass for every plate."},
                {"icon": "users", "title": "Family style", "text": "Big tables and plates to share."}]}),
            ("about", {"eyebrow": "La nostra storia", "layout": "images-right", "signoff": "Mangia bene, ridi spesso",
                       "button": "book"}),
            ("dishes", {"eyebrow": "Dalla cucina", "heading": "From our kitchen", "layout": "overlay"}),
            ("menu", {"eyebrow": "Il menù", "layout": "columns"}),
            ("reviews", {"eyebrow": "Grazie!", "layout": "slider", "bg": "dark"}),
            ("gallery", {"heading": "", "layout": "strip"}),
            ("cta", {"heading": "Pull up a chair", "text": "Book a table or order for pickup tonight.",
                     "button": "book", "bg": "accent"}),
            ("visit", {"eyebrow": "Come see us"}),
        ],
    },
    "smokehouse": {
        "label": "Smokehouse", "group": "photo", "best_for": "BBQ, burgers, grills, pubs",
        "description": "Bold and smoky: charcoal, flame orange and tall block letters.",
        "theme": {"accent": "#f2662b", "background": "#1b1816", "text": "#f3ece4", "dark": "#0e0c0b",
                  "heading_font": "Bebas Neue", "body_font": "Barlow", "script_font": "Kaushan Script",
                  "corners": "square", "eyebrow": "caps"},
        "layout": {"header": "dark", "info_bar": False, "header_button": "order", "footer": "columns"},
        "sections": [
            ("hero", {"layout": "centered", "eyebrow": "Fire, smoke and good times",
                      "tagline": "Smoked meats, cold drinks and big plates made for sharing."}),
            ("features", {"layout": "cards", "bg": "page", "items": [
                {"icon": "fire", "title": "Smoked in-house", "text": "Low and slow, the way it should be."},
                {"icon": "utensils", "title": "Made to share", "text": "Big plates for the whole table."},
                {"icon": "cocktail", "title": "Cold drinks", "text": "Beer, cocktails and something for everyone."},
                {"icon": "truck", "title": "Pickup and delivery", "text": "Order online and we'll have it ready."}]}),
            ("dishes", {"eyebrow": "Crowd favourites", "heading": "Fan favourites", "layout": "overlay",
                        "button": "order"}),
            ("about", {"eyebrow": "Our story", "heading": "Fire, smoke and good company", "layout": "images-right",
                       "bg": "dark"}),
            ("menu", {"layout": "cards"}),
            ("occasions", {"eyebrow": "Bring the crew", "heading": "Big groups welcome",
                           "text": "Team nights, birthdays and game days: we'll save you a big table.",
                           "items": [{"icon": "users", "title": "Team nights"}, {"icon": "gift", "title": "Birthdays"},
                                     {"icon": "music", "title": "Game nights"},
                                     {"icon": "briefcase", "title": "Large groups"}]}),
            ("reviews", {"heading": "What people are saying"}),
            ("cta", {"heading": "Hungry yet?", "button": "order", "bg": "accent"}),
            ("visit", {}),
        ],
    },
    "zen": {
        "label": "Zen", "group": "photo", "best_for": "Sushi, ramen, Japanese, Korean",
        "description": "Quiet and precise: ink black, rice-paper white and a single red accent.",
        "theme": {"accent": "#c8102e", "background": "#f6f3ee", "text": "#161616", "dark": "#111111",
                  "heading_font": "Shippori Mincho", "body_font": "Noto Sans", "script_font": "Allura",
                  "corners": "square", "eyebrow": "caps"},
        "layout": {"header": "solid", "info_bar": False, "header_button": "book", "footer": "simple"},
        "sections": [
            ("hero", {"layout": "split", "eyebrow": "Welcome"}),
            ("features", {"bg": "page", "items": [
                {"icon": "fish", "title": "Fresh fish", "text": "Chosen with care, cut to order."},
                {"icon": "bowl", "title": "Made by hand", "text": "Broths, sauces and dough made in our kitchen."},
                {"icon": "leaf", "title": "In season", "text": "A menu that follows the seasons."}]}),
            ("about", {"eyebrow": "Our kitchen", "layout": "images-right"}),
            ("chef", {"layout": "image-left", "bg": "dark"}),
            ("dishes", {"eyebrow": "Omakase picks", "heading": "Chef's selection"}),
            ("menu", {"layout": "tabs"}),
            ("gallery", {"heading": "", "layout": "grid"}),
            ("reviews", {"layout": "slider", "bg": "alt"}),
            ("reserve", {"bg": "page"}),
            ("visit", {}),
        ],
    },
    "cafe": {
        "label": "Café", "group": "photo", "best_for": "Brunch, coffee, bakeries",
        "description": "Bright and friendly: soft colours, rounded shapes and handwritten notes.",
        "theme": {"accent": "#d4633c", "background": "#fbf3e9", "text": "#3b2a20", "dark": "#3b2a20",
                  "heading_font": "Fraunces", "body_font": "DM Sans", "script_font": "Caveat",
                  "corners": "round", "eyebrow": "script"},
        "layout": {"header": "solid", "info_bar": True, "header_button": "order", "footer": "columns"},
        "sections": [
            ("announcement", {}),
            ("hero", {"layout": "split", "eyebrow": "Hello, neighbour!"}),
            ("features", {"layout": "cards", "bg": "alt", "items": [
                {"icon": "coffee", "title": "Good coffee", "text": "Made with care, cup after cup."},
                {"icon": "cake", "title": "Baked fresh", "text": "Treats from our oven every morning."},
                {"icon": "leaf", "title": "Fresh ingredients", "text": "Simple food, done well."},
                {"icon": "heart", "title": "Friendly faces", "text": "Come as you are and stay a while."}]}),
            ("about", {"eyebrow": "Our little story", "layout": "images-left", "badge": "Fresh · Local · Homemade",
                       "signoff": "See you soon!"}),
            ("dishes", {"eyebrow": "Everyone's favourites", "heading": "Brunch favourites"}),
            ("menu", {"layout": "cards", "bg": "alt"}),
            ("gallery", {"eyebrow": "Say cheese", "heading": "Around the café", "layout": "mosaic"}),
            ("reviews", {"eyebrow": "Kind words"}),
            ("visit", {"eyebrow": "Pop by"}),
        ],
    },
    "coastal": {
        "label": "Coastal", "group": "photo", "best_for": "Seafood, oyster bars, patios",
        "description": "Fresh and breezy: deep navy, warm sand and coral, with gentle wave edges.",
        "theme": {"accent": "#d9644a", "background": "#f5f0e6", "text": "#13283b", "dark": "#0f2a43",
                  "heading_font": "Libre Baskerville", "body_font": "Work Sans", "script_font": "Allura",
                  "corners": "soft", "eyebrow": "caps"},
        "layout": {"header": "transparent", "info_bar": False, "header_button": "book", "footer": "columns"},
        "sections": [
            ("hero", {"layout": "overlay", "eyebrow": "From the coast"}),
            ("features", {"bg": "page", "items": [
                {"icon": "fish", "title": "Seafood", "text": "From the water to your plate."},
                {"icon": "cocktail", "title": "Drinks", "text": "Wine, beer and cocktails."},
                {"icon": "sun", "title": "Long lunches", "text": "Settle in and take your time."},
                {"icon": "users", "title": "Groups", "text": "Room for the whole crew."}]}),
            ("dishes", {"eyebrow": "Fresh catch", "heading": "Guest favourites", "layout": "overlay"}),
            ("about", {"eyebrow": "Our story", "layout": "images-right", "bg": "alt"}),
            ("menu", {"layout": "columns"}),
            ("reviews", {"layout": "slider", "bg": "dark"}),
            ("gallery", {"heading": "", "layout": "strip"}),
            ("reserve", {}),
            ("visit", {}),
        ],
    },
    "spice": {
        "label": "Spice", "group": "photo", "best_for": "Indian, Middle Eastern, Thai, Persian",
        "description": "Rich and festive: jewel teal, saffron gold and arched frames.",
        "theme": {"accent": "#e0a028", "background": "#fdf7ec", "text": "#2a1d14", "dark": "#0f3b3a",
                  "heading_font": "Yeseva One", "body_font": "Poppins", "script_font": "Satisfy",
                  "corners": "round", "eyebrow": "script"},
        "layout": {"header": "dark", "info_bar": True, "header_button": "order", "footer": "columns"},
        "sections": [
            ("hero", {"layout": "arch", "bg": "dark", "eyebrow": "Welcome to our table"}),
            ("features", {"bg": "alt", "items": [
                {"icon": "fire", "title": "Spiced just right", "text": "Mild to hot, the way you like it."},
                {"icon": "bowl", "title": "Slow-cooked", "text": "Sauces and curries simmered with patience."},
                {"icon": "leaf", "title": "Plenty of veg", "text": "Lots of choice for vegetarians."},
                {"icon": "users", "title": "Made to share", "text": "Order a few and share them all."}]}),
            ("about", {"eyebrow": "Our story", "layout": "images-left", "badge": "Spice · Soul · Family recipes"}),
            ("dishes", {"eyebrow": "House favourites", "heading": "Dishes we're known for"}),
            ("menu", {"layout": "tabs", "bg": "alt"}),
            ("occasions", {"eyebrow": "Celebrate with us", "heading": "Feasts for every occasion",
                           "text": "Birthdays, family dinners and festivals: we'll help you plan the feast.",
                           "items": [{"icon": "users", "title": "Family feasts"}, {"icon": "gift", "title": "Birthdays"},
                                     {"icon": "sparkle", "title": "Festivals"},
                                     {"icon": "briefcase", "title": "Office lunches"}]}),
            ("reviews", {}),
            ("visit", {}),
        ],
    },
    "patisserie": {
        "label": "Patisserie", "group": "photo", "best_for": "Bakeries, desserts, tea rooms",
        "description": "Soft and elegant: blush pink, berry, arched photos and fine serif type.",
        "theme": {"accent": "#a8436a", "background": "#fcf1f1", "text": "#3a2430", "dark": "#3a2430",
                  "heading_font": "Bodoni Moda", "body_font": "Jost", "script_font": "Pinyon Script",
                  "corners": "round", "eyebrow": "script"},
        "layout": {"header": "solid", "info_bar": False, "header_button": "order", "footer": "simple"},
        "sections": [
            ("hero", {"layout": "arch", "eyebrow": "Freshly baked"}),
            ("features", {"layout": "cards", "bg": "alt", "items": [
                {"icon": "cake", "title": "Baked every morning", "text": "Out of the oven and onto the counter."},
                {"icon": "coffee", "title": "Coffee and tea", "text": "The perfect partner for something sweet."},
                {"icon": "gift", "title": "Celebration cakes", "text": "Ask us about cakes for your big day."}]}),
            ("about", {"eyebrow": "Our story", "layout": "images-right", "badge": "Baked with love",
                       "signoff": "Life is short, eat dessert first"}),
            ("dishes", {"eyebrow": "Sweet picks", "heading": "Today's favourites"}),
            ("gallery", {"heading": "", "layout": "mosaic"}),
            ("menu", {"layout": "cards", "bg": "alt"}),
            ("reviews", {"layout": "slider"}),
            ("visit", {}),
        ],
    },
    # Simple templates -------------------------------------------------------------------------------------
    "bistro": {
        "label": "Bistro", "group": "simple", "best_for": "Neighbourhood restaurants",
        "description": "Warm and classic: cream paper, serif headings, a menu with dotted price lines.",
        "theme": {"accent": "#b4532a", "background": "#f7f1e8", "text": "#2b211b", "dark": "#2b211b",
                  "heading_font": "Playfair Display", "body_font": "Source Sans 3", "script_font": "Dancing Script",
                  "corners": "soft", "eyebrow": "caps"},
        "layout": SIMPLE_LAYOUT,
        "sections": _simple(["announcement", "hero", "about", "menu", "reviews", "gallery", "visit"], "split"),
    },
    "street": {
        "label": "Street", "group": "simple", "best_for": "Food trucks, tacos, quick bites",
        "description": "Loud and fun: big block letters, bold borders, menu cards that pop.",
        "theme": {"accent": "#ff4d00", "background": "#fff3cf", "text": "#111111", "dark": "#111111",
                  "heading_font": "Archivo Black", "body_font": "Space Grotesk", "script_font": "Kaushan Script",
                  "corners": "square", "eyebrow": "caps"},
        "layout": SIMPLE_LAYOUT,
        "sections": _simple(["hero", "announcement", "menu", "cta", "gallery", "reviews", "visit"], "overlay"),
    },
    "minimal": {
        "label": "Minimal", "group": "simple", "best_for": "Any restaurant",
        "description": "Clean and calm: lots of white space and simple lines. Lets the food speak.",
        "theme": {"accent": "#2f5d50", "background": "#ffffff", "text": "#151515", "dark": "#151515",
                  "heading_font": "Inter", "body_font": "Inter", "script_font": "Caveat",
                  "corners": "soft", "eyebrow": "caps"},
        "layout": SIMPLE_LAYOUT,
        "sections": _simple(["hero", "menu", "about", "visit", "reviews"], "split"),
    },
    "night": {
        "label": "Night", "group": "simple", "best_for": "Wine bars, cocktail lounges",
        "description": "Dark and elegant: gold details for wine bars, fine dining and date nights.",
        "theme": {"accent": "#c9a45c", "background": "#121212", "text": "#f3ece0", "dark": "#050505",
                  "heading_font": "Cormorant Garamond", "body_font": "Montserrat", "script_font": "Great Vibes",
                  "corners": "square", "eyebrow": "caps"},
        "layout": SIMPLE_LAYOUT,
        "sections": _simple(["hero", "about", "menu", "gallery", "reviews", "cta", "visit"], "overlay"),
    },
    "garden": {
        "label": "Garden", "group": "simple", "best_for": "Cafés, family places, healthy food",
        "description": "Fresh and friendly: soft greens and rounded cards for cafes and family places.",
        "theme": {"accent": "#3f8f5a", "background": "#f3f8f1", "text": "#1f2d24", "dark": "#1f2d24",
                  "heading_font": "Fraunces", "body_font": "Nunito", "script_font": "Caveat",
                  "corners": "round", "eyebrow": "caps"},
        "layout": SIMPLE_LAYOUT,
        "sections": _simple(["hero", "about", "menu", "cta", "gallery", "reviews", "visit"], "split"),
    },
}
for _t in TEMPLATES.values():
    _t["order"] = [k for k, _ in _t["sections"]]


# --- Building content ----------------------------------------------------------------------------------------


def default_props(kind: str, name: str = "", cuisine: str | None = None) -> dict:
    props = {k: copy.deepcopy(f["default"]) for k, f in SECTIONS[kind]["fields"].items()}
    if kind == "hero":
        props["title"] = name
        props["tagline"] = f"{cuisine} in the neighbourhood" if cuisine else "Good food, close to home"
    return props


def new_section(kind: str, name: str = "", cuisine: str | None = None, overrides: dict | None = None) -> dict:
    props = default_props(kind, name, cuisine)
    props.update(copy.deepcopy(overrides or {}))
    return {"id": f"{kind}-{uuid.uuid4().hex[:8]}", "type": kind, "visible": True, "props": props}


def _template_layout(template: str) -> dict:
    out = {k: copy.deepcopy(f["default"]) for k, f in LAYOUT_FIELDS.items()}
    out.update(TEMPLATES[template]["layout"])
    return out


def starter_content(template: str, name: str, cuisine: str | None = None) -> dict:
    template = template if template in TEMPLATES else "bistro"
    t = TEMPLATES[template]
    return {
        "template": template,
        "theme": dict(t["theme"]),
        "layout": _template_layout(template),
        "seo": {"title": name,
                "description": f"{name}: {cuisine or 'restaurant'}. Menu, hours, online ordering and bookings."},
        "links": {"instagram": "", "facebook": "", "tiktok": ""},
        "sections": [new_section(k, name, cuisine, ov) for k, ov in t["sections"]],
        "menu_page": {k: copy.deepcopy(f["default"]) for k, f in MENU_PAGE_FIELDS.items()},
    }


STYLE_KEYS = ("bg", "layout")


def _template_styles(template: str) -> dict[str, dict]:
    """Per section type, the background and layout this template gives it."""
    out: dict[str, dict] = {}
    for kind, ov in TEMPLATES[template]["sections"]:
        out.setdefault(kind, {k: v for k, v in ov.items() if k in STYLE_KEYS})
    return out


def apply_template(content: dict, template: str, name: str = "", cuisine: str | None = None) -> dict:
    """Switch template. The look changes (colours, fonts, header, footer, each section's background and layout,
    section order); the owner's words and photos stay. Sections the template features but the page doesn't
    have yet are added with the template's sample text."""
    if template not in TEMPLATES:
        raise ValueError("Unknown template")
    out = copy.deepcopy(content)
    old_layout = out.get("layout") or {}
    out["template"] = template
    out["theme"] = dict(TEMPLATES[template]["theme"])
    out["layout"] = _template_layout(template)
    for keep in ("logo", "logo_sub", "footer_text"):
        if old_layout.get(keep):
            out["layout"][keep] = old_layout[keep]
    styles = _template_styles(template)
    sections = out.setdefault("sections", [])
    for s in sections:
        base = {k: f["default"] for k, f in SECTIONS.get(s.get("type"), {"fields": {}})["fields"].items()
                if k in STYLE_KEYS}
        s.setdefault("props", {}).update({**base, **styles.get(s.get("type"), {})})
    have = {s.get("type") for s in sections}
    for kind, ov in TEMPLATES[template]["sections"]:
        if kind not in have:
            sections.append(new_section(kind, name, cuisine, ov))
            have.add(kind)
    order = TEMPLATES[template]["order"]
    rank = {kind: i for i, kind in reversed(list(enumerate(order)))}
    out["sections"] = [s for _, s in sorted(enumerate(sections),
                                            key=lambda p: (rank.get(p[1].get("type"), len(order)), p[0]))]
    return out


# --- Validation ----------------------------------------------------------------------------------------------


def _text(value, limit: int) -> str:
    return str(value or "").replace("\r", "").strip()[:limit]


def _image(value) -> str | None:
    return value if isinstance(value, str) and ASSET_ID.match(value) else None


def _url(value) -> str:
    v = _text(value, 300)
    return v if v.startswith(("https://", "http://")) and " " not in v and '"' not in v else ""


def _clean_value(f: dict, v, default):
    kind = f["kind"]
    if kind == "text":
        out = _text(v, f["limit"])
        return out or (default if f.get("fallback") else "")
    if kind == "bool":
        return v if isinstance(v, bool) else default
    if kind in ("image", "dish"):
        return _image(v)
    if kind == "images":
        return [i for i in map(_image, v) if i][: f["limit"]] if isinstance(v, list) else []
    if kind == "choice":
        return v if v in {o["value"] for o in f["options"]} else default
    if kind == "int":
        try:
            return max(f["min"], min(f["max"], int(v)))
        except (TypeError, ValueError):
            return default
    if kind == "icon":
        return v if v in ICONS else default
    if kind == "categories":
        return [_text(c, 100) for c in v if _text(c, 100)][:30] if isinstance(v, list) else []
    if kind == "items":
        if not isinstance(v, list):
            return copy.deepcopy(default)
        rows = []
        for row in v[: f["limit"]]:
            row = row if isinstance(row, dict) else {}
            rows.append({k: _clean_value(sub, row.get(k), sub["default"]) for k, sub in f["fields"].items()})
        return rows
    raise ValueError(f"Unknown field kind {kind}")


def _clean_fields(fields: dict, raw: dict, defaults: dict) -> dict:
    """Missing keys take the default; keys that are present are checked (an emptied text stays empty)."""
    out = {}
    for k, f in fields.items():
        default = defaults.get(k, f["default"])
        out[k] = _clean_value(f, raw[k] if k in raw else default, default)
    return out


def clean_content(raw: dict) -> dict:
    """Return a safe, complete copy of site content, dropping anything unknown or invalid."""
    if not isinstance(raw, dict):
        raise ValueError("Site content must be an object")
    template = raw.get("template") if raw.get("template") in TEMPLATES else "bistro"
    base = TEMPLATES[template]["theme"]
    theme_in = raw.get("theme") if isinstance(raw.get("theme"), dict) else {}
    theme = {}
    for key in ("accent", "background", "text", "dark"):
        v = theme_in.get(key)
        theme[key] = v if isinstance(v, str) and HEX.match(v) else base[key]
    for key in ("heading_font", "body_font"):
        v = theme_in.get(key)
        theme[key] = v if v in FONTS else base[key]
    theme["script_font"] = theme_in.get("script_font") if theme_in.get("script_font") in FONTS else base["script_font"]
    theme.update(_clean_fields(THEME_FIELDS, theme_in, base))

    layout_in = raw.get("layout") if isinstance(raw.get("layout"), dict) else {}
    layout = _clean_fields(LAYOUT_FIELDS, layout_in, _template_layout(template))

    styles = _template_styles(template)
    seo_in = raw.get("seo") if isinstance(raw.get("seo"), dict) else {}
    links_in = raw.get("links") if isinstance(raw.get("links"), dict) else {}
    menu_page_in = raw.get("menu_page") if isinstance(raw.get("menu_page"), dict) else {}
    sections = []
    for s in (raw.get("sections") or [])[:MAX_SECTIONS]:
        if not isinstance(s, dict) or s.get("type") not in SECTIONS:
            continue
        kind = s["type"]
        p_in = s.get("props") if isinstance(s.get("props"), dict) else {}
        defaults = {**default_props(kind), **styles.get(kind, {})}
        props = _clean_fields(SECTIONS[kind]["fields"], p_in, defaults)
        sid = s.get("id") if isinstance(s.get("id"), str) and re.match(r"^[a-z]+-[0-9a-f]{4,12}$", s["id"]) else None
        sections.append({"id": sid or new_section(kind)["id"], "type": kind,
                         "visible": s.get("visible") if isinstance(s.get("visible"), bool) else True, "props": props})
    return {
        "template": template, "theme": theme, "layout": layout,
        "seo": clean_seo(seo_in),
        "links": {k: _url(links_in.get(k)) for k in ("instagram", "facebook", "tiktok")},
        "sections": sections,
        "menu_page": _clean_fields(MENU_PAGE_FIELDS, menu_page_in, {}),
    }


PRICE_RANGES = ("", "$", "$$", "$$$", "$$$$")
VERIFY_TOKEN = re.compile(r"^[A-Za-z0-9_\-]{8,100}$")
GA_ID = re.compile(r"^G-[A-Z0-9]{4,16}$")
MAX_KEYWORDS = 10


def clean_seo(raw: dict) -> dict:
    """Search settings. Title and description are on every site; the rest is used when the SEO plan is on."""
    words: list[str] = []
    for k in raw["keywords"] if isinstance(raw.get("keywords"), list) else []:
        w = re.sub(r"\s+", " ", _text(k, 60)).lower() if isinstance(k, str) else ""
        if w and w not in words:
            words.append(w)
    alts_in = raw.get("alts") if isinstance(raw.get("alts"), dict) else {}
    alts = {k: _text(v, 125) for k, v in list(alts_in.items())[:120] if _image(k) and _text(v, 125)}
    google, bing = _text(raw.get("google_verify"), 100), _text(raw.get("bing_verify"), 100)
    ga = _text(raw.get("ga_id"), 20).upper()
    return {
        "title": _text(raw.get("title"), 70),
        "description": _text(raw.get("description"), 160),
        "keywords": words[:MAX_KEYWORDS],
        "share_image": _image(raw.get("share_image")),
        "index": raw.get("index") if isinstance(raw.get("index"), bool) else True,
        "price_range": raw.get("price_range") if raw.get("price_range") in PRICE_RANGES else "",
        "area": _text(raw.get("area"), 80),
        "alts": alts,
        "google_verify": google if VERIFY_TOKEN.match(google) else "",
        "bing_verify": bing if VERIFY_TOKEN.match(bing) else "",
        "ga_id": ga if GA_ID.match(ga) else "",
    }


def image_ids(content: dict) -> set[str]:
    out = set()
    logo = (content.get("layout") or {}).get("logo")
    if logo:
        out.add(logo)
    share = (content.get("seo") or {}).get("share_image")
    if share:
        out.add(share)
    for s in content.get("sections", []):
        p = s.get("props", {})
        for key in ("image", "image2"):
            if p.get(key):
                out.add(p[key])
        out.update(p.get("images") or [])
        for row in p.get("items") or []:
            if isinstance(row, dict) and row.get("image"):
                out.add(row["image"])
    return out


SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,58}[a-z0-9])$")
DOMAIN = re.compile(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")[:50]
    return s if len(s) >= 3 else f"restaurant-{s}".strip("-")
