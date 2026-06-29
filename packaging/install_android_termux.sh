#!/data/data/com.termux/files/usr/bin/bash
# CoProducer Core Analyzer - Termux (Android) bootstrap
set -e
echo "=== CoProducer Termux Installer ==="
pkg update -y && pkg install -y python ffmpeg
python -m venv .venv || python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo "Termux install done."
echo "Activate: source .venv/bin/activate"
echo "Note: Some heavy MIR libs may need extra build deps (pkg install libsndfile etc)."
echo "Run analysis via python -m nodaw.cli --mode doctor etc."
