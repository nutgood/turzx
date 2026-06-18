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
| `rack_kiosk.py` | **Main app** — replica of the Grafana "Rack Kiosk" dashboard, auto-cycling pages. Queries Prometheus via the Grafana datasource proxy and renders to the display. |
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

The service runs headless (no X/Chromium) and drives the USB display directly.
