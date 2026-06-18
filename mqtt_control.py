#!/usr/bin/env python3
"""Home Assistant control for the TURZX kiosk via MQTT auto-discovery.

Publishes MQTT discovery configs so Home Assistant auto-creates entities to control
the kiosk (display on/off, auto-cycle, app select, page nav, interval, brightness)
and an alert service (text + timeout + send, plus a JSON command topic for automations).

Env: MQTT_HOST [MQTT_PORT=1883] [MQTT_USER] [MQTT_PASS]
"""
import json
import os
import threading

import paho.mqtt.client as mqtt

NODE = "turzx_kiosk"
BASE = "turzx/kiosk"
AVAIL = f"{BASE}/availability"
STATE = f"{BASE}/state"
CMD = f"{BASE}/cmd"
DISCO = "homeassistant"

DEVICE = {
    "identifiers": [NODE],
    "name": "TURZX Kiosk",
    "model": "Turing 8.8\"",
    "manufacturer": "TURZX",
}


def _entities(app_names):
    """(component, object_id, config) for each HA entity."""
    base = {"availability_topic": AVAIL, "device": DEVICE}

    def cfg(obj, name, **extra):
        c = dict(base, name=name, unique_id=f"{NODE}_{obj}", object_id=f"{NODE}_{obj}")
        c.update(extra)
        return c

    sv = lambda k: f"{{{{ value_json.{k} }}}}"  # noqa: E731
    return [
        ("switch", "display", cfg("display", "Display", command_topic=f"{CMD}/display",
            state_topic=STATE, value_template=sv("display"), payload_on="ON", payload_off="OFF", icon="mdi:monitor")),
        ("switch", "auto", cfg("auto", "Auto-cycle", command_topic=f"{CMD}/auto",
            state_topic=STATE, value_template=sv("auto"), payload_on="ON", payload_off="OFF", icon="mdi:autorenew")),
        ("select", "app", cfg("app", "App", command_topic=f"{CMD}/app",
            state_topic=STATE, value_template=sv("app"), options=app_names, icon="mdi:view-dashboard")),
        ("number", "interval", cfg("interval", "Page interval", command_topic=f"{CMD}/interval",
            state_topic=STATE, value_template=sv("interval"), min=2, max=60, step=1,
            unit_of_measurement="s", mode="box", icon="mdi:timer")),
        ("number", "brightness", cfg("brightness", "Brightness", command_topic=f"{CMD}/brightness",
            state_topic=STATE, value_template=sv("brightness"), min=0, max=100, step=5,
            unit_of_measurement="%", icon="mdi:brightness-6")),
        ("button", "next", cfg("next", "Next page", command_topic=f"{CMD}/next", payload_press="PRESS", icon="mdi:skip-next")),
        ("button", "prev", cfg("prev", "Previous page", command_topic=f"{CMD}/prev", payload_press="PRESS", icon="mdi:skip-previous")),
        ("sensor", "page", cfg("page", "Current page", state_topic=STATE, value_template=sv("page"), icon="mdi:book-open-page-variant")),
        # ---- alert service ----
        ("text", "alert_msg", cfg("alert_msg", "Alert message", command_topic=f"{CMD}/alert_msg",
            state_topic=STATE, value_template=sv("alert_msg"), max=120, icon="mdi:message-alert")),
        ("number", "alert_timeout", cfg("alert_timeout", "Alert timeout", command_topic=f"{CMD}/alert_timeout",
            state_topic=STATE, value_template=sv("alert_timeout"), min=1, max=600, step=1,
            unit_of_measurement="s", mode="box", icon="mdi:timer-alert")),
        ("button", "alert_send", cfg("alert_send", "Send alert", command_topic=f"{CMD}/alert_send", payload_press="PRESS", icon="mdi:bell-ring")),
        ("binary_sensor", "alert_active", cfg("alert_active", "Alert active", state_topic=STATE,
            value_template=sv("alert_active"), payload_on="ON", payload_off="OFF", device_class="problem")),
    ]


def start_mqtt(state):
    host = os.environ["MQTT_HOST"]
    port = int(os.environ.get("MQTT_PORT", "1883"))
    user = os.environ.get("MQTT_USER")
    pw = os.environ.get("MQTT_PASS")

    try:
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=NODE)
    except (AttributeError, TypeError):
        client = mqtt.Client(client_id=NODE)
    if user:
        client.username_pw_set(user, pw)
    client.will_set(AVAIL, "offline", retain=True)

    app_names = [a.name for a in state.apps]

    def publish_state():
        client.publish(STATE, json.dumps(state.status()), retain=True)

    def on_connect(c, *_):
        for comp, obj, conf in _entities(app_names):
            c.publish(f"{DISCO}/{comp}/{NODE}/{obj}/config", json.dumps(conf), retain=True)
        c.publish(AVAIL, "online", retain=True)
        c.subscribe(f"{CMD}/#")
        publish_state()

    def on_message(c, u, msg):
        sub = msg.topic.rsplit("/", 1)[-1]
        payload = msg.payload.decode(errors="replace").strip()
        try:
            if sub == "display":
                state.set_display(payload.upper() == "ON")
            elif sub == "auto":
                state.set_auto(payload.upper() == "ON")
            elif sub == "app":
                state.set_app(payload)
            elif sub == "interval":
                state.set_dwell(float(payload))
            elif sub == "brightness":
                state.set_brightness(int(float(payload)))
            elif sub == "next":
                state.nav(1)
            elif sub == "prev":
                state.nav(-1)
            elif sub == "alert_msg":
                with state.lock:
                    state.alert_msg = payload
                state._notify()
            elif sub == "alert_timeout":
                with state.lock:
                    state.alert_timeout = int(float(payload))
                state._notify()
            elif sub == "alert_send":
                state.fire_alert(state.alert_msg or "ALERT", state.alert_timeout)
            elif sub == "alert":          # JSON {text, timeout} for automations
                data = json.loads(payload)
                state.fire_alert(data.get("text", "ALERT"), data.get("timeout", state.alert_timeout))
        except Exception as e:
            print(f"mqtt cmd error ({sub}): {e}", flush=True)

    client.on_connect = on_connect
    client.on_message = on_message
    state.on_change = publish_state            # republish whenever state changes
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    threading.Thread(target=_heartbeat, args=(publish_state,), daemon=True).start()
    return client


def _heartbeat(publish_state, period=30):
    import time
    while True:
        time.sleep(period)
        try:
            publish_state()
        except Exception:
            pass
