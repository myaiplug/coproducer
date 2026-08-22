# Installation

**Target runtime: Python 3.11 (locked).**

## Windows (Recommended - Setup.exe installer)

### End users (no Python required)

1. Build or obtain `packaging\output\CoProducer-Setup-3.1.0.exe`
2. Run the Setup wizard (installs app, deps, FFmpeg when bundled, shortcuts)
3. Launch **CoProducer** from Desktop / Start Menu
4. Uninstall via Windows **Apps & features**

Rebuild the installer on a machine with Python 3.11 + Inno Setup 6:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

See `packaging\README_INSTALLER.md`.

### Developers (editable venv install)

1. Ensure Python 3.11 is installed:
   - https://www.python.org/downloads/release/python-3119/
   - `winget install --id Python.Python.3.11 --exact`

2. Install FFmpeg (includes ffprobe):
   - `winget install --id Gyan.FFmpeg.Essentials --exact`
   - Restart your terminal/PowerShell.

3. From the project root run the high-end installer:

       cd packaging
       .\install.ps1

   This will:
   - Verify Python 3.11 (clear error + links if missing)
   - Create `.venv`
   - `pip install -r requirements.txt` (pinned)
   - Verify ffmpeg/ffprobe
   - Run import + engine self-test
   - Log everything to `logs/install.log`

4. Launch:

       ..\START_GUI.bat
       # or
       ..\START_ANALYZER_PRO.bat
       # or
       .\.venv\Scripts\Activate.ps1
       python -m nodaw.cli --mode doctor

## macOS

Use the provided script:

    chmod +x packaging/install_macos.command
    ./packaging/install_macos.command

Or manually:
    python3.11 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt

Install ffmpeg via Homebrew if needed: `brew install ffmpeg`

## Android (Termux)

    pkg install python ffmpeg
    bash packaging/install_android_termux.sh

## iPhone / iOS

Full native execution of librosa etc. is heavily restricted. Recommended workflow:
- Perform analysis on desktop (Windows/macOS)
- The analyzer uses Mutagen to embed CoProducer* tags (score, LUFS, detected tempo, version, date, etc.)
- The enriched files can be read on iOS with any tag reader or future companion app.

See packaging/INSTALL_IOS.md

## Verifying the install

After any platform install run:

    START_ANALYZER_PRO.bat --mode doctor
    # or
    python -m nodaw.cli --mode doctor --no-previews

All checks should pass (or warn only on optional encoders).

## Notes on optional Essentia

Essentia is powerful but deliberately **not** auto-installed. To enable Advanced Analysis Mode later:

    .venv\Scripts\python -m pip install essentia   # or appropriate wheel/conda on your platform

See requirements.txt for comments.

## Desktop GUI (PySide6)

The premium desktop app requires:

    pip install PySide6

Launch with:

    python CoProducerDesktop.py
    # or
    START_GUI.bat

The old Tkinter interface (CoProducer_GUI.py) is kept only as an internal testing tool.

## Metadata writing notes

CoProducer tags (score, LUFS, tempo, version) are written using Mutagen.
- Strong support: MP3, FLAC, M4A, AIFF, Opus
- Limited: WAV (no standard ID3 container). The rich data is always available in the sidecar JSON/HTML reports.

All original tags are preserved when possible.

