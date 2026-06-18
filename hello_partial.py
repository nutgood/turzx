#!/usr/bin/env python3
"""Partial refresh: update only the centered text band of the existing frame.

The LcdCommTuringUSB driver keeps an in-memory framebuffer (`current_state`).
We seed it with the frame already on the screen, then redraw ONLY a horizontal
band (the text region) via DisplayPILImage(x, y) -- a partial/regional refresh
rather than rebuilding the whole 1920x480 frame.
"""
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


def centered_text_image(w, h, text, font, fill=(255, 255, 255)):
    img = Image.new("RGB", (w, h), BG)
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((w - tw) // 2 - bbox[0], (h - th) // 2 - bbox[1]), text, font=font, fill=fill)
    return img


def main():
    lcd = LcdCommTuringUSB(com_port="AUTO")
    lcd.InitializeComm()
    lcd.SetOrientation(Orientation.LANDSCAPE)

    w, h = lcd.get_width(), lcd.get_height()
    font = load_font(200)

    # Seed the driver's framebuffer with the frame currently on screen ("Hello World"),
    # so the partial paste composites over the correct background instead of black.
    lcd.current_state = centered_text_image(w, h, "Hello World", font).convert("RGBA")

    # Partial refresh: redraw only the text band (full width, centered vertical strip).
    band_h = 300
    band_y = (h - band_h) // 2
    band = centered_text_image(w, band_h, "Hello Cunt", font)

    print(f"Partial refresh: band {w}x{band_h} at y={band_y}")
    lcd.DisplayPILImage(band, x=0, y=band_y)
    print("Updated text region to 'Hello Cunt'.")


if __name__ == "__main__":
    main()
