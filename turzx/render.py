"""Shared rendering helpers (pure PIL; no device, no network).

Coordinate system: the panel is drawn in landscape ``W x H`` (1920x480). Colors follow
Grafana's dark palette. Import these in any app's ``render()``.
"""
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 480

# Grafana dark palette
COL = {"green": "#73BF69", "red": "#F2495C", "orange": "#FF9830", "blue": "#5794F2",
       "yellow": "#FADE2A", "purple": "#B877D9", "text": "#CCCCDC"}
BG = (17, 18, 23)
TILE = (26, 27, 38)
TILE_BD = (42, 47, 69)
TITLE_C = (142, 148, 168)

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Helvetica.ttc",                              # macOS
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",                  # Debian / RPi
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
)


def _find_font():
    import os
    for c in _FONT_CANDIDATES:
        if os.path.exists(c):
            return c
    return None


_FONT = _find_font()


def font(size):
    return ImageFont.truetype(_FONT, size) if _FONT else ImageFont.load_default(size=size)


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def th_color(steps, v):
    """Map a value to a color via Grafana-style threshold steps [(value, color_name), ...]."""
    c = steps[0][1]
    for val, col in steps:
        if v >= val:
            c = col
    return hexrgb(COL.get(c, "#CCCCDC"))


def fmt(unit, v):
    """Format a number the way Grafana would for the given unit."""
    if unit == "watt":
        return f"{v:.0f} W" if v < 1000 else f"{v / 1000:.2f} kW"
    if unit == "percent":
        return f"{v:.0f}%" if v >= 10 else f"{v:.1f}%"
    if unit == "bps":
        for div, suf in ((1e9, "Gb"), (1e6, "Mb"), (1e3, "Kb")):
            if abs(v) >= div:
                return f"{v / div:.1f} {suf}ps"
        return f"{v:.0f} bps"
    if unit == "celsius":
        return f"{v:.0f}°C"
    if unit == "s":
        if v >= 86400:
            return f"{v / 86400:.1f} d"
        if v >= 3600:
            return f"{v / 3600:.1f} h"
        if v >= 60:
            return f"{v / 60:.0f} m"
        return f"{v:.0f} s"
    return f"{v:.0f}"


def fit_font(d, text, maxw, target):
    """Largest font <= target that fits text within maxw px."""
    sz = target
    while sz > 14:
        f = font(sz)
        if d.textbbox((0, 0), text, font=f)[2] <= maxw:
            return f
        sz -= 2
    return font(14)


def draw_centered(d, cx, cy, text, fnt, fill):
    bb = d.textbbox((0, 0), text, font=fnt)
    d.text((cx - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]), text, font=fnt, fill=fill)


def panel(d, x, y, w, h, radius=6, fill=TILE, outline=TILE_BD):
    """Draw a standard tile/panel background rect."""
    d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill, outline=outline, width=1)


def blank():
    """A fresh full-frame image + draw context."""
    img = Image.new("RGB", (W, H), BG)
    return img, ImageDraw.Draw(img)
