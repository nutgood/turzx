#!/usr/bin/env python3
"""TURZX kiosk orchestrator.

Cycles through "apps" (each with one or more pages) on the Turing 8.8" USB display.
Page-cycling (within an app) and app-cycling are independent: each has its own
on/off toggle and its own interval. Supports an instant alert overlay (configurable
timeout) and optional Home Assistant control via MQTT auto-discovery (mqtt_control.py).

Config via env:
  GRAFANA_TOKEN / .grafana_token        Grafana token (dashboard app)
  MQTT_HOST [MQTT_PORT MQTT_USER MQTT_PASS]   enable Home Assistant MQTT control
  KIOSK_PAGE_INTERVAL (default 7)  KIOSK_APP_INTERVAL (default 30)  KIOSK_BRIGHTNESS (default 70)
"""
import json
import os
import socket
import sys
import threading
import time
from datetime import datetime

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))
sys.path.insert(0, HERE)

import rack_kiosk as RK  # reuse the dashboard rendering + helpers
from rack_kiosk import COL, W, H, font, hexrgb, draw_centered

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB


# ───────────────────────────── apps ─────────────────────────────
class App:
    name = "app"
    n_pages = 1
    refresh = 2.0          # seconds between re-renders of the current page (per-app)

    def update(self):
        pass

    def render(self, page):
        raise NotImplementedError


class RackApp(App):
    """The Grafana 'Rack Kiosk' dashboard — its 3 pages become this app's pages."""
    name = "Rack Kiosk"
    refresh = 2.0          # re-query Prometheus + redraw every 2s

    def __init__(self):
        self.n_pages = len(RK.PAGES)
        self.results = {}
        self._last = 0.0

    def update(self):
        now = time.time()
        if now - self._last >= self.refresh - 0.2:   # fetch ~each render
            self.results = RK.fetch_all()
            self._last = now

    def render(self, page):
        return RK.render_page(page % self.n_pages, self.results)


class ClockApp(App):
    name = "Clock"
    n_pages = 1
    refresh = 1.0          # tick every second

    def render(self, page):
        img = Image.new("RGB", (W, H), (6, 10, 24))
        d = ImageDraw.Draw(img)
        now = datetime.now()
        draw_centered(d, W / 2, H / 2 - 28, now.strftime("%H:%M:%S"), font(200), hexrgb(COL["text"]))
        draw_centered(d, W / 2, H - 64, now.strftime("%A %d %B %Y"), font(48), (150, 156, 178))
        return img


class PiStatsApp(App):
    name = "Pi Stats"
    n_pages = 1
    refresh = 2.0

    @staticmethod
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

    def render(self, page):
        s = self._read()
        img = Image.new("RGB", (W, H), RK.BG)
        d = ImageDraw.Draw(img)
        ncores = os.cpu_count() or 4

        def up(sec):
            if sec is None:
                return "—"
            dd, rem = divmod(int(sec), 86400)
            hh, rem = divmod(rem, 3600)
            mm = rem // 60
            return (f"{dd}d {hh}h" if dd else f"{hh}h {mm}m")

        tiles = [
            ("CPU TEMP", f"{s['temp']:.0f}°C" if s['temp'] is not None else "—",
             [(0, "green"), (60, "orange"), (75, "red")], s['temp']),
            ("LOAD (1m)", f"{s['load']:.2f}" if s['load'] is not None else "—",
             [(0, "green"), (ncores * 0.8, "orange"), (ncores * 1.5, "red")], s['load']),
            ("MEMORY", f"{s['mem']:.0f}%" if s['mem'] is not None else "—",
             [(0, "green"), (75, "orange"), (90, "red")], s['mem']),
            ("UPTIME", up(s['uptime']), [(0, "blue")], 0),
        ]
        tw = W / len(tiles)
        for i, (title, val, steps, raw) in enumerate(tiles):
            x = i * tw
            d.rounded_rectangle([x + 6, 6, x + tw - 6, H - 34], radius=8,
                                fill=RK.TILE, outline=RK.TILE_BD, width=1)
            d.text((x + 18, 20), title, font=font(20), fill=RK.TITLE_C)
            color = RK.th_color(steps, raw if raw is not None else 0) if raw is not None else hexrgb(COL["text"])
            draw_centered(d, x + tw / 2, H / 2 + 6, val, font(76), color)
        d.text((16, H - 26), f"PI STATS — {s['host']}", font=font(15), fill=(150, 156, 178))
        return img


# ───────────────────────────── state ─────────────────────────────
class State:
    # options set via MQTT/HA that persist across restarts (NOT transient alert state)
    PERSIST = ("auto_page", "auto_app", "page_interval", "app_interval", "brightness",
               "display_on", "app_idx", "page_idx", "alert_msg", "alert_timeout")

    def __init__(self, apps, page_interval, app_interval, brightness, persist_path=None):
        self.apps = apps
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.app_idx = 0
        self.page_idx = 0
        self.auto_page = True            # cycle pages within the current app
        self.auto_app = True             # cycle through apps
        self.page_interval = page_interval
        self.app_interval = app_interval
        self.display_on = True
        self.brightness = brightness
        self.alert_props = None          # dict of alert properties, or None
        self.alert_until = 0.0
        self.alert_msg = ""              # buffer for HA text entity
        self.alert_timeout = 20          # buffer for HA number entity
        self.on_change = None            # set by MQTT to publish state
        self.persist_path = persist_path
        self._load()

    # ---- persistence (only user/MQTT changes hit _notify, so auto-cycle never writes) ----
    def _load(self):
        if not self.persist_path or not os.path.exists(self.persist_path):
            return
        try:
            d = json.load(open(self.persist_path))
        except Exception as e:
            print(f"state load failed: {e}", file=sys.stderr)
            return
        with self.lock:
            for k in self.PERSIST:
                if k in d:
                    setattr(self, k, d[k])
            self.app_idx %= len(self.apps)
            self.page_idx %= self.apps[self.app_idx].n_pages
            self.brightness = max(0, min(100, int(self.brightness)))
            self.page_interval = max(2.0, float(self.page_interval))
            self.app_interval = max(3.0, float(self.app_interval))

    def _save(self):
        if not self.persist_path:
            return
        try:
            with self.lock:
                d = {k: getattr(self, k) for k in self.PERSIST}
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, self.persist_path)
        except Exception as e:
            print(f"state save failed: {e}", file=sys.stderr)

    def _notify(self):
        self.wake.set()
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass
        self._save()

    def _clear_alert_locked(self):
        self.alert_until = 0.0
        self.alert_props = None

    # ---- app navigation (also dismisses any active alert) ----
    def set_app(self, name_or_idx):
        with self.lock:
            if isinstance(name_or_idx, int):
                self.app_idx = name_or_idx % len(self.apps)
            else:
                for i, a in enumerate(self.apps):
                    if a.name.lower() == str(name_or_idx).lower():
                        self.app_idx = i
                        break
            self.page_idx = 0
            self._clear_alert_locked()
        self._notify()

    def next_app(self):
        with self.lock:
            self.app_idx = (self.app_idx + 1) % len(self.apps)
            self.page_idx = 0
            self._clear_alert_locked()
        self._notify()

    def prev_app(self):
        with self.lock:
            self.app_idx = (self.app_idx - 1) % len(self.apps)
            self.page_idx = 0
            self._clear_alert_locked()
        self._notify()

    # ---- page navigation (within the current app; also dismisses any active alert) ----
    def next_page(self):
        with self.lock:
            self.page_idx = (self.page_idx + 1) % self.apps[self.app_idx].n_pages
            self._clear_alert_locked()
        self._notify()

    def prev_page(self):
        with self.lock:
            self.page_idx = (self.page_idx - 1) % self.apps[self.app_idx].n_pages
            self._clear_alert_locked()
        self._notify()

    # ---- internal auto-advances (used by the loop timers) ----
    def adv_page(self):
        with self.lock:
            self.page_idx = (self.page_idx + 1) % self.apps[self.app_idx].n_pages

    def adv_app(self):
        with self.lock:
            self.app_idx = (self.app_idx + 1) % len(self.apps)
            self.page_idx = 0

    # ---- settings ----
    def set_auto_page(self, on):
        with self.lock:
            self.auto_page = bool(on)
        self._notify()

    def set_auto_app(self, on):
        with self.lock:
            self.auto_app = bool(on)
        self._notify()

    def set_page_interval(self, secs):
        with self.lock:
            self.page_interval = max(2.0, float(secs))
        self._notify()

    def set_app_interval(self, secs):
        with self.lock:
            self.app_interval = max(3.0, float(secs))
        self._notify()

    def set_display(self, on):
        with self.lock:
            self.display_on = bool(on)
        self._notify()

    def set_brightness(self, level):
        with self.lock:
            self.brightness = max(0, min(100, int(level)))
        self._notify()

    def fire_alert(self, props):
        """props: dict of alert properties (message, timeout, title, level, color,
        accent, text_color, icon, blink, size). A bare string is treated as message."""
        p = dict(props) if isinstance(props, dict) else {"message": str(props)}
        try:
            to = float(p.get("timeout"))
        except (TypeError, ValueError):
            to = self.alert_timeout
        with self.lock:
            self.alert_props = p
            self.alert_until = time.time() + max(1, to)
        self._notify()

    def clear_alert(self):
        with self.lock:
            self.alert_until = 0.0
            self.alert_props = None
        self._notify()

    def snapshot(self):
        with self.lock:
            return dict(app_idx=self.app_idx, page_idx=self.page_idx,
                        auto_page=self.auto_page, auto_app=self.auto_app,
                        page_interval=self.page_interval, app_interval=self.app_interval,
                        display_on=self.display_on, brightness=self.brightness,
                        alert_props=self.alert_props, alert_until=self.alert_until)

    def status(self):
        with self.lock:
            app = self.apps[self.app_idx]
            return {
                "display": "ON" if self.display_on else "OFF",
                "auto_page": "ON" if self.auto_page else "OFF",
                "auto_app": "ON" if self.auto_app else "OFF",
                "app": app.name,
                "page": f"{self.page_idx + 1}/{app.n_pages}",
                "page_interval": int(self.page_interval),
                "app_interval": int(self.app_interval),
                "brightness": self.brightness,
                "alert_active": "ON" if (self.alert_until > time.time()) else "OFF",
                "alert_msg": self.alert_msg,
                "alert_timeout": int(self.alert_timeout),
            }


# ───────────────────────────── alert rendering ─────────────────────────────
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


# severity presets: (background, accent color name, default icon)
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


def render_alert(props, remaining, blink_on=True):
    p = props or {}
    L = LEVELS.get(str(p.get("level") or "warning").lower(), LEVELS["warning"])
    bg = _hexc(p.get("color"), L["bg"])
    accent = _hexc(p.get("accent"), hexrgb(COL.get(L["accent"], "#F2495C")))
    txtcol = _hexc(p.get("text_color"), (255, 255, 255))
    icon = str(p.get("icon") or L["icon"]).lower()
    title = str(p.get("title") or "ALERT")
    message = str(p.get("message") or "")
    show = (not p.get("blink")) or blink_on   # blink hides the chrome on the off-beat

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
    while size <= 0 and len(lines) > 3 and fnt.size > 40:   # auto-shrink to ≤3 lines
        fnt = font(fnt.size - 8)
        lines = _wrap(d, message, fnt, W - 100)
    total_h = len(lines) * (fnt.size + 10)
    y = (H - total_h) / 2 + 24
    for ln in lines:
        draw_centered(d, W / 2, y + fnt.size / 2, ln, fnt, txtcol)
        y += fnt.size + 10
    return img


# ───────────────────────────── main loop ─────────────────────────────
def wait_for_grafana(timeout=30):
    host = RK.GURL.split("://", 1)[-1].split("/")[0].split(":")[0]
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.getaddrinfo(host, 443)
            return True
        except Exception:
            time.sleep(2)
    return False


def main():
    page_interval = float(os.environ.get("KIOSK_PAGE_INTERVAL", os.environ.get("KIOSK_DWELL", "7")))
    app_interval = float(os.environ.get("KIOSK_APP_INTERVAL", "30"))
    brightness = int(os.environ.get("KIOSK_BRIGHTNESS", "70"))
    apps = [RackApp(), ClockApp(), PiStatsApp()]
    state = State(apps, page_interval, app_interval, brightness,
                  persist_path=os.path.join(HERE, "state.json"))

    if os.environ.get("MQTT_HOST"):
        try:
            from mqtt_control import start_mqtt
            start_mqtt(state)
            print("MQTT control enabled", flush=True)
        except Exception as e:
            print(f"MQTT disabled: {e}", flush=True)

    wait_for_grafana()
    lcd = LcdCommTuringUSB(com_port="AUTO")
    lcd.InitializeComm()
    lcd.SetOrientation(Orientation.LANDSCAPE)
    applied_bri = -1
    applied_on = None
    page_due = None
    app_due = None

    while True:
        st = state.snapshot()

        # display on/off via brightness
        if not st["display_on"]:
            if applied_on is not False:
                lcd.SetBrightness(0)
                applied_on = False
            page_due = app_due = None
            state.wake.wait(timeout=2)
            state.wake.clear()
            continue
        if applied_on is not True or applied_bri != st["brightness"]:
            lcd.SetBrightness(st["brightness"])
            applied_bri = st["brightness"]
            applied_on = True

        now = time.time()
        # alert overlay (instant, time-limited) — overrides everything
        if st["alert_props"] and st["alert_until"] > now:
            blink_on = int(now * 2) % 2 == 0
            lcd.DisplayPILImage(render_alert(st["alert_props"], st["alert_until"] - now, blink_on))
            page_due = app_due = None       # re-arm cycle timers after the alert clears
            state.wake.wait(timeout=min(0.5, st["alert_until"] - now))
            state.wake.clear()
            continue

        # arm independent cycle timers
        if st["auto_page"]:
            if page_due is None:
                page_due = now + st["page_interval"]
        else:
            page_due = None
        if st["auto_app"]:
            if app_due is None:
                app_due = now + st["app_interval"]
        else:
            app_due = None

        # render current app/page
        app = apps[st["app_idx"]]
        try:
            app.update()
        except Exception as e:
            print(f"app update error: {e}", file=sys.stderr)
        lcd.DisplayPILImage(app.render(st["page_idx"]))
        if state.on_change:
            state.on_change()

        # wake at the soonest of: this app's re-render cadence, page cycle, app cycle
        render_due = time.time() + max(0.2, getattr(app, "refresh", 2.0))
        deadlines = [d for d in (page_due, app_due, render_due) if d is not None]
        woken = state.wake.wait(timeout=max(0.0, min(deadlines) - time.time()))
        state.wake.clear()
        if woken:
            page_due = app_due = None       # control changed — re-arm from now, re-render
            continue

        now2 = time.time()
        if app_due is not None and now2 >= app_due:      # app cycle fires (takes priority)
            state.adv_app()
            app_due = now2 + st["app_interval"]
            page_due = (now2 + st["page_interval"]) if st["auto_page"] else None
        elif page_due is not None and now2 >= page_due:  # page cycle fires (stays within app)
            state.adv_page()
            page_due = now2 + st["page_interval"]
        # else: render_due fired — just re-render the same page (cycle timers keep counting)


if __name__ == "__main__":
    main()
