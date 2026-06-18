#!/usr/bin/env python3
"""Display a frame and sweep brightness: hold high/mid/low, then a smooth triangle sweep."""
import os
import sys
import time

from PIL import Image, ImageDraw, ImageFont

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB

lcd = LcdCommTuringUSB(com_port="AUTO")
lcd.InitializeComm()
lcd.SetOrientation(Orientation.LANDSCAPE)
lcd.SetBrightness(100)
lcd.Clear()

w, h = lcd.get_width(), lcd.get_height()
img = Image.new("RGB", (w, h), (12, 16, 34))
d = ImageDraw.Draw(img)
# bright color bars so brightness changes are obvious on camera
bars = [(255, 80, 80), (80, 255, 120), (90, 160, 255), (255, 220, 80), (220, 90, 255)]
bw = w // len(bars)
for i, c in enumerate(bars):
    d.rectangle([i * bw, 0, (i + 1) * bw, h], fill=c)
font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 120)
d.text((w // 2 - 320, h // 2 - 70), "BRIGHTNESS", font=font, fill=(0, 0, 0))
lcd.DisplayPILImage(img)

# discrete holds (for webcam capture)
for level, hold in [(100, 2.5), (40, 2.5), (8, 2.5)]:
    lcd.SetBrightness(level)
    print(f"t={time.time():.1f} brightness={level}", flush=True)
    time.sleep(hold)

# smooth triangle sweep
print("smooth sweep...", flush=True)
for _ in range(2):
    for level in list(range(0, 101, 4)) + list(range(100, -1, -4)):
        lcd.SetBrightness(level)
        time.sleep(0.05)

lcd.SetBrightness(70)
print("done", flush=True)
