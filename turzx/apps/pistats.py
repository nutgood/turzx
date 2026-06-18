"""Pi Stats app — the host's CPU temp / load / memory / uptime (reads /proc + /sys)."""
import os
import socket

from ..render import COL, H, W, TILE, TILE_BD, TITLE_C, blank, draw_centered, font, hexrgb, th_color
from .base import App


def _read():
    s = {"host": socket.gethostname()}
    try:
        s["temp"] = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000.0
    except Exception:
        s["temp"] = None
    try:
        s["load"] = float(open("/proc/loadavg").read().split()[0])
    except Exception:
        s["load"] = None
    try:
        mi = {}
        for ln in open("/proc/meminfo"):
            k, v = ln.split(":")
            mi[k] = int(v.strip().split()[0])
        s["mem"] = (1 - mi["MemAvailable"] / mi["MemTotal"]) * 100
    except Exception:
        s["mem"] = None
    try:
        s["uptime"] = float(open("/proc/uptime").read().split()[0])
    except Exception:
        s["uptime"] = None
    return s


def _uptime(sec):
    if sec is None:
        return "—"
    dd, rem = divmod(int(sec), 86400)
    hh, rem = divmod(rem, 3600)
    return f"{dd}d {hh}h" if dd else f"{hh}h {rem // 60}m"


class PiStatsApp(App):
    name = "Pi Stats"
    n_pages = 1
    refresh = 2.0

    def render(self, page):
        s = _read()
        ncores = os.cpu_count() or 4
        img, d = blank()
        tiles = [
            ("CPU TEMP", f"{s['temp']:.0f}°C" if s['temp'] is not None else "—",
             [(0, "green"), (60, "orange"), (75, "red")], s['temp']),
            ("LOAD (1m)", f"{s['load']:.2f}" if s['load'] is not None else "—",
             [(0, "green"), (ncores * 0.8, "orange"), (ncores * 1.5, "red")], s['load']),
            ("MEMORY", f"{s['mem']:.0f}%" if s['mem'] is not None else "—",
             [(0, "green"), (75, "orange"), (90, "red")], s['mem']),
            ("UPTIME", _uptime(s['uptime']), [(0, "blue")], 0),
        ]
        tw = W / len(tiles)
        for i, (title, val, steps, raw) in enumerate(tiles):
            x = i * tw
            d.rounded_rectangle([x + 6, 6, x + tw - 6, H - 34], radius=8, fill=TILE, outline=TILE_BD, width=1)
            d.text((x + 18, 20), title, font=font(20), fill=TITLE_C)
            color = th_color(steps, raw) if raw is not None else hexrgb(COL["text"])
            draw_centered(d, x + tw / 2, H / 2 + 6, val, font(76), color)
        d.text((16, H - 26), f"PI STATS — {s['host']}", font=font(15), fill=(150, 156, 178))
        return img
