# turzx

Tooling to drive a **Turing / TURZX USB smart display** (the new-generation, vendor-USB
protocol models — e.g. the 8.8" 480×1920 bar screen, VID `0x1cbe`) from Python, plus a
faithful on-device replica of a Grafana dashboard ("Rack Kiosk").

Built and verified against the **Turing 8.8"** (`1cbe:0088`, native 480×1920, viewed in
landscape 1920×480). These models are **not** serial devices — they speak a vendor-specific
USB protocol (libusb bulk transfers, DES-encrypted command packets), driven via the
`LcdCommTuringUSB` class from [mathoudebine/turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(cloned into `lib/` by `setup.sh`).

## Layout

```
turzx/              # the package — run with `python -m turzx`
  orchestrator.py     main loop (cycles apps/pages, alert overlay, display on/off)
  state.py            control state + persistence (state.json)
  apps/               one module per app: rack.py, clock.py, pistats.py (+ base.py, __init__.py)
  render.py           shared render helpers (fonts, colors, fmt, th_color, draw_centered, W/H)
  grafana.py          Prometheus-via-Grafana-proxy client
  alert.py            configurable alert overlay rendering
  mqtt.py             Home Assistant MQTT auto-discovery + commands
  device.py           opens the USB display (wraps vendored lib/)
tools/              # standalone demos/utilities (not part of the kiosk)
  render_app.py       render any app/page to a PNG (offline iteration)
  hello_world.py show.py animate.py random_graphs.py make_video.py play_video.py
  stream_clock.py brightness_sweep.py recover.py
deploy/            # turzx-kiosk.service, ha-kiosk-alert.yaml, kiosk.env.example
lib/               # vendored upstream driver (gitignored; cloned by setup.sh)
```

### Display facts / gotchas
- **Only the full-compression PNG path renders reliably** (`DisplayPILImage`, cmd 102). JPEG upload (cmd 101) and low-compression PNG are silently dropped → screen reverts to its idle wallpaper.
- **Motion:** PNG frame-push ≈ 4 fps; native **H.264 video** ≈ 25 fps and is the path for smooth/live motion (see `play_video.py`, `stream_clock.py`).
- **Never kill mid-USB-transfer** — it desyncs the firmware and corrupts the display. Stop gracefully; `recover.py` does a USB reset if needed.
- macOS (Apple Silicon): run with `DYLD_LIBRARY_PATH=/opt/homebrew/lib`. Linux: install `libusb-1.0-0`; run as root or add a udev rule for `1cbe:*`.

## Setup

```bash
./setup.sh                       # apt deps + clone lib/ + venv + pip install
echo -n '<grafana-token>' > .grafana_token && chmod 600 .grafana_token
./venv/bin/python tools/render_app.py "Rack Kiosk" 0 /tmp/test.png   # render without the device
./venv/bin/python -m turzx       # run against the display
```

The **Rack Kiosk** app is a faithful, paged copy of the Grafana dashboard (uid `adg8v6n`):
queries Prometheus through the Grafana **datasource proxy** with a service-account token
(`GRAFANA_TOKEN` env or `.grafana_token`); 3 pages (Power & WAN, Temps & Outlets, Infra &
Compute) preserving Grafana units, threshold colors, value mappings, and the two bar gauges.

## Deploy on a headless Raspberry Pi

```bash
ssh kiosk
git clone https://github.com/nutgood/turzx.git ~/turzx && cd ~/turzx
./setup.sh
echo -n '<grafana-token>' > .grafana_token && chmod 600 .grafana_token
cp deploy/kiosk.env.example kiosk.env   # then edit MQTT host/creds; chmod 600
sudo cp deploy/turzx-kiosk.service /etc/systemd/system/
sudo systemctl enable --now turzx-kiosk
```

The service runs headless (no X/Chromium) and drives the USB display directly via
`python -m turzx`, reading optional MQTT/display settings from `kiosk.env`.

## Apps & orchestrator

`python -m turzx` cycles through **apps**, each with one or more **pages**. Built-in apps:
- **Cameras** — live multi-camera wall (only if `cameras.json` is present; see below)
- **Rack Kiosk** — the Grafana dashboard (3 pages)
- **Clock** — large clock + date
- **Pi Stats** — the host's CPU temp / load / memory / uptime

### Cameras app (multi-camera video wall)
Composites N RTSP(S) cameras side-by-side into 1920×480 and streams them via the display's
**native H.264 path** (a *streaming app* — it drives the display directly while active; the
PNG path can't, since photographic frames exceed the 1MB payload limit). Configure via
`cameras.json` (gitignored; copy `cameras.example.json`):
```json
{ "fps": 20, "hwaccel": "", "transpose": 1, "labels": true,
  "cameras": [ {"name": "Doorbell", "url": "rtsps://…?enableSrtp", "width": 360}, … ] }
```
- Tile `width`s should sum to 1920; each tile is cover-scaled + center-cropped.
- **Use low-resolution substreams** — the tiles are tiny (≤780px). 4K streams overwhelm the
  Pi's decoder; the camera's lowest RTSP quality (e.g. 640×360) is plenty and ~free on CPU.
- `transpose`: `1` (90° CW) is upright for the normal mounting; set `3` if it's upside-down.
- `hwaccel`: `""` (software, steadiest at low res) or `"drm"` (Pi 5 HW HEVC).
- It interrupts instantly for alerts / navigation; for continuous viewing, pin it (select
  **Cameras** + turn **Auto-cycle apps** off, else it reconnects each rotation).

Cycling model (page-cycling and app-cycling are **independent**, each with its own toggle + interval):
- **Auto-cycle pages** rotates pages *within* the current app every *page interval*.
- **Auto-cycle apps** advances to the next app every *app interval*.
- Each app also has its own **`refresh`** cadence — how often its current page re-renders
  (Clock = 1s so seconds tick; dashboard/stats = 2s). Independent of the cycle timers.
- **Options set via MQTT persist** to `state.json` and restore on boot (writes only on
  change, never on auto-cycle ticks). MQTT host/creds/defaults come from `kiosk.env`.

### Building a new app

Add a module under `turzx/apps/` with a class implementing the `App` contract, then register it.

```python
# turzx/apps/myapp.py
from ..render import W, H, font, COL, hexrgb, draw_centered, blank
from .base import App

class MyApp(App):
    name = "My App"     # unique; appears in the HA "App" select + persisted by name/index
    n_pages = 2         # number of pages
    refresh = 5.0       # seconds between re-renders of the current page

    def update(self):           # optional: fetch data; called before each render
        ...
    def render(self, page):     # REQUIRED: return a 1920x480 RGB PIL.Image for this page
        img, d = blank()        # fresh image + ImageDraw on the dark background
        draw_centered(d, W/2, H/2, f"page {page+1}", font(120), hexrgb(COL["text"]))
        return img
```

Register it in `turzx/apps/__init__.py`:
```python
from .myapp import MyApp
def default_apps():
    return [RackApp(), ClockApp(), PiStatsApp(), MyApp()]
```

Shared helpers in `turzx/render.py`: `W,H` (1920,480), `font(px)`, `COL` (Grafana palette),
`hexrgb`, `th_color(steps, value)` (threshold→color), `fmt(unit, value)` (watt/percent/bps/…),
`draw_centered`, `panel(...)`, `blank()`, and `BG/TILE/TILE_BD/TITLE_C` colors. For Grafana
data, use `from ..grafana import query, query_many`.

Tips: only the full-frame PNG path renders, so always return a full 1920×480 image.
Iterate offline without the device:
```bash
./venv/bin/python tools/render_app.py "My App" 0 /tmp/p.png   # then open /tmp/p.png
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
`turzx/mqtt.py` — retained discovery configs are cleared on connect so HA drops stale duplicates.

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
ssh kiosk 'sudo systemctl restart turzx-kiosk'   # runs `python -m turzx`
```
