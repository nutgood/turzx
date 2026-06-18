"""Clock app — large time + date, ticking every second."""
from datetime import datetime

from ..render import COL, H, W, blank, draw_centered, font, hexrgb
from .base import App


class ClockApp(App):
    name = "Clock"
    n_pages = 1
    refresh = 1.0          # tick every second

    def render(self, page):
        img, d = blank()
        now = datetime.now()
        draw_centered(d, W / 2, H / 2 - 28, now.strftime("%H:%M:%S"), font(200), hexrgb(COL["text"]))
        draw_centered(d, W / 2, H - 64, now.strftime("%A %d %B %Y"), font(48), (150, 156, 178))
        return img
