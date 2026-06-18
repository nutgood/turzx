"""Rack Kiosk app — faithful replica of the Grafana 'Rack Kiosk' dashboard (uid adg8v6n).

Queries Prometheus via the Grafana datasource proxy and renders the panels across pages,
preserving each panel's unit, thresholds and color mappings (incl. two bar gauges).
"""
import time
from datetime import datetime

from ..grafana import query_many
from ..render import (BG, COL, TILE, TILE_BD, TITLE_C, W, H, blank, draw_centered,
                      fit_font, fmt, font, hexrgb, panel, th_color)
from .base import App

CPU_TH = [(0, "green"), (70, "orange"), (90, "red")]

# panel registry, keyed by title: (kind, unit, thresholds, exprs[], mapping/extra)
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

# pages: (title, [(panel_title, x, y, w, h), ...]) on a 24-col x 12-row grid
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

FOOT = 26                          # bottom band for the page indicator
SX, SY = W / 24.0, (H - FOOT) / 12.0


def _draw_stat(d, title, x, y, w, h, kind, unit, steps, exprs, extra, results):
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
    d.text((x + 10, y + 7), title.upper(), font=font(max(13, min(20, int(h * 0.11)))), fill=TITLE_C)
    fnt = fit_font(d, val, w - 28, int(h * 0.42))
    draw_centered(d, x + w / 2, y + h / 2 + h * 0.06, val, fnt, color)


def _draw_bargauge(d, title, x, y, w, h, unit, steps, exprs, extra, results):
    d.text((x + 10, y + 7), title.upper(), font=font(18), fill=TITLE_C)
    rows = sorted(results.get(exprs[0], []), key=lambda s: -s[1])
    mn, mx = extra["min"], extra["max"]
    top = y + 34
    avail = (y + h - 10) - top
    n = max(1, len(rows))
    rh = min(46, avail / n)
    lblsz = max(13, min(22, int(rh * 0.5)))
    top += max(0, (avail - n * rh) / 2)            # vertically center
    lblw, valw = int(w * 0.30), 78
    for i, (m, v) in enumerate(rows):
        ry = top + i * rh
        parts = [str(m.get(k, "")) for k in extra["label"]]
        parts = [p for p in parts if p and p.lower() != "general"]
        label = " ".join(parts).strip()
        col = th_color(steps, v)
        d.text((x + 12, ry + (rh - lblsz) / 2 - 1), label, font=fit_font(d, label, lblw - 16, lblsz), fill=(180, 186, 206))
        bx0, bx1 = x + lblw, x + w - valw
        frac = max(0.0, min(1.0, (v - mn) / (mx - mn) if mx > mn else 0))
        d.rounded_rectangle([bx0, ry + 3, bx1, ry + rh - 4], radius=3, fill=(38, 41, 58))
        if frac > 0:
            d.rounded_rectangle([bx0, ry + 3, bx0 + (bx1 - bx0) * frac, ry + rh - 4], radius=3, fill=col)
        d.text((bx1 + 8, ry + (rh - lblsz) / 2 - 1), fmt(unit, v), font=font(lblsz), fill=col)


def render_page(idx, results):
    title, layout = PAGES[idx]
    img, d = blank()
    for pt, x0, y0, w0, h0 in layout:
        x, y, w, h = x0 * SX, y0 * SY, w0 * SX, h0 * SY
        panel(d, x + 3, y + 3, w - 6, h - 6)
        kind, unit, steps, exprs, extra = PANELS[pt]
        if kind == "bargauge":
            _draw_bargauge(d, pt, x + 3, y + 3, w - 6, h - 6, unit, steps, exprs, extra, results)
        else:
            _draw_stat(d, pt, x + 3, y + 3, w - 6, h - 6, kind, unit, steps, exprs, extra, results)

    fy = H - FOOT                                   # footer: page name | clock | dots
    d.text((14, fy + 6), f"RACK KIOSK — {title}", font=font(15), fill=(150, 156, 178))
    draw_centered(d, W / 2, fy + FOOT / 2, datetime.now().strftime("%a %d %b  %H:%M:%S"), font(15), (150, 156, 178))
    dx = W - 24 - (len(PAGES) - 1) * 22
    for i in range(len(PAGES)):
        cx, r = dx + i * 22, (6 if i == idx else 4)
        c = hexrgb(COL["blue"]) if i == idx else (70, 74, 96)
        d.ellipse([cx - r, fy + FOOT / 2 - r, cx + r, fy + FOOT / 2 + r], fill=c)
    return img


def fetch_all():
    exprs = []
    for _kind, _unit, _steps, ex, _extra in PANELS.values():
        exprs.extend(ex)
    return query_many(exprs)


class RackApp(App):
    name = "Rack Kiosk"
    refresh = 2.0                                   # re-query Prometheus + redraw every 2s

    def __init__(self):
        self.n_pages = len(PAGES)
        self.results = {}
        self._last = 0.0

    def update(self):
        now = time.time()
        if now - self._last >= self.refresh - 0.2:
            self.results = fetch_all()
            self._last = now

    def render(self, page):
        return render_page(page % self.n_pages, self.results)
