"""Cameras app — live multi-camera wall via the native H.264 video path.

Composites N RTSP(S) cameras side-by-side into one 1920x480 frame with a single ffmpeg
(HW/SW HEVC decode → scale/crop → hstack → H.264), and streams it to the display's decoder.
This is a *streaming app*: it implements ``stream()`` and drives the display directly while
active (the orchestrator hands it the device and a stop predicate).

Config: ``cameras.json`` at the repo root (gitignored; see cameras.example.json):
    { "fps": 20, "hwaccel": "", "transpose": 1, "labels": true,
      "cameras": [ {"name": "...", "url": "rtsps://...", "width": 360}, ... ] }
Tile ``width`` values should sum to 1920; each tile is cover-scaled + center-cropped.
"""
import json
import os
import select
import subprocess
import sys
import time

from .. import ROOT, h264
from ..render import COL, H, W, _FONT, blank, draw_centered, font, hexrgb
from .base import App

CONFIG = os.path.join(ROOT, "cameras.json")


class CamerasApp(App):
    name = "Cameras"
    n_pages = 1
    refresh = 5.0          # only used by the placeholder render()

    def __init__(self, cfg):
        self.cfg = cfg

    @classmethod
    def maybe(cls):
        """Return a CamerasApp if cameras.json exists and is valid, else None."""
        if not os.path.exists(CONFIG):
            return None
        try:
            cfg = json.load(open(CONFIG))
            if cfg.get("cameras"):
                return cls(cfg)
        except Exception as e:
            print(f"cameras config error: {e}", file=sys.stderr)
        return None

    def _ffmpeg_cmd(self, fps):
        cams = self.cfg["cameras"]
        hw = self.cfg.get("hwaccel", "")          # "" = software, "drm" = Pi5 HW HEVC
        transpose = self.cfg.get("transpose", 1)  # 1 = 90° CW (landscape→portrait, upright)
        labels = self.cfg.get("labels", True) and _FONT
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay"]
        for c in cams:
            if hw:
                cmd += ["-hwaccel", hw]
            cmd += ["-rtsp_transport", "tcp", "-i", c["url"]]
        chains, tiles = [], []
        for i, c in enumerate(cams):
            w = c["width"]
            f = (f"[{i}:v]scale={w}:{H}:force_original_aspect_ratio=increase,"
                 f"crop={w}:{H},setsar=1")
            if labels:
                name = str(c.get("name", "")).replace(":", " ").replace("'", "")
                f += (f",drawtext=fontfile='{_FONT}':text='{name}':x=8:y=8:fontsize=20:"
                      f"fontcolor=white:borderw=2:bordercolor=black@0.7")
            f += f"[v{i}]"
            chains.append(f)
            tiles.append(f"[v{i}]")
        fc = ";".join(chains) + ";" + "".join(tiles) + \
            f"hstack=inputs={len(cams)},transpose={transpose},format=yuv420p[v]"
        cmd += ["-filter_complex", fc, "-map", "[v]", "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-g", str(fps), "-bf", "0",
                "-x264-params", "repeat-headers=1", "-r", str(fps), "-f", "h264", "-"]
        return cmd

    def stream(self, lcd, should_stop, brightness=70):
        """Run the composite pipeline and feed the display until should_stop() is true."""
        dev = lcd.dev
        fps = int(self.cfg.get("fps", 20))
        # Show a clean placeholder while RTSP connects + cameras deliver their first keyframes
        # (~1-2s) instead of leaving the display in a blank/gray video buffer.
        try:
            lcd.DisplayPILImage(self.render(0))
        except Exception:
            pass
        ff = subprocess.Popen(self._ffmpeg_cmd(fps), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out_fd = ff.stdout.fileno()
        last_status = 0.0
        try:
            # Wait for ffmpeg's first output (begins with SPS/PPS + IDR) BEFORE switching the
            # device into video mode — so the decoder's first input is a keyframe, not garbage.
            first = b""
            while not should_stop() and not first:
                r, _, _ = select.select([out_fd], [], [], 0.3)
                if r:
                    first = os.read(out_fd, 1 << 18)
                    if not first:
                        return                                   # ffmpeg exited before any output
            if should_stop():
                return
            chunk = h264.preamble(dev, brightness=brightness, fps=fps)
            h264.send_chunk(dev, first)
            while not should_stop():
                r, _, _ = select.select([out_fd], [], [], 0.3)   # wake to re-check stop even if stalled
                if not r:
                    continue
                data = os.read(out_fd, chunk)
                if not data:
                    break                                        # ffmpeg exited (stream error)
                h264.send_chunk(dev, data)
                now = time.time()
                if now - last_status > 0.25:                     # flow control
                    last_status = now
                    if h264.queue_depth(dev) > 3:
                        time.sleep(0.02)
        finally:
            try:
                ff.terminate()
                ff.wait(timeout=2)
            except Exception:
                try:
                    ff.kill()
                except Exception:
                    pass
            h264.stop(dev)

    def render(self, page):
        img, d = blank()
        draw_centered(d, W / 2, H / 2, "Cameras — connecting…", font(60), hexrgb(COL["text"]))
        return img
