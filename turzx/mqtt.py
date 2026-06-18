"""Home Assistant control via MQTT auto-discovery.

Publishes retained discovery configs so HA auto-creates a "TURZX Kiosk" device, subscribes
to command topics, and republishes state. Env: MQTT_HOST [MQTT_PORT MQTT_USER MQTT_PASS].
"""
import json
import os
import threading
import time

import paho.mqtt.client as mqtt

NODE = "turzx_kiosk"
BASE = "turzx/kiosk"
AVAIL = f"{BASE}/availability"
STATE = f"{BASE}/state"
CMD = f"{BASE}/cmd"
DISCO = "homeassistant"
DEVICE = {"identifiers": [NODE], "name": "TURZX Kiosk", "model": "Turing 8.8\"", "manufacturer": "TURZX"}

# Entities removed/renamed in older versions — discovery configs are retained, so we must
# clear them on connect or HA keeps showing stale duplicates.
LEGACY = [("switch", "auto"), ("number", "interval"), ("button", "next"), ("button", "prev")]


def _entities(app_names):
    base = {"availability_topic": AVAIL, "device": DEVICE}

    def cfg(obj, name, **extra):
        return dict(base, name=name, unique_id=f"{NODE}_{obj}", object_id=f"{NODE}_{obj}", **extra)

    sv = lambda k: f"{{{{ value_json.{k} }}}}"  # noqa: E731
    btn = lambda obj, name, icon: ("button", obj, cfg(obj, name, command_topic=f"{CMD}/{obj}", payload_press="PRESS", icon=icon))  # noqa: E731
    return [
        ("switch", "display", cfg("display", "Display", command_topic=f"{CMD}/display",
            state_topic=STATE, value_template=sv("display"), payload_on="ON", payload_off="OFF", icon="mdi:monitor")),
        ("select", "app", cfg("app", "App", command_topic=f"{CMD}/app",
            state_topic=STATE, value_template=sv("app"), options=app_names, icon="mdi:view-dashboard")),
        ("sensor", "page", cfg("page", "Current page", state_topic=STATE, value_template=sv("page"), icon="mdi:book-open-page-variant")),
        btn("next_app", "Next app", "mdi:page-next"),
        btn("prev_app", "Previous app", "mdi:page-previous"),
        btn("next_page", "Next page", "mdi:skip-next"),
        btn("prev_page", "Previous page", "mdi:skip-previous"),
        ("switch", "auto_page", cfg("auto_page", "Auto-cycle pages", command_topic=f"{CMD}/auto_page",
            state_topic=STATE, value_template=sv("auto_page"), payload_on="ON", payload_off="OFF", icon="mdi:autorenew")),
        ("switch", "auto_app", cfg("auto_app", "Auto-cycle apps", command_topic=f"{CMD}/auto_app",
            state_topic=STATE, value_template=sv("auto_app"), payload_on="ON", payload_off="OFF", icon="mdi:rotate-3d-variant")),
        ("number", "page_interval", cfg("page_interval", "Page interval", command_topic=f"{CMD}/page_interval",
            state_topic=STATE, value_template=sv("page_interval"), min=2, max=120, step=1, unit_of_measurement="s", mode="box", icon="mdi:timer")),
        ("number", "app_interval", cfg("app_interval", "App interval", command_topic=f"{CMD}/app_interval",
            state_topic=STATE, value_template=sv("app_interval"), min=3, max=600, step=1, unit_of_measurement="s", mode="box", icon="mdi:timer-cog")),
        ("number", "brightness", cfg("brightness", "Brightness", command_topic=f"{CMD}/brightness",
            state_topic=STATE, value_template=sv("brightness"), min=0, max=100, step=5, unit_of_measurement="%", icon="mdi:brightness-6")),
        ("text", "alert_msg", cfg("alert_msg", "Alert message", command_topic=f"{CMD}/alert_msg",
            state_topic=STATE, value_template=sv("alert_msg"), max=160, icon="mdi:message-alert")),
        ("number", "alert_timeout", cfg("alert_timeout", "Alert timeout", command_topic=f"{CMD}/alert_timeout",
            state_topic=STATE, value_template=sv("alert_timeout"), min=1, max=600, step=1, unit_of_measurement="s", mode="box", icon="mdi:timer-alert")),
        btn("alert_send", "Send alert", "mdi:bell-ring"),
        ("binary_sensor", "alert_active", cfg("alert_active", "Alert active", state_topic=STATE,
            value_template=sv("alert_active"), payload_on="ON", payload_off="OFF", device_class="problem")),
        # Auto-discovered notify action -> notify.turzx_kiosk_alert (message-only, no YAML).
        # Rich/multi-property alerts: script.kiosk_alert or publish JSON to cmd/alert.
        ("notify", "alert", cfg("alert", "Alert", command_topic=f"{CMD}/alert", icon="mdi:bell-alert")),
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
        for comp, obj in LEGACY:
            c.publish(f"{DISCO}/{comp}/{NODE}/{obj}/config", "", retain=True)
        for comp, obj, conf in _entities(app_names):
            c.publish(f"{DISCO}/{comp}/{NODE}/{obj}/config", json.dumps(conf), retain=True)
        c.publish(AVAIL, "online", retain=True)
        c.subscribe(f"{CMD}/#")
        publish_state()

    def on_message(c, u, msg):
        sub = msg.topic.rsplit("/", 1)[-1]
        payload = msg.payload.decode(errors="replace").strip()
        on = payload.upper() == "ON"
        try:
            if sub == "display":
                state.set_display(on)
            elif sub == "auto_page":
                state.set_auto_page(on)
            elif sub == "auto_app":
                state.set_auto_app(on)
            elif sub == "app":
                state.set_app(payload)
            elif sub == "page_interval":
                state.set_page_interval(float(payload))
            elif sub == "app_interval":
                state.set_app_interval(float(payload))
            elif sub == "brightness":
                state.set_brightness(int(float(payload)))
            elif sub == "next_app":
                state.next_app()
            elif sub == "prev_app":
                state.prev_app()
            elif sub == "next_page":
                state.next_page()
            elif sub == "prev_page":
                state.prev_page()
            elif sub == "alert_msg":
                state.set_alert_msg(payload)
            elif sub == "alert_timeout":
                state.set_alert_timeout(float(payload))
            elif sub == "alert_send":
                state.fire_alert({"message": state.alert_msg or "ALERT", "timeout": state.alert_timeout})
            elif sub == "alert":          # JSON props (automations) OR plain text (notify entity)
                try:
                    props = json.loads(payload)
                    if not isinstance(props, dict):
                        props = {"message": str(props)}
                except (ValueError, TypeError):
                    props = {"message": payload}
                state.fire_alert(props)
        except Exception as e:
            print(f"mqtt cmd error ({sub}): {e}", flush=True)

    client.on_connect = on_connect
    client.on_message = on_message
    state.on_change = publish_state
    client.connect_async(host, port, keepalive=30)
    client.loop_start()
    threading.Thread(target=_heartbeat, args=(publish_state,), daemon=True).start()
    return client


def _heartbeat(publish_state, period=30):
    while True:
        time.sleep(period)
        try:
            publish_state()
        except Exception:
            pass
