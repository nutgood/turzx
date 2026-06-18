#!/bin/bash
cd "$(dirname "$0")"
exec env DYLD_LIBRARY_PATH=/opt/homebrew/lib ./venv/bin/python rack_kiosk.py "$@"
