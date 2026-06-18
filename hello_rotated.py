#!/usr/bin/env python3
"""Redraw the text rotated 180 degrees (REVERSE_LANDSCAPE)."""
import os
import sys

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

from PIL import Image, ImageDraw, ImageFont

from library.lcd.lcd_comm import Orientation
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB

BG = (8, 12, 28)


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
    lcd = LcdCommTuringUSB(com_port="AUTO")
    lcd.InitializeComm()
    lcd.SetOrientation(Orientation.REVERSE_LANDSCAPE)  # 180 deg from LANDSCAPE
    lcd.SetBrightness(60)

    w, h = lcd.get_width(), lcd.get_height()
    font = load_font(200)
    text = "Hello Cunt"

    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) // 2 - bbox[0], (h - th) // 2 - bbox[1]), text, font=font, fill=(255, 255, 255))

    print(f"Rendering {text!r} on {w}x{h}, REVERSE_LANDSCAPE")
    lcd.DisplayPILImage(img)
    print("Displayed rotated 180 degrees.")


if __name__ == "__main__":
    main()
