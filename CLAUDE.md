# Turing 8.8" Smart Screen — project notes

Driving a physical **Turing 8.8" smart screen** connected to this Mac (Apple Silicon).

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

## Scripts

- `hello_world.py` — minimal static "Hello World".
- `show.py "TEXT" [landscape|reverse]` — static test frame with orientation markers.
- `random_graphs.py [landscape|reverse]` — matplotlib assortment of random charts.
- `animate.py [seconds|loop] ` — live oscilloscope (frame-by-frame PNG push, ~4.3 fps, graceful stop).
- `recover.py` — USB reset + clean frame after corruption.
- `make_video.py [out.mp4] [seconds]` — render a demo animation to a device-ready H.264 MP4.
- `play_video.py [file.mp4] [loop]` — stream a pre-encoded MP4 via the native video path.
- `stream_clock.py [seconds]` — LIVE on-the-fly H.264 stream of the current time incl. milliseconds (no arg = non-stop).
- `rack_kiosk.py` / `run-kiosk.sh` — replica of the Grafana "Rack Kiosk" dashboard (see below).

## Grafana "Rack Kiosk" dashboard replica (`rack_kiosk.py`)

Faithful on-device copy of the Grafana **Rack Kiosk** dashboard (uid `adg8v6n`), refreshing every 5s via the PNG path.
- **Data access:** queries Prometheus through the **Grafana datasource proxy** — `GET {GRAFANA_URL}/api/datasources/proxy/uid/prometheus/api/v1/query` with `Authorization: Bearer <token>`. No need to expose Prometheus directly over Tailscale; the proxy covers all panels. (Grafana itself: `https://htalos-grafana.feist-boa.ts.net`, behind Tailscale; the API needs a token — `/api/health` is the only unauth endpoint.)
- **Token:** read from `GRAFANA_TOKEN` env or the local `.grafana_token` file (chmod 600). It's a Grafana service-account token (`sa-1-claude`). Rotate in Grafana if leaked.
- **Layout trick:** the Grafana grid is 24 cols × 28 row-units, which maps almost exactly onto 1920×480 (80px/col, ~17px/row-unit) — so the replica uses the dashboard's real `gridPos` and preserves the exact layout instead of re-flowing.
- **Fidelity:** per-panel `unit` formatting (watt/percent/bps/celsius/seconds), `thresholds` → value colors (Grafana dark palette), value `mappings` (WAN UP/DOWN), and the two `bargauge` panels (rack temps, top outlets). Scalar-type PromQL results (e.g. `scalar()/scalar()`) need special handling — `data.result` is `[ts, val]`, not a series list.
- **Paged:** auto-cycles 3 pages (Power & WAN / Temps & Outlets / Infra & Compute) with bigger tiles + a footer (page name, clock, page dots). `PANELS` is the data registry (by title); `PAGES` defines per-page layout on a 24-col×12-row grid. Args: `--secs=N` (seconds/page, default 7), `--page=N` (pin one page), `--save=FILE --once`/`--page=N` (render a PNG to inspect without the device — Read it directly for fast iteration).
- **Run:** `./run-kiosk.sh [--secs=N]` (managed background task). The dashboard JSON is cached at `rackkiosk.json`; re-fetch with the token if panels change.
- Source of homelab config: `../homelab-flux` (Flux GitOps, kube-prometheus-stack; dashboards live in Grafana's DB, not Git).

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
