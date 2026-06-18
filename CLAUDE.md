# Turing 8.8" Smart Screen — project notes

Driving a physical **Turing 8.8" smart screen**. Developed on this Mac (Apple Silicon);
**deployed headless on a Raspberry Pi 5** (`ssh kiosk`, user `kiosk`) where it runs as the
`turzx-kiosk.service` systemd unit (runs `python -m turzx` as root for USB access). Repo is pushed to
`github.com/nutgood/turzx`. The Pi reaches Grafana/MQTT over Tailscale + LAN.
**To build/extend apps, see the "Building a new app" guide in README.md.**

## The hardware (verified by inspection, not docs)

- USB id: **VID `0x1cbe` / PID `0x0088`**, USB product string `TURZX1.0`, manufacturer `TURZX`.
- Native panel: **480×1920 portrait**. We mount/view it as a wide bar, so use **`Orientation.LANDSCAPE`** (1920×480) — verified upright via webcam (TOP-LEFT marker at top-left, up-arrow pointing up).
- It is the **new-generation TURZX USB-protocol** model: `bDeviceClass = 255` (vendor-specific). It is **NOT** a serial device — there is **no `/dev/cu.usbmodem` port**. Anything that drives it over pyserial (e.g. `zackpollard/turing-display-apps`, most Turing tooling, and GitHub issue #7 about Mac serial corruption) **does not apply to this screen.**
- Driven via **libusb bulk transfers** with **DES-encrypted** 512-byte command packets. Endpoints: IN `0x81`, OUT `0x01`, interface 0.
- The correct driver is **`library/lcd/lcd_comm_turing_usb.py` → class `LcdCommTuringUSB`** from `mathoudebine/turing-smart-screen-python` (cloned into `lib/`).

## Setup / how to run

- Library cloned to `lib/`. venv at `venv/` with: `pyusb pycryptodome Pillow pyserial numpy matplotlib`. System `libusb` from Homebrew (already installed), `ffmpeg` present.
- **Always run with the Homebrew lib path** (Apple Silicon needs it for libusb):
  ```
  DYLD_LIBRARY_PATH=/opt/homebrew/lib ./venv/bin/python <script>
  ```
- `lib/` must be on `sys.path` (scripts insert it). The base `lcd_comm.py` imports `serial` at top level, so `pyserial` must be installed even though this screen isn't serial.

## What renders and what DOESN'T (key gotcha — cost hours)

- ✅ **PNG upload via `DisplayPILImage` / `send_image` (cmd 102) with `compress_level=9`** renders perfectly. This is the ONLY reliable image path. Static frames AND frame-by-frame animation both work this way (~4.3 fps at full 1920×480).
- ❌ **JPEG upload (cmd 101, `send_jpeg`)**: USB write returns OK but **nothing displays** — screen reverts to idle wallpaper.
- ❌ **Low-compression PNG (`compress_level=1`)**: same failure — silently dropped, screen shows idle wallpaper. This looked like "corruption" but was actually dropped frames.
- The firmware **reverts to a built-in idle wallpaper** whenever no process is actively pushing accepted frames. Persistent content => keep a process running.
- **Diagnosing display issues requires the webcam** — USB writes return success even when nothing renders. Capture with:
  ```
  ffmpeg -hide_banner -loglevel error -f avfoundation -framerate 30 -i "0" -frames:v 40 -update 1 cam.jpg
  ```
  (device `0` = MacBook Pro Camera; grab ~40 frames so auto-exposure settles, keep last). Then Read `cam.jpg`.

## Stability / recovery

- **Never kill an animation mid-USB-transfer.** A `SIGKILL`/abrupt `pkill` during a bulk PNG write leaves the firmware waiting for the rest of the bytes → the command stream **desyncs** → real corruption. `animate.py` installs SIGTERM/SIGINT handlers that finish the current frame, then send a final sync.
- To recover a desynced/corrupted screen: **`recover.py`** does a `dev.reset()` (USB port reset clears the firmware's half-read buffer) + sync + Clear + clean frame. A physical replug also works (and re-enumerates).
- After a replug the device briefly disappears; re-check presence before launching.

## Package layout (`turzx/`) — the production entrypoint is `python -m turzx`

The code is a package: `orchestrator.py` (loop), `state.py` (State + persistence),
`render.py` (shared draw helpers), `grafana.py` (proxy client), `alert.py`, `mqtt.py`,
`device.py` (USB open/presence), and `apps/` (one module per app + `__init__.default_apps()`).
Standalone demos live in `tools/`. The vendored upstream driver is `lib/` (gitignored).

- **App contract:** `name` (unique str, shown in HA App select), `n_pages` (int), `refresh`
  (float secs between re-renders of the current page), `update()` (optional data fetch),
  `render(page) -> 1920×480 RGB PIL.Image`. Add a module under `turzx/apps/` and register in
  `turzx/apps/__init__.py` → `default_apps()`. Built-in: `RackApp`, `ClockApp`, `PiStatsApp`.
- **Render helpers live in `turzx/render.py`** (W/H, font, COL, th_color, fmt, draw_centered,
  panel, blank) — apps import from there, NOT from another app. Grafana data: `turzx/grafana.py`.
- **Display disconnect/reconnect is automatic:** the driver swallows USB write errors, so the
  loop polls `device.present()` every 2s; on absence (or a `USBError`) it disposes the handle and
  blocks in `_connect()` until the display is replugged, then re-opens. No service restart needed.
- **Two independent cycles:** page-cycle (within app) and app-cycle, each its own on/off toggle
  + interval, driven by separate timers in the loop. Per-app `refresh` re-renders the current
  page on its own cadence (Clock 1s, dashboard 2s) — that's why the loop's wait deadline is
  `min(page_due, app_due, render_due)`.
- **`State`** is the single source of truth (thread-safe). User/MQTT changes call `_notify()`
  (wake loop + publish + save); **auto-cycle advances (`adv_page`/`adv_app`) deliberately do NOT
  call `_notify`**, so persistence/ MQTT chatter don't fire on every tick.
- **Persistence:** `State.PERSIST` keys are written to `state.json` (atomic) on every `_notify`
  and restored on construction. Don't persist transient alert state. (SD-card-friendly: no
  per-tick writes.)
- **Alert overlay:** `fire_alert(props)` sets `alert_props` + `alert_until`; the loop renders it
  over everything until timeout. Props: message/title/timeout/level/color/accent/text_color/
  icon/blink/size (`render_alert`). Nav (`next/prev app/page`, `set_app`) clears the alert
  instantly via `_clear_alert_locked()`.

## Home Assistant / MQTT (`turzx/mqtt.py`)

- Started by the orchestrator if `MQTT_HOST` is set (from `kiosk.env`, an EnvironmentFile in the unit).
- Publishes retained HA **discovery** configs → auto-creates the "TURZX Kiosk" device. Entities
  are generated from `_entities(app_names)`; the App select options come from app names.
- **Gotcha (cost a round):** discovery configs are **retained**. Renaming/removing an entity
  leaves a stale retained config → duplicate HA entities. Fix: add the old `(component, object_id)`
  to `LEGACY`; `on_connect` publishes empty retained payloads to clear them.
- Control topics: `turzx/kiosk/cmd/<entity>`; retained state JSON `turzx/kiosk/state`;
  availability (LWT) `turzx/kiosk/availability`. The `cmd/alert` topic accepts JSON props or
  plain text. A `notify` discovery entity gives `notify.turzx_kiosk_alert` (HA has no discovery
  for a multi-field custom action — that's what `deploy/ha-kiosk-alert.yaml` / `script.kiosk_alert` is for).

## Deploy / RPi notes

- `setup.sh` is cross-platform (Linux: apt installs `python3-venv libusb-1.0-0 fonts-dejavu-core git ffmpeg`, clones `lib/`, builds venv). Fonts: code finds macOS Helvetica → Linux DejaVu (don't hardcode macOS paths in new apps; use `rack_kiosk.font`).
- Service is **`turzx-kiosk.service`** (ExecStart `python -m turzx`, WorkingDirectory `/home/kiosk/turzx`). Deploy = rsync (or git pull) + `sudo systemctl restart turzx-kiosk`. Secrets (`.grafana_token`, `kiosk.env`) live only on the Pi (gitignored); `state.json` is per-host (gitignored).
- The old display driver on the Pi was **grafana-kiosk** (Chromium via `~/.xinitrc`/startx on tty1) — removed (autostart neutralized in `~/.bash_profile`, backups `.bak`).
- macOS-Linux gotcha: `pkill -f <pattern>` matches your own SSH shell's argv — don't `pkill -f xinit` while a file named `.xinitrc` is in your command line.

## Production app & tools

- **`python -m turzx`** — the production orchestrator (apps + pages + MQTT + alerts + persistence + reconnect). What `turzx-kiosk.service` runs.
- `tools/render_app.py "<App>" [page] [out.png]` — render an app/page to PNG offline (no device).
- `tools/hello_world.py`, `tools/show.py "TEXT" [landscape|reverse]` — static frames.
- `tools/random_graphs.py [landscape|reverse]` — matplotlib charts.
- `tools/animate.py [seconds|loop]` — live oscilloscope (frame-by-frame PNG push, ~4.3 fps, graceful stop).
- `tools/recover.py` — USB reset + clean frame after corruption.
- `tools/make_video.py [out.mp4] [seconds]` / `tools/play_video.py [file.mp4] [loop]` — H.264 video.
- `tools/stream_clock.py [seconds]` — LIVE on-the-fly H.264 stream of the current time incl. milliseconds.
- (tools run on macOS with `DYLD_LIBRARY_PATH=/opt/homebrew/lib`.)

## Grafana "Rack Kiosk" dashboard app (`turzx/apps/rack.py`)

Faithful on-device copy of the Grafana **Rack Kiosk** dashboard (uid `adg8v6n`) — the `RackApp`.
- **Data access:** queries Prometheus through the **Grafana datasource proxy** — `GET {GRAFANA_URL}/api/datasources/proxy/uid/prometheus/api/v1/query` with `Authorization: Bearer <token>`. No need to expose Prometheus directly over Tailscale; the proxy covers all panels. (Grafana itself: `https://htalos-grafana.feist-boa.ts.net`, behind Tailscale; the API needs a token — `/api/health` is the only unauth endpoint.)
- **Token:** read from `GRAFANA_TOKEN` env or the local `.grafana_token` file (chmod 600). It's a Grafana service-account token (`sa-1-claude`). Rotate in Grafana if leaked.
- **Layout trick:** the Grafana grid is 24 cols × 28 row-units, which maps almost exactly onto 1920×480 (80px/col, ~17px/row-unit) — so the replica uses the dashboard's real `gridPos` and preserves the exact layout instead of re-flowing.
- **Fidelity:** per-panel `unit` formatting (watt/percent/bps/celsius/seconds), `thresholds` → value colors (Grafana dark palette), value `mappings` (WAN UP/DOWN), and the two `bargauge` panels (rack temps, top outlets). Scalar-type PromQL results (e.g. `scalar()/scalar()`) need special handling — `data.result` is `[ts, val]`, not a series list.
- **Paged:** 3 pages (Power & WAN / Temps & Outlets / Infra & Compute) + a footer (page name, clock, page dots). `PANELS` is the data registry (by title); `PAGES` defines per-page layout on a 24-col×12-row grid. Inspect offline: `tools/render_app.py "Rack Kiosk" <page> out.png`.
- Source of homelab config: `../homelab-flux` (Flux GitOps, kube-prometheus-stack; dashboards live in Grafana's DB, not Git). The dashboard JSON can be re-fetched from the Grafana API with the token.

## Video (H.264) — works, this is the path for smooth motion

Smooth full-motion (~25 fps) uses the firmware's **native H.264 decoder** via `send_video(dev, mp4_path, loop=)` (in `lcd_comm_turing_usb.py`). It auto-extracts Annex-B H.264 (uses `ffmpeg` if present; else a built-in MP4 parser), then streams chunks with built-in flow control. **Verified working** end-to-end.

Encoding that the decoder accepts (from `make_video.py`):
- **Resolution = panel-native portrait 480×1920.** Design content in landscape 1920×480, then `transpose(ROTATE_270)` → 480×1920 before encoding, so it appears **upright in landscape** (the video path does NOT auto-rotate, unlike `DisplayPILImage`).
- `libx264 -profile:v baseline -level 3.1 -pix_fmt yuv420p -g 12 -bf 0` (Constrained Baseline, no B-frames, frequent keyframes). H.265/HEVC is rejected — must be H.264.
- Pipe raw `rgb24` frames to ffmpeg stdin (`-f rawvideo -s 480x1920`).

**Clean stop matters here too:** `send_video` only sends `STOP_STREAM` on `KeyboardInterrupt`. `play_video.py` maps SIGTERM→KeyboardInterrupt so `kill -TERM` (and our `pkill`) stops the stream cleanly instead of desyncing. After stop, screen reverts to idle wallpaper (expected).

Decision rule: **live/dynamically-generated content → `animate.py` PNG push (~4 fps)**; **smooth pre-rendered motion → H.264 video (~25 fps)**.

### Live, on-the-fly H.264 streaming (`stream_clock.py`) — works, sub-second latency

You can generate a video stream in real time and feed the decoder continuously (verified: a live clock with milliseconds, display matched host wall-clock to <1s). Pattern:
- Run the same video preamble as `send_video` (cmds 111/112/13 → brightness → 41 → `clear_image` → `send_frame_rate` → `GET_H264_CHUNK_SIZE`), but then **drive `CMD_PLAY_H264_CHUNK` (121) yourself** instead of reading from a file.
- A **producer thread** renders frames (PIL, rotated ROTATE_270 → 480×1920) and writes raw `rgb24` to a **live ffmpeg encoder**: `libx264 -preset ultrafast -tune zerolatency -profile:v baseline -g <fps> -bf 0 -x264-params repeat-headers=1 -f h264 -`.
- The main loop does `os.read(ffmpeg.stdout, chunk_size)` (returns whatever's available → low latency) and sends each blob as a `cmd 121` chunk with length in bytes [8..11], **never** setting the last-flag [12] (so it streams forever). Poll `GET_STREAM_STATUS` (122) ~4×/s; back off if `resp[8] > 3`.
- Stop cleanly: SIGTERM → set stop event → send `CMD_STOP_STREAM` (123) + terminate ffmpeg. `repeat-headers=1` lets the decoder start/recover mid-stream.
- **Python is fast enough** for 20fps @ 480×1920 on this M-series Mac (encode+stream real-time); no need for Go.

## API quick reference (`LcdCommTuringUSB`)

```python
lcd = LcdCommTuringUSB(com_port="AUTO")   # auto-finds by VID/PID; sets w/h from PID map
lcd.InitializeComm()                       # sync handshake (cmd 10) — also use to leave device idle/clean
lcd.SetOrientation(Orientation.LANDSCAPE)  # 1920x480, upright for this mounting
lcd.SetBrightness(60)                      # 0..100
lcd.Clear()
lcd.DisplayPILImage(pil_img)               # full 1920x480 RGB image; the ONLY reliable render path
```
Orientation enum: PORTRAIT=0, REVERSE_PORTRAIT=1, LANDSCAPE=2, REVERSE_LANDSCAPE=3. Driver rotates the frame to portrait before sending (LANDSCAPE→ROTATE_270).
