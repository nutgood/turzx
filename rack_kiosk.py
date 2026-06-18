#!/usr/bin/env python3
"""Replicate the Grafana 'Rack Kiosk' dashboard on the Turing 8.8" (1920x480), paged.

Queries Prometheus via the Grafana datasource proxy and renders the panels across
auto-cycling pages (bigger, more readable tiles), preserving each panel's unit,
thresholds and color mappings. Pushed via the PNG path.

Env: GRAFANA_URL, GRAFANA_TOKEN (or local .grafana_token file).
Args: --page=N (single page, no cycle), --secs=N (seconds/page), --save=FILE --once
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

GURL = os.environ.get("GRAFANA_URL", "https://htalos-grafana.feist-boa.ts.net").rstrip("/")
_TOKFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".grafana_token")
GTOK = os.environ.get("GRAFANA_TOKEN", "") or (
    open(_TOKFILE).read().strip() if os.path.exists(_TOKFILE) else "")
PROXY = f"{GURL}/api/datasources/proxy/uid/prometheus/api/v1/query"

COL = {"green": "#73BF69", "red": "#F2495C", "orange": "#FF9830", "blue": "#5794F2",
       "yellow": "#FADE2A", "purple": "#B877D9", "text": "#CCCCDC"}
BG = (17, 18, 23)
TILE = (26, 27, 38)
TILE_BD = (42, 47, 69)
TITLE_C = (142, 148, 168)
F = "/System/Library/Fonts/Helvetica.ttc"
CPU_TH = [(0, "green"), (70, "orange"), (90, "red")]

# --- panel data registry, keyed by title: kind, unit, thresholds, exprs[], mapping/extra ---
PANELS = {
    "WAN — Bell Fiber": ("stat", None, [(0, "red"), (1, "green")],
        ['(unifi_device_wan_speed_bps{port="eth10"} > bool 0) or on() vector(0)'], {0: ("DOWN", "red"), 1: ("UP", "green")}),
    "WAN — Videotron": ("stat", None, [(0, "red"), (1, "green")],
        ['(unifi_device_wan_speed_bps{port="eth8"} > bool 0) or on() vector(0)'], {0: ("DOWN", "red"), 1: ("UP", "green")}),
    "Total Rack Power": ("stat", "watt", [(0, "green"), (1500, "red")],
        ['last_over_time(unifi_device_outlet_ac_power_consumption{name="USP PDU Pro"}[5m])'], None),
    "Est. runtime on battery": ("stat", "s", [(0, "red"), (600, "orange"), (1800, "green")],
        ['(scalar(hass_sensor_battery_percent{entity="sensor.ecoflow_network_main_battery_level"}) / 100 * 1024) / scalar(avg_over_time(unifi_device_outlet_ac_power_consumption{name="USP PDU Pro"}[30m])) * 3600'], None),
    "EcoFlow battery": ("stat", "percent", [(0, "red"), (20, "orange"), (50, "green")],
        ['last_over_time(hass_sensor_battery_percent{entity="sensor.ecoflow_network_main_battery_level"}[10m])'], None),
    "30m avg PDU load": ("stat", "watt", [(0, "blue")],
        ['avg_over_time(unifi_device_outlet_ac_power_consumption{name="USP PDU Pro"}[30m])'], None),
    "Active alerts": ("stat", None, [(0, "green"), (1, "orange"), (5, "red")],
        ['count(ALERTS{alertstate="firing", alertname!~"Watchdog|InfoInhibitor"}) or vector(0)'], None),
    "Fiber Throughput": ("stat", "bps", [(0, "blue")],
        ['(rate(unifi_device_wan_receive_bytes_total{port="eth10"}[30s]) + rate(unifi_device_wan_transmit_bytes_total{port="eth10"}[30s]))*8'], None),
    "Cable Throughput": ("stat", "bps", [(0, "blue")],
        ['(rate(unifi_device_wan_receive_bytes_total{port="eth8"}[30s]) + rate(unifi_device_wan_transmit_bytes_total{port="eth8"}[30s]))*8'], None),
    "UDM CPU": ("stat", "percent", CPU_TH, ['last_over_time(unifi_device_cpu_utilization_ratio{name="mcflurry"}[10m])*100'], None),
    "UDM Memory": ("stat", "percent", CPU_TH, ['last_over_time(unifi_device_memory_utilization_ratio{name="mcflurry"}[10m])*100'], None),
    "Rack temperatures": ("bargauge", "celsius", [(0, "green"), (55, "orange"), (70, "red")],
        ['unifi_device_temperature_celsius{temp_type=~"board|cpu"} > 0'], {"min": 20, "max": 80, "label": ["name", "temp_area"]}),
    "Top outlets (W)": ("bargauge", "watt", [(0, "green"), (150, "red")],
        ['topk(10, unifi_device_outlet_outlet_power{name="USP PDU Pro"} > 1)'], {"min": 0, "max": 300, "label": ["outlet_name"]}),
    "K8s nodes ready": ("ratio", None, [(0, "red"), (1, "green")],
        ['sum(kube_node_status_condition{condition="Ready",status="true"}==1)', 'count(kube_node_info)'], None),
    "PVE nodes up": ("stat", None, [(0, "red"), (1, "orange"), (3, "green")],
        ['count(max by (id) (pve_up{id=~"node/.*"}) == 1)'], None),
    "LH unhealthy": ("stat", None, [(0, "green"), (1, "orange"), (3, "red")],
        ['sum(longhorn_volume_robustness > 1) or vector(0)'], None),
    "K8s cluster CPU": ("stat", "percent", CPU_TH,
        ['last_over_time((sum(rate(node_cpu_seconds_total{mode!="idle"}[30s])) / count(node_cpu_seconds_total{mode="idle"}) * 100)[10m:5s])'], None),
    "K8s cluster memory": ("stat", "percent", CPU_TH,
        ['(1 - sum(last_over_time(node_memory_MemAvailable_bytes[10m])) / sum(last_over_time(node_memory_MemTotal_bytes[10m]))) * 100'], None),
    "PVE cluster CPU": ("stat", "percent", CPU_TH,
        ['avg(avg by (id) (last_over_time(pve_cpu_usage_ratio{id=~"node/.*"}[10m]))) * 100'], None),
    "PVE cluster memory": ("stat", "percent", CPU_TH,
        ['sum(max by (id) (last_over_time(pve_memory_usage_bytes{id=~"node/.*"}[10m]))) / sum(max by (id) (last_over_time(pve_memory_size_bytes{id=~"node/.*"}[10m]))) * 100'], None),
    "Mac mini CPU": ("stat", "percent", CPU_TH,
        ['last_over_time((avg(1 - rate(node_cpu_seconds_total{instance="mac-mini",mode="idle"}[30s])) * 100)[10m:5s])'], None),
    "Mac mini memory": ("stat", "percent", CPU_TH,
        ['last_over_time(((node_memory_active_bytes{instance="mac-mini"} + node_memory_wired_bytes{instance="mac-mini"} + node_memory_compressed_bytes{instance="mac-mini"}) / node_memory_total_bytes{instance="mac-mini"} * 100)[10m:5s])'], None),
    "Mac mini disk": ("stat", "percent", [(0, "green"), (80, "orange"), (95, "red")],
        ['last_over_time(((1 - node_filesystem_avail_bytes{instance="mac-mini", mountpoint="/System/Volumes/Data"} / node_filesystem_size_bytes{instance="mac-mini", mountpoint="/System/Volumes/Data"}) * 100)[10m:5s])'], None),
    "Now": ("time", "dateTimeAsLocal", [(0, "text")], ['time()*1000'], None),
}

# --- pages: each is (title, [(panel_title, x, y, w, h), ...]) on a 24-col x 12-row grid ---
PAGES = [
    ("Power & WAN", [
        ("WAN — Bell Fiber", 0, 0, 6, 6), ("WAN — Videotron", 6, 0, 6, 6),
        ("EcoFlow battery", 12, 0, 6, 6), ("Est. runtime on battery", 18, 0, 6, 6),
        ("Total Rack Power", 0, 6, 6, 6), ("30m avg PDU load", 6, 6, 6, 6),
        ("Fiber Throughput", 12, 6, 6, 6), ("Cable Throughput", 18, 6, 6, 6),
    ]),
    ("Temps & Outlets", [
        ("Rack temperatures", 0, 0, 12, 12), ("Top outlets (W)", 12, 0, 12, 12),
    ]),
    ("Infra & Compute", [
        ("Active alerts", 0, 0, 4, 4), ("K8s nodes ready", 4, 0, 4, 4), ("PVE nodes up", 8, 0, 4, 4),
        ("LH unhealthy", 12, 0, 4, 4), ("UDM CPU", 16, 0, 4, 4), ("UDM Memory", 20, 0, 4, 4),
        ("K8s cluster CPU", 0, 4, 6, 4), ("K8s cluster memory", 6, 4, 6, 4),
        ("PVE cluster CPU", 12, 4, 6, 4), ("PVE cluster memory", 18, 4, 6, 4),
        ("Mac mini CPU", 0, 8, 8, 4), ("Mac mini memory", 8, 8, 8, 4), ("Mac mini disk", 16, 8, 8, 4),
    ]),
]

W, H = 1920, 480
FOOT = 26                      # bottom band for page indicator
SX, SY = W / 24.0, (H - FOOT) / 12.0


def _find_font():
    for c in (F,  # macOS Helvetica
              "/Library/Fonts/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",        # Debian/RPi
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
              "/usr/share/fonts/TTF/DejaVuSans.ttf"):
        if os.path.exists(c):
            return c
    return None


_FONT = _find_font()


def font(sz):
    return ImageFont.truetype(_FONT, sz) if _FONT else ImageFont.load_default(size=sz)


def query(expr):
    data = urllib.parse.urlencode({"query": expr}).encode()
    req = urllib.request.Request(PROXY, data=data, headers={"Authorization": f"Bearer {GTOK}"})
    with urllib.request.urlopen(req, timeout=12) as r:
        res = json.load(r)
    d = res.get("data", {})
    if d.get("resultType") == "scalar":
        try:
            return [({}, float(d["result"][1]))]
        except (KeyError, ValueError, IndexError):
            return []
    out = []
    for s in d.get("result", []):
        try:
            out.append((s.get("metric", {}), float(s["value"][1])))
        except (KeyError, ValueError):
            pass
    return out


def hexrgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def th_color(steps, v):
    c = steps[0][1]
    for val, col in steps:
        if v >= val:
            c = col
    return hexrgb(COL.get(c, "#CCCCDC"))


def fmt(unit, v):
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
    sz = target
    while sz > 14:
        f = font(sz)
        bb = d.textbbox((0, 0), text, font=f)
        if bb[2] - bb[0] <= maxw:
            return f
        sz -= 2
    return font(14)


def draw_centered(d, cx, cy, text, fnt, fill):
    bb = d.textbbox((0, 0), text, font=fnt)
    d.text((cx - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]), text, font=fnt, fill=fill)


def draw_stat(d, title, x, y, w, h, kind, unit, steps, exprs, extra, results):
    series = results.get(exprs[0], [])
    if kind == "time":
        val, color = datetime.now().strftime("%a %H:%M:%S"), hexrgb(COL["text"])
    elif kind == "ratio":
        ready = series[0][1] if series else 0
        tot = results.get(exprs[1], [])
        tot = tot[0][1] if tot else 0
        val, color = f"{ready:.0f}/{tot:.0f}", th_color(steps, ready if ready >= tot else 0)
    else:
        v = series[0][1] if series else 0
        if extra:
            txt, col = extra.get(int(round(v)), (fmt(unit, v), "text"))
            val, color = txt, hexrgb(COL.get(col, "#CCCCDC"))
        else:
            val, color = fmt(unit, v), th_color(steps, v)
    tsz = max(13, min(20, int(h * 0.11)))
    d.text((x + 10, y + 7), title.upper(), font=font(tsz), fill=TITLE_C)
    fnt = fit_font(d, val, w - 28, int(h * 0.42))
    draw_centered(d, x + w / 2, y + h / 2 + h * 0.06, val, fnt, color)


def draw_bargauge(d, title, x, y, w, h, unit, steps, exprs, extra, results):
    d.text((x + 10, y + 7), title.upper(), font=font(18), fill=TITLE_C)
    rows = sorted(results.get(exprs[0], []), key=lambda s: -s[1])
    mn, mx = extra["min"], extra["max"]
    top = y + 34
    avail = (y + h - 10) - top
    n = max(1, len(rows))
    rh = min(46, avail / n)
    lblsz = max(13, min(22, int(rh * 0.5)))
    block = n * rh
    top += max(0, (avail - block) / 2)  # vertically center the bars
    lblw = int(w * 0.30)
    valw = 78
    for i, (m, v) in enumerate(rows):
        ry = top + i * rh
        parts = [str(m.get(k, "")) for k in extra["label"]]
        parts = [p for p in parts if p and p.lower() != "general"]
        label = " ".join(parts).strip()
        col = th_color(steps, v)
        lf = fit_font(d, label, lblw - 16, lblsz)
        d.text((x + 12, ry + (rh - lblsz) / 2 - 1), label, font=lf, fill=(180, 186, 206))
        bx0, bx1 = x + lblw, x + w - valw
        frac = max(0.0, min(1.0, (v - mn) / (mx - mn) if mx > mn else 0))
        d.rounded_rectangle([bx0, ry + 3, bx1, ry + rh - 4], radius=3, fill=(38, 41, 58))
        if frac > 0:
            d.rounded_rectangle([bx0, ry + 3, bx0 + (bx1 - bx0) * frac, ry + rh - 4], radius=3, fill=col)
        d.text((bx1 + 8, ry + (rh - lblsz) / 2 - 1), fmt(unit, v), font=font(lblsz), fill=col)


def render_page(idx, results):
    title, layout = PAGES[idx]
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    for pt, x0, y0, w0, h0 in layout:
        x, y, w, h = x0 * SX, y0 * SY, w0 * SX, h0 * SY
        d.rounded_rectangle([x + 3, y + 3, x + w - 3, y + h - 3], radius=6, fill=TILE, outline=TILE_BD, width=1)
        kind, unit, steps, exprs, extra = PANELS[pt]
        if kind == "bargauge":
            draw_bargauge(d, pt, x + 3, y + 3, w - 6, h - 6, unit, steps, exprs, extra, results)
        else:
            draw_stat(d, pt, x + 3, y + 3, w - 6, h - 6, kind, unit, steps, exprs, extra, results)

    # footer: page name (left), clock (center), dots (right)
    fy = H - FOOT
    d.text((14, fy + 6), f"RACK KIOSK — {title}", font=font(15), fill=(150, 156, 178))
    draw_centered(d, W / 2, fy + FOOT / 2, datetime.now().strftime("%a %d %b  %H:%M:%S"), font(15), (150, 156, 178))
    dots_n = len(PAGES)
    dx = W - 24 - (dots_n - 1) * 22
    for i in range(dots_n):
        on = i == idx
        cx = dx + i * 22
        r = 6 if on else 4
        c = hexrgb(COL["blue"]) if on else (70, 74, 96)
        d.ellipse([cx - r, fy + FOOT / 2 - r, cx + r, fy + FOOT / 2 + r], fill=c)
    return img


def fetch_all():
    exprs = []
    for kind, unit, steps, ex, extra in PANELS.values():
        exprs.extend(ex)
    results = {}
    with ThreadPoolExecutor(max_workers=12) as exr:
        futs = {exr.submit(query, e): e for e in exprs}
        for fu, e in futs.items():
            try:
                results[e] = fu.result()
            except Exception as err:
                results[e] = []
                print(f"query failed: {e[:40]}... {err}", file=sys.stderr)
    return results


def main():
    once = "--once" in sys.argv
    save = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--save=")), None)
    fixed = next((int(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--page=")), None)
    secs = next((float(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--secs=")), 7.0)

    lcd = None
    if not save or "--display" in sys.argv:
        from library.lcd.lcd_comm import Orientation
        from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB
        lcd = LcdCommTuringUSB(com_port="AUTO")
        lcd.InitializeComm()
        lcd.SetOrientation(Orientation.LANDSCAPE)
        lcd.SetBrightness(70)

    idx = fixed or 0
    while True:
        img = render_page(idx, fetch_all())
        if save:
            img.save(save)
            print(f"saved {save} (page {idx}: {PAGES[idx][0]})")
        if lcd:
            lcd.DisplayPILImage(img)
        if once:
            break
        time.sleep(secs)
        if fixed is None:
            idx = (idx + 1) % len(PAGES)


if __name__ == "__main__":
    main()
