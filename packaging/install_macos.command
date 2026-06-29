#!/bin/bash
# CoProducer v3.2 -- macOS Installer
# Saves as: CoProducer_v3.2.pkg (via productbuild) or launch directly
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/install.log"

echo "[$(date)] === CoProducer v3.2 macOS Installer ===" | tee -a "$LOG"
echo "Root: $ROOT" | tee -a "$LOG"

# ------------------------------------------------------------------
# 1. Locate Python 3.11
# ------------------------------------------------------------------
PY=""
for candidate in python3.11 python3; do
  if command -v $candidate >/dev/null 2>&1; then
    VER=$($candidate --version 2>&1)
    if echo "$VER" | grep -q "3.11"; then
      PY=$candidate
      echo "Found Python 3.11: $VER" | tee -a "$LOG"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "" | tee -a "$LOG"
  echo "ERROR: Python 3.11 is required but not found." | tee -a "$LOG"
  echo "" | tee -a "$LOG"
  echo "Install via one of:" | tee -a "$LOG"
  echo "  brew install python@3.11" | tee -a "$LOG"
  echo "  pyenv install 3.11.9 && pyenv global 3.11.9" | tee -a "$LOG"
  echo "  https://www.python.org/downloads/release/python-3119/" | tee -a "$LOG"
  exit 1
fi

# ------------------------------------------------------------------
# 2. Create virtual environment
# ------------------------------------------------------------------
VENV="$ROOT/.venv"
echo "Creating virtual environment..." | tee -a "$LOG"
rm -rf "$VENV"
$PY -m venv "$VENV"
source "$VENV/bin/activate"
echo "Venv: $VENV" | tee -a "$LOG"

# ------------------------------------------------------------------
# 3. Install Python dependencies
# ------------------------------------------------------------------
echo "Upgrading pip..." | tee -a "$LOG"
pip install --quiet --upgrade pip setuptools wheel

echo "Installing requirements (this may take several minutes)..." | tee -a "$LOG"
pip install -r "$ROOT/requirements.txt" 2>&1 | tee -a "$LOG"

# Install local package in editable mode
pip install -e "$ROOT" --no-deps -q 2>&1 | tee -a "$LOG"
echo "Local package installed." | tee -a "$LOG"

# ------------------------------------------------------------------
# 4. Verify FFmpeg
# ------------------------------------------------------------------
if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v ffprobe >/dev/null 2>&1; then
  echo "" | tee -a "$LOG"
  echo "WARNING: ffmpeg/ffprobe not found." | tee -a "$LOG"
  echo "Install: brew install ffmpeg" | tee -a "$LOG"
else
  echo "ffmpeg: $(ffmpeg -version 2>&1 | head -1)" | tee -a "$LOG"
  echo "ffprobe: $(ffprobe -version 2>&1 | head -1)" | tee -a "$LOG"
fi

# ------------------------------------------------------------------
# 5. Self-test
# ------------------------------------------------------------------
echo "Running dependency self-test..." | tee -a "$LOG"
$VENV/bin/python -c "
import sys
print('Python:', sys.version)
for m in ['numpy', 'soundfile', 'librosa', 'pyloudnorm', 'mutagen']:
    try:
        __import__(m)
        print(f'  OK: {m}')
    except Exception as e:
        print(f'  FAIL: {m} - {e}')
        sys.exit(1)
print('All core modules importable.')
" 2>&1 | tee -a "$LOG"

# ------------------------------------------------------------------
# 6. macOS .app bundle setup (optional)
# ------------------------------------------------------------------
# Creates a launchable .command for easy access
LAUNCHER="$ROOT/CoProducer.command"
cat > "$LAUNCHER" << 'EOF'
#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate
python CoProducerDesktop.py
EOF
chmod +x "$LAUNCHER"
echo "Launcher: $LAUNCHER" | tee -a "$LOG"

echo "" | tee -a "$LOG"
echo "=== Installation Complete ===" | tee -a "$LOG"
echo "Activate: source .venv/bin/activate" | tee -a "$LOG"
echo "Launch:   python CoProducerDesktop.py" | tee -a "$LOG"
echo "Or:       open CoProducer.command" | tee -a "$LOG"
echo "Log:      $LOG" | tee -a "$LOG"

# ------------------------------------------------------------------
# 7. DMG build instructions (for distribution)
# ------------------------------------------------------------------
echo "" | tee -a "$LOG"
echo "--- DMG Packaging ---" | tee -a "$LOG"
echo "To build a distributable .dmg:" | tee -a "$LOG"
echo "  1. Create a folder: CoProducer.app" | tee -a "$LOG"
echo "  2. Copy the project into it" | tee -a "$LOG"
echo "  3. Run: hdiutil create -volname CoProducer -srcfolder CoProducer.app -ov -format UDZO CoProducer_v3.2.dmg" | tee -a "$LOG"
