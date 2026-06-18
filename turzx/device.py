"""Display device access — wraps the vendored upstream LcdCommTuringUSB driver.

Importing this module puts the vendored ``lib/`` (mathoudebine/turing-smart-screen-python)
on sys.path, so ``library.lcd.*`` becomes importable for the driver and for tools that need
low-level functions (send_image, send_video, etc.).
"""
import os
import sys

from . import ROOT

_LIB = os.path.join(ROOT, "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

import usb.core  # noqa: E402
import usb.util  # noqa: E402

from library.lcd.lcd_comm import Orientation  # noqa: E402
from library.lcd.lcd_comm_turing_usb import LcdCommTuringUSB, VENDOR_ID, PRODUCT_ID  # noqa: E402


def open_display(brightness=70, orientation=Orientation.LANDSCAPE):
    """Open the USB display (auto-detect), init, set orientation + brightness."""
    lcd = LcdCommTuringUSB(com_port="AUTO")
    lcd.InitializeComm()
    lcd.SetOrientation(orientation)
    lcd.SetBrightness(brightness)
    return lcd


def present():
    """True if a supported TURZX display is currently enumerated on USB.

    The driver swallows USB write errors, so this is how we detect a disconnect/replug.
    """
    try:
        return any(usb.core.find(idVendor=VENDOR_ID, idProduct=pid) is not None for pid in PRODUCT_ID)
    except Exception:
        return False


def dispose(lcd):
    """Release the libusb handle of a (possibly dead) display before reopening."""
    try:
        usb.util.dispose_resources(lcd.dev)
    except Exception:
        pass
