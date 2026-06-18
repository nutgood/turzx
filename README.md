# turzx

Tooling to drive a **Turing / TURZX USB smart display** (the new-generation, vendor-USB
protocol models — e.g. the 8.8" 480×1920 bar screen, VID `0x1cbe`) from Python, plus a
faithful on-device replica of a Grafana dashboard ("Rack Kiosk").

Built and verified against the **Turing 8.8"** (`1cbe:0088`, native 480×1920, viewed in
landscape 1920×480). These models are **not** serial devices — they speak a vendor-specific
USB protocol (libusb bulk transfers, DES-encrypted command packets), driven via the
`LcdCommTuringUSB` class from [mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(cloned into `lib/` by `setup.sh`).

## What's here

| Script | Purpose |
|---|---|
| `kiosk.py` | **Main entrypoint** — orchestrator that cycles through *apps* (Rack Kiosk dashboard, Clock, Pi Stats), with an instant alert overlay and optional Home Assistant MQTT control. |
| `mqtt_control.py` | Home Assistant MQTT auto-discovery + command handling (used by `kiosk.py`). |
| `rack_kiosk.py` | The Grafana "Rack Kiosk" dashboard replica (one of the apps; also runnable standalone). Queries Prometheus via the Grafana datasource proxy. |
| `stream_clock.py` | Live, on-the-fly H.264 stream of the current time (incl. milliseconds). |
| `hello_world.py`, `show.py`, `random_graphs.py`, `animate.py`, `make_video.py`, `play_video.py`, `brightness_sweep.py`, `recover.py` | Capability demos (static images, animation, native H.264 video playback, brightness, USB recovery). Some hardcode macOS font paths. |

### Display facts / gotchas
- **Only the full-compression PNG path renders reliably** (`DisplayPILImage`, cmd 102). JPEG upload (cmd 101) and low-compression PNG are silently dropped → screen reverts to its idle wallpaper.
- **Motion:** PNG frame-push ≈ 4 fps; native **H.264 video** ≈ 25 fps and is the path for smooth/live motion (see `play_video.py`, `stream_clock.py`).
- **Never kill mid-USB-transfer** — it desyncs the firmware and corrupts the display. Stop gracefully; `recover.py` does a USB reset if needed.
- macOS (Apple Silicon): run with `DYLD_LIBRARY_PATH=/opt/homebrew/lib`. Linux: install `libusb-1.0-0`; run as root or add a udev rule for `1cbe:*`.

## Setup

```bash
./setup.sh                       # apt deps + clone lib/ + venv + pip install
echo -n '<grafana-token>' > .grafana_token && chmod 600 .grafana_token
./venv/bin/python rack_kiosk.py --once --save=/tmp/test.png   # render without the device
```

### rack_kiosk.py
Faithful, paged copy of the Grafana **Rack Kiosk** dashboard (uid `adg8v6n`):
- Queries Prometheus through the Grafana **datasource proxy** (`/api/datasources/proxy/uid/prometheus/...`) with a service-account token (`GRAFANA_TOKEN` env or `.grafana_token`).
- 3 auto-cycling pages — Power & WAN, Temps & Outlets, Infra & Compute — preserving Grafana units, threshold colors, value mappings, and the two bar gauges.
- Args: `--secs=N` (seconds/page), `--page=N` (pin one page), `--save=FILE --once` (render a PNG to inspect).

## Deploy on a headless Raspberry Pi

```bash
ssh kiosk
git clone https://github.com/nutgood/turzx.git ~/turzx && cd ~/turzx
./setup.sh
echo -n '<grafana-token>' > .grafana_token && chmod 600 .grafana_token
sudo cp deploy/rack-kiosk.service /etc/systemd/system/
sudo systemctl enable --now rack-kiosk
```

The service runs headless (no X/Chromium) and drives the USB display directly. It runs
`kiosk.py`, reading optional MQTT/display settings from `kiosk.env` (see `kiosk.env.example`).

## Apps & orchestrator (`kiosk.py`)

`kiosk.py` cycles through **apps**, each with one or more **pages**. Built-in apps:
- **Rack Kiosk** — the Grafana dashboard (3 pages)
- **Clock** — large clock + date
- **Pi Stats** — the host's CPU temp / load / memory / uptime

Cycling model (page-cycling and app-cycling are **independent**, each with its own toggle + interval):
- **Auto-cycle pages** rotates pages *within* the current app every *page interval*.
- **Auto-cycle apps** advances to the next app every *app interval*.
- Each app also has its own **`refresh`** cadence — how often its current page re-renders
  (Clock = 1s so seconds tick; dashboard/stats = 2s). Independent of the cycle timers.
- **Options set via MQTT persist** to `state.json` and restore on boot (writes only on
  change, never on auto-cycle ticks). MQTT host/creds/defaults come from `kiosk.env`.

### Building a new app

An app is any object with this contract; drop it in a module and register it.

```python
# myapp.py
from rack_kiosk import W, H, font, COL, hexrgb, draw_centered, BG  # shared render helpers

class MyApp:
    name = "My App"     # unique; appears in the HA "App" select + persisted by name/index
    n_pages = 2         # number of pages
    refresh = 5.0       # seconds between re-renders of the current page

    def update(self):           # optional: fetch data; called before each render
        ...
    def render(self, page):     # REQUIRED: return a 1920x480 RGB PIL.Image for this page
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        draw_centered(d, W/2, H/2, f"page {page+1}", font(120), hexrgb(COL["text"]))
        return img
```

Register it in `kiosk.py` → `main()`:
```python
from myapp import MyApp
apps = [RackApp(), ClockApp(), PiStatsApp(), MyApp()]
```

Shared helpers in `rack_kiosk.py`: `W,H` (1920,480), `font(px)`, `COL` (Grafana palette),
`hexrgb`, `th_color(steps, value)` (threshold→color), `fmt(unit, value)` (watt/percent/bps/…),
`draw_centered`, and `BG/TILE/TILE_BD/TITLE_C` colors. For Grafana data, reuse
`rack_kiosk.fetch_all()` / the datasource-proxy `query(expr)`.

Tips: only the full-frame PNG path renders, so always return a full 1920×480 image.
Iterate offline without the device:
```python
MyApp().render(0).save("/tmp/p.png")   # then open /tmp/p.png
```

### Home Assistant (MQTT auto-discovery)
Set `MQTT_HOST` (+ `MQTT_USER`/`MQTT_PASS`) in `kiosk.env`. On start the kiosk publishes
retained MQTT discovery configs, so HA auto-creates a **TURZX Kiosk** device with:
- `switch` **Display**, `select` **App**, `sensor` **Current page**
- `button` **Next app / Previous app**, `button` **Next page / Previous page**
- `switch` **Auto-cycle pages**, `switch` **Auto-cycle apps**
- `number` **Page interval**, `number` **App interval**, `number` **Brightness**
- Alert: `text` **Alert message**, `number` **Alert timeout**, `button` **Send alert**,
  `binary_sensor` **Alert active**, and a `notify` action **`notify.turzx_kiosk_alert`**

Renaming/removing entities? Add the old `(component, object_id)` to `LEGACY` in
`mqtt_control.py` — retained discovery configs are cleared on connect so HA drops stale duplicates.

### Alert service
Instantly interrupts the display with a message for a configurable timeout, then resumes
(pressing Next/Prev app/page dismisses it immediately). Properties:
`message` (required), `title`, `timeout`, `level` (info|warning|critical|success),
`icon` (warning|info|error|success|none), `color`, `accent`, `text_color`, `blink`, `size`.

Trigger options:
- **`notify.turzx_kiosk_alert`** (auto-discovered, message-only, no YAML).
- **`script.kiosk_alert`** — rich action with all properties as fields; install `deploy/ha-kiosk-alert.yaml`.
- Raw MQTT (automations): publish JSON (or plain text) to `turzx/kiosk/cmd/alert`:
  ```yaml
  service: mqtt.publish
  data:
    topic: turzx/kiosk/cmd/alert
    payload: '{"message": "UPS on battery — power lost", "level": "critical", "timeout": 30}'
  ```

MQTT layout: base `turzx/kiosk`; commands under `turzx/kiosk/cmd/<entity>`; retained state JSON at `turzx/kiosk/state`; availability at `turzx/kiosk/availability`.

### Deploying changes to the Pi
```bash
rsync -az --exclude venv --exclude lib --exclude .git --exclude kiosk.env --exclude state.json ./ kiosk:turzx/
ssh kiosk 'sudo systemctl restart rack-kiosk'   # service name is rack-kiosk; runs kiosk.py
```
