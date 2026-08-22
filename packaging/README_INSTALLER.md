# CoProducer Windows Installer (1.0.0-beta)

Produces a single **Setup.exe** that installs everything needed to run CoProducer
smoothly — no separate Python, pip, or FFmpeg setup for end users.

**Output:** `packaging\output\CoProducer-Setup-1.0.0-beta.exe`

## What the Setup installs

| Component | Included |
|-----------|----------|
| CoProducer GUI + analysis engine | Yes (frozen) |
| Python 3.11 runtime | Yes (inside freeze) |
| PySide6, numpy, scipy, librosa, soundfile | Yes |
| Pedalboard (high-quality repair) | Yes |
| pyloudnorm, mutagen | Yes |
| FFmpeg + FFprobe | Yes (`runtime\ffmpeg\bin\`) |
| Themes, Studio Player, A/B, History | Yes |
| Desktop + Start Menu shortcuts | Yes |
| Writable reports / exports / logs | Yes |
| Offline user docs | Yes |
| Uninstaller | Yes |

## Build machine prerequisites

1. **Windows 10/11** 64-bit  
2. **Python 3.11** (`py -3.11`) — [python.org](https://www.python.org/downloads/)  
3. **Inno Setup 6** — [jrsoftware.org/isinfo.php](https://jrsoftware.org/isinfo.php)  
4. Internet (first build) for `pip install`  
5. Optional: FFmpeg on PATH (otherwise the build downloads essentials)

## One-command build

From the project root:

```bat
BUILD_INSTALLER.bat
```

Or PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
```

Recompile installer only (reuse last freeze):

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1 -SkipFreeze
```

## End-user install

1. Double-click `CoProducer-Setup-1.0.0-beta.exe`  
2. Accept license → Next → Install  
3. Launch from Desktop or Start Menu  

No console window. No extra dependencies.

## Artifacts

| Artifact | Path |
|----------|------|
| Frozen portable folder | `packaging\dist\CoProducer\` |
| **Installer (share this)** | `packaging\output\CoProducer-Setup-1.0.0-beta.exe` |
| Build logs | `logs\build_exe.log`, `logs\build_installer.log` |

## Dev install (no freeze)

For day-to-day development only:

```powershell
cd packaging
.\install.ps1
```

Then launch with `CoProducer.vbs` / `START_GUI.bat`.
