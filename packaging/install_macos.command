#!/bin/bash
# CoProducer Core Analyzer macOS Installer
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/install.log"

echo "[$(date)] === CoProducer macOS Installer ===" | tee -a "$LOG"
echo "Root: $ROOT" | tee -a "$LOG"

# Prefer python3.11 if available
PY="python3.11"
if ! command -v $PY >/dev/null 2>&1; then
  PY="python3"
fi
if ! $PY --version 2>&1 | grep -q "3.11"; then
  echo "ERROR: Python 3.11 is required." | tee -a "$LOG"
  echo "Install via: brew install python@3.11  or  pyenv install 3.11.x" | tee -a "$LOG"
  exit 1
fi

VENV="$ROOT/.venv"
rm -rf "$VENV"
$PY -m venv "$VENV"
source "$VENV/bin/activate"
pip install --upgrade pip setuptools wheel
pip install -r "$ROOT/requirements.txt" 2>&1 | tee -a "$LOG"

# Verify ffmpeg
if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "WARNING: Install ffmpeg (brew install ffmpeg)" | tee -a "$LOG"
fi

# Quick test
python -c "
import sys
print('Python', sys.version)
for m in ['numpy','librosa','pyloudnorm','mutagen']:
    __import__(m)
print('Core imports OK')
" 2>&1 | tee -a "$LOG"

echo "=== macOS install complete. Activate: source .venv/bin/activate" | tee -a "$LOG"
echo "Launch: ./START_ANALYZER_PRO.bat (or python -m app.nodaw.cli ...)"