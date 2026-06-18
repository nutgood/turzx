#!/usr/bin/env python3
"""TURZX kiosk orchestrator.

Cycles through "apps" (each with one or more pages) on the Turing 8.8" USB display.
Supports an instant alert overlay (configurable timeout) and optional Home Assistant
control via MQTT auto-discovery (see mqtt_control.py).

Config via env:
  GRAFANA_TOKEN / .grafana_token      Grafana service-account token (for the dashboard app)
  MQTT_HOST [MQTT_PORT MQTT_USER MQTT_PASS]   enable Home Assistant MQTT control
  KIOSK_DWELL (default 7)  KIOSK_BRIGHTNESS (default 70)
"""
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
from rack_kiosk import COL, W, H, font, hexrgb, draw_centered, fmt

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB


# ───────────────────────────── apps ─────────────────────────────
class App:
    name = "app"
    n_pages = 1

    def update(self):
        pass

    def render(self, page):
        raise NotImplementedError


class RackApp(App):
    """The Grafana 'Rack Kiosk' dashboard — its 3 pages become this app's pages."""
    name = "Rack Kiosk"

    def __init__(self):
        self.n_pages = len(RK.PAGES)
        self.results = {}
        self._last = 0.0

    def update(self):
        now = time.time()
        if now - self._last > 4.5:          # throttle Prometheus fetches
            self.results = RK.fetch_all()
            self._last = now

    def render(self, page):
        return RK.render_page(page % self.n_pages, self.results)


class ClockApp(App):
    name = "Clock"
    n_pages = 1

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
    def __init__(self, apps, dwell, brightness):
        self.apps = apps
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.app_idx = 0
        self.page_idx = 0
        self.auto = True
        self.dwell = dwell
        self.display_on = True
        self.brightness = brightness
        self.alert_text = None
        self.alert_until = 0.0
        self.alert_msg = ""          # buffer for HA text entity
        self.alert_timeout = 20      # buffer for HA number entity
        self.on_change = None        # set by MQTT to publish state

    # ---- mutations (thread-safe) ----
    def _notify(self):
        self.wake.set()
        if self.on_change:
            try:
                self.on_change()
            except Exception:
                pass

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
        self._notify()

    def set_page(self, idx):
        with self.lock:
            self.page_idx = max(0, int(idx)) % self.apps[self.app_idx].n_pages
        self._notify()

    def nav(self, delta):
        with self.lock:
            self._advance(delta)
        self._notify()

    def _advance(self, delta=1):
        self.page_idx += delta
        if self.page_idx >= self.apps[self.app_idx].n_pages:
            self.page_idx = 0
            self.app_idx = (self.app_idx + 1) % len(self.apps)
        elif self.page_idx < 0:
            self.app_idx = (self.app_idx - 1) % len(self.apps)
            self.page_idx = self.apps[self.app_idx].n_pages - 1

    def set_auto(self, on):
        with self.lock:
            self.auto = bool(on)
        self._notify()

    def set_dwell(self, secs):
        with self.lock:
            self.dwell = max(2.0, float(secs))
        self._notify()

    def set_display(self, on):
        with self.lock:
            self.display_on = bool(on)
        self._notify()

    def set_brightness(self, level):
        with self.lock:
            self.brightness = max(0, min(100, int(level)))
        self._notify()

    def fire_alert(self, text, timeout):
        with self.lock:
            self.alert_text = str(text)
            self.alert_until = time.time() + max(1, float(timeout))
        self._notify()

    def clear_alert(self):
        with self.lock:
            self.alert_until = 0.0
            self.alert_text = None
        self._notify()

    def snapshot(self):
        with self.lock:
            return dict(app_idx=self.app_idx, page_idx=self.page_idx, auto=self.auto,
                        dwell=self.dwell, display_on=self.display_on, brightness=self.brightness,
                        alert_text=self.alert_text, alert_until=self.alert_until)

    def status(self):
        with self.lock:
            app = self.apps[self.app_idx]
            return {
                "display": "ON" if self.display_on else "OFF",
                "auto": "ON" if self.auto else "OFF",
                "app": app.name,
                "page": f"{self.page_idx + 1}/{app.n_pages}",
                "interval": int(self.dwell),
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


def render_alert(text, remaining):
    img = Image.new("RGB", (W, H), (44, 10, 12))
    d = ImageDraw.Draw(img)
    red = hexrgb(COL["red"])
    d.rectangle([0, 0, W, 8], fill=red)
    d.rectangle([0, H - 8, W, H], fill=red)
    # warning triangle (drawn, not glyph) + label
    d.polygon([(54, 56), (30, 26), (78, 26)], outline=red, width=4)
    d.text((50, 33), "!", font=font(30), fill=red)
    d.text((92, 20), "ALERT", font=font(46), fill=red)
    d.text((W - 150, 24), f"{int(remaining)}s", font=font(34), fill=(210, 150, 150))
    fnt = font(96)
    lines = _wrap(d, text, fnt, W - 100)
    while len(lines) > 3 and fnt.size > 40:          # shrink to fit ≤3 lines
        fnt = font(fnt.size - 8)
        lines = _wrap(d, text, fnt, W - 100)
    total_h = len(lines) * (fnt.size + 10)
    y = (H - total_h) / 2 + 24
    for ln in lines:
        draw_centered(d, W / 2, y + fnt.size / 2, ln, fnt, (255, 255, 255))
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
    dwell = float(os.environ.get("KIOSK_DWELL", "7"))
    brightness = int(os.environ.get("KIOSK_BRIGHTNESS", "70"))
    apps = [RackApp(), ClockApp(), PiStatsApp()]
    state = State(apps, dwell, brightness)

    # optional Home Assistant MQTT control
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

    while True:
        st = state.snapshot()

        # display on/off via brightness
        if not st["display_on"]:
            if applied_on is not False:
                lcd.SetBrightness(0)
                applied_on = False
            state.wake.wait(timeout=2)
            state.wake.clear()
            continue
        if applied_on is not True or applied_bri != st["brightness"]:
            lcd.SetBrightness(st["brightness"])
            applied_bri = st["brightness"]
            applied_on = True

        now = time.time()
        # alert overlay (instant, time-limited)
        if st["alert_text"] and st["alert_until"] > now:
            lcd.DisplayPILImage(render_alert(st["alert_text"], st["alert_until"] - now))
            state.wake.wait(timeout=min(1.0, st["alert_until"] - now))
            state.wake.clear()
            continue

        # normal app/page
        app = apps[st["app_idx"]]
        try:
            app.update()
        except Exception as e:
            print(f"app update error: {e}", file=sys.stderr)
        lcd.DisplayPILImage(app.render(st["page_idx"]))
        if state.on_change:
            state.on_change()

        woken = state.wake.wait(timeout=st["dwell"] if st["auto"] else 3600)
        state.wake.clear()
        if woken:
            continue          # react to control change; don't auto-advance
        if st["auto"]:
            with state.lock:
                state._advance()


if __name__ == "__main__":
    main()
