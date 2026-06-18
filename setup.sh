#!/usr/bin/env bash
# Set up the TURZX display tooling on a fresh machine (tested: Raspberry Pi OS / Debian, macOS).
set -euo pipefail
cd "$(dirname "$0")"

OS="$(uname -s)"
if [ "$OS" = "Linux" ]; then
    echo ">> Installing system packages (sudo)…"
    sudo apt-get update -qq
    sudo apt-get install -y python3-venv libusb-1.0-0 fonts-dejavu-core git ffmpeg
elif [ "$OS" = "Darwin" ]; then
    echo ">> macOS: ensure libusb is installed (brew install libusb). Run scripts with DYLD_LIBRARY_PATH=/opt/homebrew/lib"
fi

# Vendored upstream driver (mathoudebine/turing-smart-screen-python) — provides LcdCommTuringUSB
if [ ! -d lib/library ]; then
    echo ">> Cloning upstream display library into ./lib …"
    rm -rf lib
    git clone --depth 1 https://github.com/mathoudebine/turing-smart-screen-python.git lib
fi

echo ">> Creating venv and installing Python deps…"
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

echo
echo "Setup complete."
echo "  1. Put your Grafana service-account token in ./.grafana_token  (chmod 600)"
echo "  2. Test:    ./venv/bin/python rack_kiosk.py --once --save=/tmp/test.png"
echo "  3. Service: see deploy/rack-kiosk.service"
