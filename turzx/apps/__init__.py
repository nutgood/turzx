"""App registry. Add a new app: import it here and append to ``default_apps()``."""
from .base import App
from .cameras import CamerasApp
from .clock import ClockApp
from .pistats import PiStatsApp
from .rack import RackApp

__all__ = ["App", "RackApp", "ClockApp", "PiStatsApp", "CamerasApp", "default_apps"]


def default_apps():
    apps = [RackApp(), ClockApp(), PiStatsApp()]
    cameras = CamerasApp.maybe()          # only if cameras.json is present
    if cameras:
        apps.insert(0, cameras)
    return apps
