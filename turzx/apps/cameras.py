"""Cameras app — live multi-camera wall via the native H.264 video path.

Composites N RTSP(S) cameras side-by-side into one 1920x480 frame with a single ffmpeg
(HEVC decode → scale/crop → hstack → drawtext overlays → H.264) and streams it to the
display. A *streaming app*: it implements ``stream()`` and drives the display directly.

Keep-warm: when enabled the ffmpeg pipeline keeps running in the background (a reader thread
drains it), so switching to Cameras is instant — no RTSP reconnect / keyframe wait.

Overlay: three drawtext layers (top/middle/bottom) read reloadable text files, so a banner
can be shown over the live video without restarting ffmpeg (used by the camera-alert action).

Config: ``cameras.json`` (gitignored; see cameras.example.json):
    { "fps": 20, "hwaccel": "", "transpose": 1, "labels": true,
      "cameras": [ {"name": "...", "url": "rtsps://...", "width": 360}, ... ] }
"""
import json
import os
import select
import subprocess
import sys
import tempfile
import threading
import time

from .. import ROOT, h264
from ..render import COL, H, W, _FONT, blank, draw_centered, font, hexrgb
from .base import App

CONFIG = os.path.join(ROOT, "cameras.json")
_OVL_DIR = tempfile.gettempdir()
POSITIONS = ("top", "middle", "bottom")


class CamerasApp(App):
    name = "Cameras"
    n_pages = 1
    refresh = 5.0

    def __init__(self, cfg):
        self.cfg = cfg
        self.fps = int(cfg.get("fps", 20))
        self._lock = threading.Lock()
        self._proc = None            # warm background ffmpeg (or None)
        self._reader = None
        self._stop_reader = threading.Event()
        self._sink = None            # {"dev": dev} when forwarding to the display, else None
        self._ovl = {p: os.path.join(_OVL_DIR, f"turzx_cam_{p}.txt") for p in POSITIONS}
        self._ensure_overlay_files()

    @classmethod
    def maybe(cls):
        if not os.path.exists(CONFIG):
            return None
        try:
            cfg = json.load(open(CONFIG))
            if cfg.get("cameras"):
                return cls(cfg)
        except Exception as e:
            print(f"cameras config error: {e}", file=sys.stderr)
        return None

    # ---------- overlay text files (read by ffmpeg drawtext reload=1) ----------
    def _ensure_overlay_files(self):
        for f in self._ovl.values():
            if not os.path.exists(f):
                open(f, "w").close()

    def _write_overlay(self, position, text):
        for p in POSITIONS:
            try:
                with open(self._ovl[p], "w") as fh:
                    fh.write(text if p == position else "")
            except Exception:
                pass

    def _clear_overlay(self):
        self._write_overlay(None, "")

    def _apply_overlay(self, state):
        """Drive the banner from state.cam_alert_* (set by the camera-alert action)."""
        if state is None:
            return
        msg = getattr(state, "cam_alert_msg", "")
        until = getattr(state, "cam_alert_until", 0.0)
        pos = getattr(state, "cam_alert_pos", "top")
        self._write_overlay(pos if (msg and until > time.time()) else None, msg or "")

    # ---------- ffmpeg ----------
    def _ffmpeg_cmd(self):
        cams = self.cfg["cameras"]
        hw = self.cfg.get("hwaccel", "")
        transpose = self.cfg.get("transpose", 1)
        labels = self.cfg.get("labels", True) and _FONT
        gop = max(5, self.fps // 4)        # short GOP → fast decoder re-sync on warm switch
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-fflags", "nobuffer", "-flags", "low_delay"]
        for c in cams:
            if hw:
                cmd += ["-hwaccel", hw]
            cmd += ["-rtsp_transport", "tcp", "-i", c["url"]]
        chains, tiles = [], []
        for i, c in enumerate(cams):
            w = c["width"]
            f = f"[{i}:v]scale={w}:{H}:force_original_aspect_ratio=increase,crop={w}:{H},setsar=1"
            if labels:
                name = str(c.get("name", "")).replace(":", " ").replace("'", "")
                f += (f",drawtext=fontfile='{_FONT}':text='{name}':x=8:y=8:fontsize=20:"
                      f"fontcolor=white:borderw=2:bordercolor=black@0.7")
            f += f"[v{i}]"
            chains.append(f)
            tiles.append(f"[v{i}]")
        overlays = ""
        if _FONT:
            ypos = {"top": "20", "middle": "(h-text_h)/2", "bottom": "h-text_h-20"}
            for p in POSITIONS:
                overlays += (f",drawtext=fontfile='{_FONT}':textfile='{self._ovl[p]}':reload=1:"
                             f"x=(w-text_w)/2:y={ypos[p]}:fontsize=46:fontcolor=white:"
                             f"box=1:boxcolor=black@0.55:boxborderw=16")
        fc = (";".join(chains) + ";" + "".join(tiles)
              + f"hstack=inputs={len(cams)}{overlays},transpose={transpose},format=yuv420p[v]")
        cmd += ["-filter_complex", fc, "-map", "[v]", "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
                "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-g", str(gop),
                "-keyint_min", str(gop), "-bf", "0", "-x264-params", "repeat-headers=1",
                "-r", str(self.fps), "-f", "h264", "-"]
        return cmd

    # ---------- keep-warm lifecycle ----------
    def ensure_warm(self, want):
        """Start/stop the background ffmpeg to match the keep-warm toggle."""
        with self._lock:
            running = self._proc is not None and self._proc.poll() is None
            if want and not running:
                self._start_proc()
            elif not want and running and self._sink is None:
                self._stop_proc()

    def _start_proc(self):
        self._ensure_overlay_files()
        self._clear_overlay()
        self._stop_reader.clear()
        self._proc = subprocess.Popen(self._ffmpeg_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self._reader = threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True)
        self._reader.start()

    def _stop_proc(self):
        self._stop_reader.set()
        p, self._proc, self._reader = self._proc, None, None
        if p:
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _read_loop(self, proc):
        fd = proc.stdout
        while not self._stop_reader.is_set() and proc.poll() is None:
            data = fd.read(65536)
            if not data:
                break
            with self._lock:
                sink = self._sink
            if sink is not None:
                h264.send_chunk(sink["dev"], data)

    # ---------- streaming ----------
    def stream(self, lcd, should_stop, brightness=70, state=None):
        dev = lcd.dev
        with self._lock:
            warm = self._proc is not None and self._proc.poll() is None
        if warm:
            self._stream_warm(dev, should_stop, brightness, state)
        else:
            self._stream_cold(dev, should_stop, brightness, state)

    def _stream_warm(self, dev, should_stop, brightness, state):
        h264.preamble(dev, brightness=brightness, fps=self.fps)
        with self._lock:
            self._sink = {"dev": dev}               # reader thread now forwards to the display
        try:
            while not should_stop():
                self._apply_overlay(state)
                time.sleep(0.1)
        finally:
            with self._lock:
                self._sink = None
            self._clear_overlay()
            h264.stop(dev)                          # ffmpeg stays warm

    def _stream_cold(self, dev, should_stop, brightness, state):
        self._ensure_overlay_files()
        self._clear_overlay()
        ff = subprocess.Popen(self._ffmpeg_cmd(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        out_fd = ff.stdout.fileno()
        chunk = h264.preamble(dev, brightness=brightness, fps=self.fps)
        last_status = 0.0
        try:
            while not should_stop():
                self._apply_overlay(state)
                r, _, _ = select.select([out_fd], [], [], 0.2)
                if not r:
                    continue
                data = os.read(out_fd, chunk)
                if not data:
                    break
                h264.send_chunk(dev, data)
                now = time.time()
                if now - last_status > 0.25:
                    last_status = now
                    if h264.queue_depth(dev) > 3:
                        time.sleep(0.02)
        finally:
            self._clear_overlay()
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
