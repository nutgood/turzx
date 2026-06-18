#!/usr/bin/env python3
"""Recover the screen after a desynced/interrupted USB transfer: reset + clean frame."""
import os
import sys
import time

import usb.core
import usb.util
from PIL import Image, ImageDraw, ImageFont

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB

# 1. USB-level reset to clear any half-finished bulk transfer in the firmware buffer
dev = usb.core.find(idVendor=0x1cbe, idProduct=0x0088)
if dev is not None:
    try:
        dev.reset()
        print("USB reset sent.")
    except Exception as e:
        print("reset warning:", e)
    usb.util.dispose_resources(dev)
time.sleep(2)

# 2. Re-open cleanly and draw a single static frame
lcd = LcdCommTuringUSB(com_port="AUTO")
lcd.InitializeComm()
lcd.SetOrientation(Orientation.LANDSCAPE)
lcd.SetBrightness(60)
lcd.Clear()

w, h = lcd.get_width(), lcd.get_height()
img = Image.new("RGB", (w, h), (8, 12, 28))
d = ImageDraw.Draw(img)
font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 140)
text = "Recovered"
bb = d.textbbox((0, 0), text, font=font)
d.text(((w - (bb[2] - bb[0])) // 2 - bb[0], (h - (bb[3] - bb[1])) // 2 - bb[1]),
       text, font=font, fill=(180, 230, 140))
lcd.DisplayPILImage(img)
print("Clean static frame displayed.")
