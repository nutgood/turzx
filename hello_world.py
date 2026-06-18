#!/usr/bin/env python3
"""Display "Hello World" on a Turing 8.8" smart screen (TURZX USB, VID 0x1cbe / PID 0x0088).

This screen is the new-generation TURZX USB-protocol model (vendor-specific USB class,
driven via libusb bulk transfers with DES-encrypted command packets) -- NOT the serial
Rev A 3.5" screen that zackpollard/turing-display-apps targets. We therefore use the
LcdCommTuringUSB driver from mathoudebine/turing-smart-screen-python directly.
"""
import os
import sys

# Make the cloned library importable
LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

from PIL import Image, ImageDraw, ImageFont

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB


def load_font(size):
    for path in (
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/SFNS.ttf",
    ):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def main():
    # AUTO discovers the device by VID/PID; resolution is read from the PID map.
    lcd = LcdCommTuringUSB(com_port="AUTO")
    lcd.InitializeComm()          # sync handshake (command ID 10)
    lcd.SetOrientation(Orientation.LANDSCAPE)  # 1920 x 480, horizontal reading
    lcd.SetBrightness(60)
    lcd.Clear()

    w, h = lcd.get_width(), lcd.get_height()
    print(f"Rendering on {w}x{h} ({lcd.dev_pid:#06x})")

    img = Image.new("RGB", (w, h), (8, 12, 28))
    draw = ImageDraw.Draw(img)
    font = load_font(200)
    text = "Hello World"

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (w - tw) // 2 - bbox[0]
    y = (h - th) // 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    lcd.DisplayPILImage(img)
    print("Displayed 'Hello World'.")


if __name__ == "__main__":
    main()
