"""Kiosk orchestrator — owns the display and runs the render/cycle/alert loop.

Page-cycling (within an app) and app-cycling are independent (own toggle + interval);
each app re-renders at its own ``refresh`` cadence. An alert overlay interrupts instantly
for a configurable timeout. Optional Home Assistant MQTT control.

Config via env: GRAFANA_TOKEN/.grafana_token, MQTT_HOST [MQTT_PORT/USER/PASS],
KIOSK_PAGE_INTERVAL (7), KIOSK_APP_INTERVAL (30), KIOSK_BRIGHTNESS (70).
"""
import os
import socket
import sys
import time

import usb.core

from . import ROOT, grafana
from .alert import render_alert
from .apps import default_apps
from .device import Orientation, dispose, open_display, present
from .state import State


def _wait_for_grafana(timeout=30):
    host = grafana.GURL.split("://", 1)[-1].split("/")[0].split(":")[0]
    end = time.time() + timeout
    while time.time() < end:
        try:
            socket.getaddrinfo(host, 443)
            return
        except Exception:
            time.sleep(2)


def _connect(brightness):
    """Block until a display is present, then open it. Survives disconnects/replugs."""
    waited = False
    while True:
        if present():
            try:
                lcd = open_display(brightness=brightness, orientation=Orientation.LANDSCAPE)
                print("display connected", flush=True)
                return lcd
            except Exception as e:
                print(f"display open failed: {e}", file=sys.stderr, flush=True)
        elif not waited:
            print("waiting for display…", flush=True)
            waited = True
        time.sleep(2)


def main():
    page_interval = float(os.environ.get("KIOSK_PAGE_INTERVAL", os.environ.get("KIOSK_DWELL", "7")))
    app_interval = float(os.environ.get("KIOSK_APP_INTERVAL", "30"))
    brightness = int(os.environ.get("KIOSK_BRIGHTNESS", "70"))

    apps = default_apps()
    state = State(apps, page_interval, app_interval, brightness,
                  persist_path=os.path.join(ROOT, "state.json"))
    cam_app = next((a for a in apps if hasattr(a, "ensure_warm")), None)

    if os.environ.get("MQTT_HOST"):
        try:
            from .mqtt import start_mqtt
            start_mqtt(state)
            print("MQTT control enabled", flush=True)
        except Exception as e:
            print(f"MQTT disabled: {e}", flush=True)

    _wait_for_grafana()
    lcd = _connect(brightness)
    applied_bri, applied_on = -1, None
    page_due = app_due = None
    next_present_check = 0.0

    while True:
        st = state.snapshot()
        try:
            # The driver swallows USB write errors, so poll presence to catch disconnects.
            if time.time() >= next_present_check:
                next_present_check = time.time() + 2.0
                if not present():
                    raise ConnectionError("display absent")

            # keep the camera pipeline warm in the background if requested (instant switching)
            if cam_app is not None:
                cam_app.ensure_warm(st["cam_warm"])

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
                applied_bri, applied_on = st["brightness"], True

            now = time.time()
            # alert overlay (instant, time-limited) — overrides everything
            if st["alert_props"] and st["alert_until"] > now:
                blink_on = int(now * 2) % 2 == 0
                lcd.DisplayPILImage(render_alert(st["alert_props"], st["alert_until"] - now, blink_on))
                page_due = app_due = None
                state.wake.wait(timeout=min(0.5, st["alert_until"] - now))
                state.wake.clear()
                continue

            app = apps[st["app_idx"]]
            # streaming apps (e.g. Cameras) drive the display directly via H.264 while active
            if getattr(app, "stream", None):
                idx = st["app_idx"]
                started = time.time()

                def _stop():
                    s = state.snapshot()
                    if (not s["display_on"]
                            or (s["alert_props"] and s["alert_until"] > time.time())
                            or s["app_idx"] != idx
                            or state.wake.is_set()
                            or not present()):
                        return True
                    if s["cam_alert_return"] and s["cam_alert_until"]:   # camera-alert: hold until it expires
                        return time.time() > s["cam_alert_until"]
                    return s["auto_app"] and time.time() - started >= s["app_interval"]

                app.stream(lcd, _stop, st["brightness"], state)
                applied_on = None              # video mode left the device in a new state
                state.wake.clear()
                if not present():
                    raise ConnectionError("display absent")
                s2 = state.snapshot()
                now3 = time.time()
                alerting = s2["alert_props"] and s2["alert_until"] > now3
                if s2["app_idx"] != idx:
                    if s2["cam_alert_return"]:          # navigated away mid camera-alert
                        state.cam_alert_return = None
                        state.clear_cam_alert()
                elif s2["cam_alert_return"] and s2["cam_alert_until"] and now3 > s2["cam_alert_until"]:
                    ret = s2["cam_alert_return"]        # camera-alert expired → return
                    state.cam_alert_return = None
                    state.clear_cam_alert()
                    state.set_app(ret)
                elif (s2["auto_app"] and now3 - started >= s2["app_interval"]
                        and s2["display_on"] and not alerting):
                    state.adv_app()                    # dwell elapsed → advance to next app
                page_due = app_due = None
                continue

            # arm independent cycle timers
            if st["auto_page"]:
                page_due = page_due or now + st["page_interval"]
            else:
                page_due = None
            if st["auto_app"]:
                app_due = app_due or now + st["app_interval"]
            else:
                app_due = None

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
                page_due = app_due = None   # control changed — re-arm + re-render
                continue

            now2 = time.time()
            if app_due is not None and now2 >= app_due:       # app cycle (takes priority)
                state.adv_app()
                app_due = now2 + st["app_interval"]
                page_due = (now2 + st["page_interval"]) if st["auto_page"] else None
            elif page_due is not None and now2 >= page_due:   # page cycle (within app)
                state.adv_page()
                page_due = now2 + st["page_interval"]
            # else: render_due fired — re-render same page; cycle timers keep counting

        except (usb.core.USBError, ConnectionError) as e:
            print(f"display disconnected ({e}); reconnecting…", file=sys.stderr, flush=True)
            dispose(lcd)
            lcd = _connect(brightness)      # blocks until replugged, then re-init
            applied_bri, applied_on = -1, None
            page_due = app_due = None
            next_present_check = 0.0
