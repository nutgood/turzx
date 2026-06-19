"""Drive the display's native H.264 decoder (the smooth ~25fps video path).

The firmware decodes an H.264 (Annex-B) stream of 480x1920 portrait frames. Use this for
photographic/video content — the PNG path can't (photographic PNGs exceed the 1MB payload
limit and fall back to JPEG, which this firmware silently drops).

Typical use:
    chunk = preamble(dev, brightness=70, fps=20)
    # feed Annex-B H.264 bytes:
    send_chunk(dev, data)            # repeatedly
    stop(dev)                        # when done
"""
from .device import LcdCommTuringUSB  # noqa: F401  (ensures lib/ is on sys.path)
from library.lcd.lcd_comm_turing_usb import (  # noqa: E402
    build_command_packet_header, clear_image, encrypt_command_packet,
    send_brightness_command, send_frame_rate_command, write_to_device,
    CMD_PLAY_H264_CHUNK, CMD_GET_H264_CHUNK_SIZE, CMD_GET_STREAM_STATUS, CMD_STOP_STREAM,
)


def _cmd(dev, cid):
    return write_to_device(dev, encrypt_command_packet(build_command_packet_header(cid)))


def preamble(dev, brightness=70, fps=20):
    """Put the device into video-streaming mode; returns the negotiated chunk size."""
    for cid in (111, 112, 13):
        _cmd(dev, cid)
    send_brightness_command(dev, int(brightness / 100 * 102))
    _cmd(dev, 41)
    clear_image(dev)
    send_frame_rate_command(dev, fps)
    resp = _cmd(dev, CMD_GET_H264_CHUNK_SIZE)
    chunk = 202752
    if resp and len(resp) >= 12:
        neg = int.from_bytes(resp[8:12], "big")
        if 0 < neg <= 1024 * 1024:
            chunk = neg
    return chunk


def send_chunk(dev, data):
    pkt = build_command_packet_header(CMD_PLAY_H264_CHUNK)
    n = len(data)
    pkt[8] = (n >> 24) & 0xFF
    pkt[9] = (n >> 16) & 0xFF
    pkt[10] = (n >> 8) & 0xFF
    pkt[11] = n & 0xFF
    write_to_device(dev, encrypt_command_packet(pkt) + data)


def queue_depth(dev):
    st = _cmd(dev, CMD_GET_STREAM_STATUS)
    return st[8] if st and len(st) > 8 else 0


def stop(dev):
    _cmd(dev, CMD_STOP_STREAM)
