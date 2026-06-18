"""Kiosk control state (thread-safe). Single source of truth for the orchestrator.

User/MQTT changes call ``_notify()`` (wake loop + publish + persist). Auto-cycle advances
(``adv_page``/``adv_app``) deliberately do NOT notify, so persistence/MQTT don't fire on
every tick (no SD-card wear). Persisted options survive restarts via ``state.json``.
"""
import json
import os
import sys
import threading
import time


class State:
    # options set via MQTT/HA that persist across restarts (not transient alert state)
    PERSIST = ("auto_page", "auto_app", "page_interval", "app_interval", "brightness",
               "display_on", "app_idx", "page_idx", "alert_msg", "alert_timeout")

    def __init__(self, apps, page_interval=7.0, app_interval=30.0, brightness=70, persist_path=None):
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

    # ---- persistence ----
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

    # ---- internal auto-advances (used by the loop timers; no notify) ----
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

    def set_alert_msg(self, msg):
        with self.lock:
            self.alert_msg = str(msg)
        self._notify()

    def set_alert_timeout(self, secs):
        with self.lock:
            self.alert_timeout = int(float(secs))
        self._notify()

    def fire_alert(self, props):
        """props: dict (message, timeout, title, level, color, accent, text_color, icon,
        blink, size). A bare string is treated as the message."""
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
            self._clear_alert_locked()
        self._notify()

    # ---- snapshots ----
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
