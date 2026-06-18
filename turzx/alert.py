"""Alert overlay rendering — a configurable full-screen alert.

Properties: message (required), title, level (info/warning/critical/success),
color, accent, text_color, icon (warning/info/error/success/none), blink, size.
"""
from PIL import Image, ImageDraw

from .render import COL, H, W, draw_centered, font, hexrgb

# severity presets: background, accent color name, default icon
LEVELS = {
    "info":     {"bg": (10, 20, 44), "accent": "blue", "icon": "info"},
    "warning":  {"bg": (44, 30, 8), "accent": "orange", "icon": "warning"},
    "critical": {"bg": (46, 10, 12), "accent": "red", "icon": "error"},
    "success":  {"bg": (10, 34, 16), "accent": "green", "icon": "success"},
}


def _hexc(s, default):
    try:
        return hexrgb(s) if s else default
    except Exception:
        return default


def _draw_icon(d, kind, cx, cy, r, color):
    if kind == "none":
        return
    if kind == "warning":
        d.polygon([(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)], outline=color, width=4)
        draw_centered(d, cx, cy + 4, "!", font(int(r)), color)
    elif kind == "info":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=4)
        draw_centered(d, cx, cy, "i", font(int(r * 1.2)), color)
    elif kind == "error":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=4)
        d.line([cx - r * .4, cy - r * .4, cx + r * .4, cy + r * .4], fill=color, width=4)
        d.line([cx - r * .4, cy + r * .4, cx + r * .4, cy - r * .4], fill=color, width=4)
    elif kind == "success":
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=4)
        d.line([cx - r * .45, cy, cx - r * .05, cy + r * .4], fill=color, width=5)
        d.line([cx - r * .05, cy + r * .4, cx + r * .5, cy - r * .45], fill=color, width=5)


def _wrap(d, text, fnt, maxw):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=fnt) <= maxw:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def render_alert(props, remaining, blink_on=True):
    p = props or {}
    L = LEVELS.get(str(p.get("level") or "warning").lower(), LEVELS["warning"])
    bg = _hexc(p.get("color"), L["bg"])
    accent = _hexc(p.get("accent"), hexrgb(COL.get(L["accent"], "#F2495C")))
    txtcol = _hexc(p.get("text_color"), (255, 255, 255))
    icon = str(p.get("icon") or L["icon"]).lower()
    title = str(p.get("title") or "ALERT")
    message = str(p.get("message") or "")
    show = (not p.get("blink")) or blink_on               # blink hides chrome on the off-beat

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    if show:
        d.rectangle([0, 0, W, 8], fill=accent)
        d.rectangle([0, H - 8, W, H], fill=accent)
        _draw_icon(d, icon, 52, 40, 24, accent)
        d.text((92, 18), title.upper(), font=font(46), fill=accent)
    d.text((W - 150, 24), f"{int(remaining)}s", font=font(34), fill=(210, 180, 180))

    try:
        size = int(p.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    fnt = font(size if size > 0 else 96)
    lines = _wrap(d, message, fnt, W - 100)
    while size <= 0 and len(lines) > 3 and fnt.size > 40:  # auto-shrink to ≤3 lines
        fnt = font(fnt.size - 8)
        lines = _wrap(d, message, fnt, W - 100)
    y = (H - len(lines) * (fnt.size + 10)) / 2 + 24
    for ln in lines:
        draw_centered(d, W / 2, y + fnt.size / 2, ln, fnt, txtcol)
        y += fnt.size + 10
    return img
