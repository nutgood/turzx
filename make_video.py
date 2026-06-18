#!/usr/bin/env python3
"""Render a demo animation to an H.264 MP4 sized for the Turing 8.8" panel.

Frames are designed in LANDSCAPE (1920x480) then rotated 270 to the panel's native
480x1920 portrait (same convention DisplayPILImage uses), and piped to ffmpeg as a
device-friendly H.264 (baseline / yuv420p) MP4.
"""
import math
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1920, 480          # landscape design size
FPS = 25
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
OUT = sys.argv[1] if len(sys.argv) > 1 else "demo.mp4"

FONT = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 150)
WAVES = [("#4cc9f0", 1.0, 1.0), ("#f72585", 1.7, 0.65), ("#b5e48c", 0.55, 1.35)]


def make_frame(t):
    # shifting vertical gradient background
    grad = np.zeros((H, W, 3), dtype=np.uint8)
    col = np.linspace(0, 1, W)
    r = (20 + 30 * np.sin(col * 6 + t * 1.5)).clip(0, 255)
    g = (15 + 25 * np.sin(col * 4 - t)).clip(0, 255)
    b = (60 + 60 * np.sin(col * 3 + t * 0.7)).clip(0, 255)
    grad[:] = np.dstack([np.tile(r, (H, 1)), np.tile(g, (H, 1)), np.tile(b, (H, 1))]).astype(np.uint8)
    img = Image.fromarray(grad, "RGB")
    d = ImageDraw.Draw(img)

    xs = np.arange(0, W, 2)
    mid = H / 2
    for color, freq, amp in WAVES:
        ys = mid + np.sin(xs * freq * 2 * math.pi / W * 3 + t * 3) * (H * 0.34 * amp) \
            * np.sin(xs * 2 * math.pi / W + t)
        d.line(list(zip(xs.tolist(), ys.tolist())), fill=color, width=4)

    text = "TURING 8.8\""
    bb = d.textbbox((0, 0), text, font=FONT)
    d.text(((W - (bb[2] - bb[0])) // 2 - bb[0], (H - (bb[3] - bb[1])) // 2 - bb[1]),
           text, font=FONT, fill=(255, 255, 255))
    return img


def main():
    nframes = int(FPS * SECONDS)
    ff = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{H}x{W}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.1",
         "-pix_fmt", "yuv420p", "-g", "12", "-bf", "0", OUT],
        stdin=subprocess.PIPE)
    for i in range(nframes):
        t = i / FPS
        frame = make_frame(t).transpose(Image.Transpose.ROTATE_270)  # -> 480x1920 portrait
        ff.stdin.write(frame.tobytes())
    ff.stdin.close()
    ff.wait()
    print(f"wrote {OUT}: {nframes} frames @ {FPS}fps, {H}x{W} portrait H.264")


if __name__ == "__main__":
    main()
