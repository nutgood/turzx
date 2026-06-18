#!/usr/bin/env python3
"""Stream an H.264 MP4 to the Turing 8.8" via the native video path. Usage: play_video.py [file] [loop]"""
import os
import signal
import sys

LIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "lib")  # repo-root/lib
sys.path.insert(0, LIB)

from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB, send_video, send_sync_command, send_brightness_command

path = sys.argv[1] if len(sys.argv) > 1 else "demo.mp4"
loop = "loop" in sys.argv[2:]

lcd = LcdCommTuringUSB(com_port="AUTO")
lcd.InitializeComm()
lcd.SetBrightness(70)

# Translate SIGTERM into KeyboardInterrupt so send_video's finally-block sends STOP_STREAM
# cleanly (avoids leaving the H.264 stream half-open / desynced).
signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

print(f"Playing {path} (loop={loop}) ...")
try:
    send_video(lcd.dev, path, loop=loop)
finally:
    send_sync_command(lcd.dev)
    print("stopped cleanly")
