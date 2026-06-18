#!/usr/bin/env python3
"""Live, on-the-fly H.264 stream of the current time (with milliseconds).

Frames are generated in real time (current wall-clock incl. ms), piped through a
low-latency libx264 encoder, and the encoded Annex-B chunks are streamed to the
Turing 8.8" firmware H.264 decoder via cmd 121 -- continuously (never flagged last).
"""
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

LIB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")
sys.path.insert(0, LIB)

from library.lcd.lcd_comm_turing_usb import (
    LcdCommTuringUSB, build_command_packet_header, encrypt_command_packet, write_to_device,
    clear_image, send_brightness_command, send_frame_rate_command,
    CMD_PLAY_H264_CHUNK, CMD_GET_H264_CHUNK_SIZE, CMD_GET_STREAM_STATUS, CMD_STOP_STREAM,
)

W, H = 1920, 480       # landscape design; rotated to 480x1920 portrait for the panel
FPS = 20
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else float("inf")

def _font_path():
    for c in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"):
        if os.path.exists(c):
            return c
    return None


_FP = _font_path()
BIG = ImageFont.truetype(_FP, 200) if _FP else ImageFont.load_default(size=200)
SMALL = ImageFont.truetype(_FP, 46) if _FP else ImageFont.load_default(size=46)
stop = threading.Event()


def producer(ff_stdin):
    bg = Image.new("RGB", (W, H), (6, 10, 24))
    next_t = time.time()
    while not stop.is_set():
        now = datetime.now()
        clock = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}"
        img = bg.copy()
        d = ImageDraw.Draw(img)
        bb = d.textbbox((0, 0), clock, font=BIG)
        d.text(((W - (bb[2] - bb[0])) // 2 - bb[0], (H - (bb[3] - bb[1])) // 2 - bb[1]),
               clock, font=BIG, fill=(120, 230, 255))
        d.text((30, 18), "LIVE • on-the-fly H.264", font=SMALL, fill=(255, 110, 160))
        try:
            ff_stdin.write(img.transpose(Image.Transpose.ROTATE_270).tobytes())
        except (BrokenPipeError, ValueError):
            break
        next_t += 1 / FPS
        dt = next_t - time.time()
        if dt > 0:
            time.sleep(dt)
        else:
            next_t = time.time()  # we fell behind; resync
    try:
        ff_stdin.close()
    except Exception:
        pass


def main():
    lcd = LcdCommTuringUSB(com_port="AUTO")
    dev = lcd.dev
    lcd.InitializeComm()

    # --- video-mode preamble (mirrors send_video setup) ---
    for cid in (111, 112, 13):
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(cid)))
    send_brightness_command(dev, 65)
    write_to_device(dev, encrypt_command_packet(build_command_packet_header(41)))
    clear_image(dev)
    send_frame_rate_command(dev, FPS)
    resp = write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_GET_H264_CHUNK_SIZE)))
    chunk_size = 202752
    if resp and len(resp) >= 12:
        neg = int.from_bytes(resp[8:12], "big")
        if 0 < neg <= 1024 * 1024:
            chunk_size = neg

    # --- low-latency live encoder: raw rgb24 frames in -> Annex-B H.264 out ---
    ff = subprocess.Popen(
        ["ffmpeg", "-hide_banner", "-loglevel", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{H}x{W}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
         "-profile:v", "baseline", "-pix_fmt", "yuv420p", "-g", str(FPS), "-bf", "0",
         "-x264-params", "repeat-headers=1",
         "-f", "h264", "-"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    threading.Thread(target=producer, args=(ff.stdin,), daemon=True).start()
    print(f"Streaming live clock @ {FPS}fps (chunk_size={chunk_size}) ...", flush=True)

    out_fd = ff.stdout.fileno()
    start = time.time()
    last_status = 0.0
    n = 0
    try:
        while not stop.is_set() and time.time() - start < DURATION:
            data = os.read(out_fd, chunk_size)
            if not data:
                break
            pkt = build_command_packet_header(CMD_PLAY_H264_CHUNK)
            ln = len(data)
            pkt[8] = (ln >> 24) & 0xFF; pkt[9] = (ln >> 16) & 0xFF
            pkt[10] = (ln >> 8) & 0xFF; pkt[11] = ln & 0xFF
            write_to_device(dev, encrypt_command_packet(pkt) + data)
            n += 1
            now = time.time()
            if now - last_status > 0.25:  # periodic flow control
                st = write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_GET_STREAM_STATUS)))
                last_status = now
                if st and len(st) > 8 and st[8] > 3:
                    time.sleep(0.02)
    finally:
        stop.set()
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(CMD_STOP_STREAM)))
        try:
            ff.terminate()
        except Exception:
            pass
        print(f"streamed {n} chunks, stopped cleanly", flush=True)


if __name__ == "__main__":
    main()
