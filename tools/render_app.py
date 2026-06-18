#!/usr/bin/env python3
"""Render an app's page to a PNG without the display — for offline app iteration.

Usage: render_app.py "<App name>" [page] [out.png]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from turzx.apps import default_apps

name = sys.argv[1] if len(sys.argv) > 1 else "Clock"
page = int(sys.argv[2]) if len(sys.argv) > 2 else 0
out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/render.png"

apps = {a.name.lower(): a for a in default_apps()}
app = apps.get(name.lower())
if app is None:
    sys.exit(f"unknown app {name!r}; have: {', '.join(a.name for a in default_apps())}")
app.update()
app.render(page).save(out)
print(f"saved {out} — {app.name} page {page}")
