#!/usr/bin/env python3
"""Display a static test frame (text + orientation markers). Usage: show.py "TEXT" [landscape|reverse]"""
import os
import sys

from PIL import Image, ImageDraw, ImageFont

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB

text = sys.argv[1] if len(sys.argv) > 1 else "HELLO 8.8"
mode = sys.argv[2].lower() if len(sys.argv) > 2 else "landscape"
orient = Orientation.REVERSE_LANDSCAPE if mode.startswith("rev") else Orientation.LANDSCAPE

lcd = LcdCommTuringUSB(com_port="AUTO")
lcd.InitializeComm()
lcd.SetOrientation(orient)
lcd.SetBrightness(70)
lcd.Clear()

w, h = lcd.get_width(), lcd.get_height()
img = Image.new("RGB", (w, h), (10, 14, 30))
d = ImageDraw.Draw(img)
font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 150)
small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)

bb = d.textbbox((0, 0), text, font=font)
d.text(((w - (bb[2] - bb[0])) // 2 - bb[0], (h - (bb[3] - bb[1])) // 2 - bb[1]),
       text, font=font, fill=(120, 220, 255))
# orientation markers: TL corner label + arrow pointing up
d.text((20, 10), "TOP-LEFT", font=small, fill=(255, 90, 140))
d.text((w - 220, h - 60), "BOT-RIGHT", font=small, fill=(180, 230, 140))
d.polygon([(w // 2, 20), (w // 2 - 30, 90), (w // 2 + 30, 90)], fill=(255, 210, 90))  # up arrow

lcd.DisplayPILImage(img)
print(f"shown {text!r} in {mode} on {w}x{h}")
