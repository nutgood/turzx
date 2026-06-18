#!/usr/bin/env python3
"""Live animation via frame-by-frame USB image push (real-time generated).

Draws a scrolling multi-wave oscilloscope with PIL (fast) and pushes each frame
to the Turing 8.8" screen, then reports the achieved frame rate.
"""
import math
import os
import signal
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB

BG = (8, 12, 28)
WAVES = [("#4cc9f0", 1.0, 1.0), ("#f72585", 1.7, 0.6), ("#b5e48c", 0.5, 1.4)]


def frame(w, h, t, xs):
    img = Image.new("RGB", (w, h), BG)
    d = ImageDraw.Draw(img)
    # grid
    for gx in range(0, w, w // 16):
        d.line([(gx, 0), (gx, h)], fill=(20, 28, 50))
    for gy in range(0, h, h // 4):
        d.line([(0, gy), (w, gy)], fill=(20, 28, 50))
    mid = h / 2
    for color, freq, amp in WAVES:
        ys = mid + np.sin(xs * freq * 2 * math.pi / w * 3 + t * 3) * (h * 0.32 * amp) \
            * np.sin(xs * 2 * math.pi / w + t)
        d.line(list(zip(xs.tolist(), ys.tolist())), fill=color, width=3)
    return img


def main():
    # Duration in seconds; <= 0 (or "inf") means loop forever until Ctrl-C.
    arg = sys.argv[1] if len(sys.argv) > 1 else "0"
    duration = float("inf") if arg in ("0", "inf", "loop") else float(arg)
    # NOTE: only the driver's full-compression PNG path (cmd 102) renders reliably on this
    # 8.8" firmware. The JPEG path (cmd 101) and low-compression PNGs are silently dropped
    # (screen reverts to idle wallpaper -> looks like "corruption"). So we always use DisplayPILImage.
    lcd = LcdCommTuringUSB(com_port="AUTO")
    lcd.InitializeComm()
    lcd.SetOrientation(Orientation.LANDSCAPE)
    lcd.SetBrightness(60)

    w, h = lcd.get_width(), lcd.get_height()
    xs = np.arange(0, w, 2)  # every 2px -> lighter polyline
    forever = duration == float("inf")
    print(f"Animating {w}x{h} {'forever (Ctrl-C to stop)' if forever else f'for {duration:.0f}s'} ...")

    # Graceful stop: finish the current frame (don't cut a USB bulk transfer in half,
    # which desyncs the command stream and corrupts the display).
    stopping = {"flag": False}
    signal.signal(signal.SIGTERM, lambda *_: stopping.__setitem__("flag", True))
    signal.signal(signal.SIGINT, lambda *_: stopping.__setitem__("flag", True))

    n = 0
    start = time.time()
    try:
        while not stopping["flag"] and time.time() - start < duration:
            lcd.DisplayPILImage(frame(w, h, time.time() - start, xs))  # reliable cmd-102 PNG path
            n += 1
            if forever and n % 50 == 0:
                el = time.time() - start
                print(f"  {n} frames, {n / el:.1f} fps", flush=True)
    except KeyboardInterrupt:
        pass
    lcd.InitializeComm()  # final sync -> leave device in a clean, idle state
    elapsed = time.time() - start
    print(f"Pushed {n} frames in {elapsed:.2f}s -> {n / elapsed:.1f} fps (stopped cleanly)")


if __name__ == "__main__":
    main()
