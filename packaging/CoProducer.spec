# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec — CoProducer Desktop (onedir, windowed, full runtime)
# Build: py -3.11 -m PyInstaller packaging/CoProducer.spec --noconfirm

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "app"))

block_cipher = None

datas = [
    (str(ROOT / "app" / "nodaw" / "ui" / "assets"), "app/nodaw/ui/assets"),
    (str(ROOT / "assets"), "assets"),
    (str(ROOT / "config" / "settings.json"), "config"),
    (str(ROOT / "LICENSE.txt"), "."),
    (str(ROOT / "README.md"), "."),
]

# End-user docs bundled for offline help
for doc in (
    "docs/USER_GUIDE.md",
    "docs/INSTALLATION.md",
    "docs/BETA_TESTER_GUIDE.md",
    "docs/KNOWN_ISSUES.md",
):
    p = ROOT / doc
    if p.is_file():
        datas.append((str(p), "docs"))

# Optional sample input folders (empty README stubs)
for sub in ("input/song", "input/reference", "input/batch", "input/album"):
    p = ROOT / sub
    if p.is_dir():
        datas.append((str(p), sub.replace("\\", "/")))

# Seed beta invite config if present (empty invites still ok)
beta_inv = ROOT / "config" / "beta_invites.json"
if beta_inv.is_file():
    datas.append((str(beta_inv), "config"))

hiddenimports = [
    "nodaw",
    "nodaw.cli",
    "nodaw.config",
    "nodaw.core.engine",
    "nodaw.core.models",
    "nodaw.core.scoring",
    "nodaw.audio.analyzer",
    "nodaw.audio.ffmpeg",
    "nodaw.audio.metrics",
    "nodaw.audio.tags_media",
    "nodaw.audio.pedalboard_repair",
    "nodaw.audio.convert",
    "nodaw.beta",
    "nodaw.beta.license",
    "nodaw.beta.telemetry",
    "nodaw.features.repairs",
    "nodaw.features.reference",
    "nodaw.features.streaming",
    "nodaw.features.codecs",
    "nodaw.features.diagnostics",
    "nodaw.features.history",
    "nodaw.features.collections",
    "nodaw.reporting.renderers",
    "nodaw.ui.theme",
    "nodaw.ui.skins",
    "nodaw.ui.components",
    "nodaw.ui.charts",
    "nodaw.ui.player",
    "nodaw.ui.ab_studio",
    "nodaw.ui.track_meta",
    "nodaw.ui.prefs",
    "nodaw.ui.animations",
    "nodaw.ui.icons",
    "nodaw.ui.metric_status",
    "nodaw.ui.eq_knobs",
    "nodaw.ui.convert_dialog",
    "nodaw.ui.analysis_history",
    "pedalboard",
    "pedalboard.io",
    "pedalboard_native",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
    "PySide6.QtMultimedia",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtNetwork",
    "numpy",
    "scipy",
    "scipy.signal",
    "scipy.ndimage",
    "soundfile",
    "sounddevice",
    "nodaw.audio.pcm_player",
    "nodaw.audio.live_fx",
    "librosa",
    "librosa.feature",
    "librosa.core",
    "librosa.util",
    "sklearn",
    "sklearn.decomposition",
    "sklearn.utils",
    "numba",
    "llvmlite",
    "mutagen",
    "mutagen.id3",
    "mutagen.mp4",
    "mutagen.flac",
    "mutagen.wave",
    "mutagen.oggvorbis",
    "pyloudnorm",
    "audioread",
    "packaging",
    "pkg_resources",
    "certifi",
    "charset_normalizer",
    "soxr",
    "msgpack",
]

a = Analysis(
    [str(ROOT / "CoProducerDesktop.py")],
    pathex=[str(ROOT), str(ROOT / "app")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "IPython",
        "jupyter",
        "pytest",
        "essentia",
        "torch",
        "tensorflow",
        "cv2",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CoProducer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,  # no black terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "assets" / "icon.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CoProducer",
)
