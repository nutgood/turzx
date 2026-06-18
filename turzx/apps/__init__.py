"""App registry. Add a new app: import it here and append to ``default_apps()``."""
from .base import App
from .clock import ClockApp
from .pistats import PiStatsApp
from .rack import RackApp

__all__ = ["App", "RackApp", "ClockApp", "PiStatsApp", "default_apps"]


def default_apps():
    return [RackApp(), ClockApp(), PiStatsApp()]
