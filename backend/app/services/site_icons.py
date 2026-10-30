"""Line icons for restaurant websites (24x24, drawn with the current text colour).

Owners pick icons by key; only these fixed paths ever reach the page.
"""

ICONS: dict[str, tuple[str, list[str]]] = {
    "leaf": ("Fresh / local", ["M5 19c0-8 5-13 14-14 0 9-5 14-13 14z", "M5 19l8-8"]),
    "chef": ("Chef", ["M7 13a4 4 0 1 1 2-7.5A4 4 0 0 1 16 5a4 4 0 1 1 1 8", "M7 13v6h10v-6", "M7 16h10"]),
    "heart": ("Heart", ["M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"]),
    "star": ("Star", ["M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z"]),
    "award": ("Award", ["M12 3a6 6 0 1 0 0 12 6 6 0 0 0 0-12z", "M8.5 14L7 21l5-3 5 3-1.5-7"]),
    "utensils": ("Cutlery", ["M7 3v8", "M5 3v5a2 2 0 0 0 4 0V3", "M7 11v10", "M17 21V3c-2.2 1.5-3 4-3 7v4h3"]),
    "bowl": ("Bowl", ["M3 11h18a9 9 0 0 1-18 0z", "M9 20h6", "M8 8c0-1.5 1-2 1-3.5", "M12 8c0-1.5 1-2 1-3.5",
                      "M16 8c0-1.5 1-2 1-3.5"]),
    "pizza": ("Pizza", ["M12 21L3.5 6.5a15 15 0 0 1 17 0z", "M5.5 9.5a12 12 0 0 1 13 0", "M10 12h.01",
                        "M13 15h.01", "M14 10.5h.01"]),
    "fish": ("Seafood", ["M3 12c3-4.5 8-6 12-4l4-3v14l-4-3c-4 2-9 .5-12-4z", "M8 11.2h.01"]),
    "fire": ("Grill / spicy", ["M12 21c-3.9 0-6.5-2.6-6.5-6.2 0-3.3 2.4-5.2 3.6-8.3.9 1.9 2.2 2.9 2.2 2.9S12 6 "
                               "13.6 3c1.2 3.2 4.9 6 4.9 11.4 0 3.8-2.6 6.6-6.5 6.6z"]),
    "coffee": ("Coffee", ["M4 9h12v5a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5z", "M16 10h1.5a2.5 2.5 0 0 1 0 5H16",
                          "M8 3v3", "M12 3v3"]),
    "cake": ("Dessert", ["M4 20h16", "M5 20v-7h14v7", "M5 15.5c2 1.3 4.5 1.3 7 0s5-1.3 7 0", "M12 13V9",
                         "M12 6.5c-.8-.8-.8-1.8 0-3 .8 1.2.8 2.2 0 3z"]),
    "wine": ("Wine", ["M8 3h8l-.5 5a3.5 3.5 0 0 1-7 0z", "M12 11.5V20", "M8.5 20h7"]),
    "cocktail": ("Cocktail", ["M4 4h16l-8 9z", "M12 13v7", "M8 20h8", "M7 7h10"]),
    "sun": ("Patio / sunny", ["M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z", "M12 2v2", "M12 20v2", "M2 12h2", "M20 12h2",
                              "M4.9 4.9l1.4 1.4", "M17.7 17.7l1.4 1.4", "M4.9 19.1l1.4-1.4", "M17.7 6.3l1.4-1.4"]),
    "clock": ("Clock", ["M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z", "M12 7v5l3 2"]),
    "truck": ("Delivery", ["M3 6h11v10H3z", "M14 9h4l3 3v4h-7", "M6 17.5a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0",
                           "M15 17.5a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0"]),
    "calendar": ("Calendar", ["M4 6h16v14H4z", "M4 10h16", "M8 3v4", "M16 3v4"]),
    "gift": ("Gift / birthday", ["M4 10h16v4H4z", "M5 14v6h14v-6", "M12 10v10",
                                 "M12 10c-2-4-6-4-6-1.5S10 10 12 10z", "M12 10c2-4 6-4 6-1.5S14 10 12 10z"]),
    "users": ("Family / groups", ["M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7z",
                                  "M2.5 20c.8-3.5 3.3-5.5 6.5-5.5s5.7 2 6.5 5.5", "M16 4.5a3.5 3.5 0 0 1 0 6.5",
                                  "M18 14.8c1.8.7 3 2.4 3.5 5.2"]),
    "briefcase": ("Business", ["M3 8h18v11H3z", "M8 8V5h8v3", "M3 13h18"]),
    "music": ("Live music", ["M9 18V5l11-2v13", "M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z",
                             "M20 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z"]),
    "sparkle": ("Special", ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z",
                            "M19 15l.8 2.2 2.2.8-2.2.8L19 21l-.8-2.2-2.2-.8 2.2-.8z"]),
    "pin": ("Location", ["M12 21s-6.5-6-6.5-11a6.5 6.5 0 0 1 13 0c0 5-6.5 11-6.5 11z",
                         "M12 7.5a2.5 2.5 0 1 0 0 5 2.5 2.5 0 0 0 0-5z"]),
    "phone": ("Phone", ["M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"]),
    "mail": ("Email", ["M3 6h18v12H3z", "M3 7l9 6 9-6"]),
}

# Used by the page itself (not offered in the icon picker).
SOCIAL: dict[str, list[str]] = {
    "instagram": ["M8 3h8a5 5 0 0 1 5 5v8a5 5 0 0 1-5 5H8a5 5 0 0 1-5-5V8a5 5 0 0 1 5-5z",
                  "M12 8.5a3.5 3.5 0 1 0 0 7 3.5 3.5 0 0 0 0-7z", "M17 6.8h.01"],
    "facebook": ["M14 8h2.5V4.5H14a4 4 0 0 0-4 4V11H7.5v3.5H10V21h3.5v-6.5H16l.5-3.5h-3V8.8c0-.5.3-.8.5-.8z"],
    "tiktok": ["M14 3v11.5a3.5 3.5 0 1 1-3.5-3.5", "M14 3c.4 2.8 2.2 4.5 5 4.8"],
    "menu": ["M4 7h16", "M4 12h16", "M4 17h16"],
    "arrow": ["M5 12h14", "M13 6l6 6-6 6"],
    "quote": ["M10 7H6a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h3v1a3 3 0 0 1-3 3", "M20 7h-4a2 2 0 0 0-2 2v3a2 2 0 0 0 2 2h3v1"
              "a3 3 0 0 1-3 3"],
}


def svg(key: str, cls: str = "ic", size: int = 24) -> str:
    """Inline SVG for an icon key; unknown keys give an empty string."""
    paths = ICONS[key][1] if key in ICONS else SOCIAL.get(key)
    if not paths:
        return ""
    body = "".join(f'<path d="{d}"/>' for d in paths)
    return (f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            f'stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{body}</svg>')
