#!/data/data/com.termux/files/usr/bin/bash
# CoProducer v3.2 -- Termux (Android) Bootstrap
# Requires: Termux from F-Droid (not Play Store -- outdated)
#           Storage permission granted: termux-setup-storage
set -e

echo "=========================================="
echo " CoProducer v3.2 - Termux Installer"
echo " AI Production Assistant (Android CLI)"
echo "=========================================="
echo ""

# Update packages
echo "[1/5] Updating Termux packages..."
pkg update -y -q
pkg upgrade -y -q

# Install core deps
echo "[2/5] Installing Python + FFmpeg..."
pkg install -y python ffmpeg libsndfile binutils 2>&1 | tail -1

# Create venv
echo "[3/5] Creating virtual environment..."
python -m venv .venv --without-pip 2>/dev/null || python -m venv .venv
source .venv/bin/activate
pkg install -y python-pip 2>&1 | tail -1
pip install --upgrade pip setuptools wheel -q

# Install Python packages
echo "[4/5] Installing Python dependencies (may take 10+ min)..."
pip install numpy scipy soundfile 2>&1 | tail -3
pip install pyloudnorm mutagen audioread 2>&1 | tail -3
echo "Optional heavy deps (librosa) -- skip if low on RAM"
pip install librosa 2>&1 | tail -3 || echo "librosa skipped (RAM limited)"

# Install local package
echo "[5/5] Installing CoProducer engine..."
pip install -e . --no-deps -q 2>&1 | tail -1

echo ""
echo "=========================================="
echo " CoProducer Termux install complete."
echo "=========================================="
echo ""
echo "Activate: source .venv/bin/activate"
echo "Run:      python -m nodaw.cli --mode single --input path/to/audio.wav"
echo ""
echo "NOTE: Desktop GUI (PySide6) is not supported on Android."
echo "CoProducer runs in CLI mode on Termux."
echo "For full GUI: use Windows/macOS desktop build."
echo ""
