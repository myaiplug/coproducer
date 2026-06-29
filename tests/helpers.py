from __future__ import annotations

import json
import math
import shutil
import struct
import wave
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def write_tone(path: Path, frequency: float, amplitude: float, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sample_rate = 44100
    frame_count = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        frames = bytearray()
        for index in range(frame_count):
            fade = min(1.0, index / 400, (frame_count - index) / 400)
            sample = int(32767 * amplitude * fade * math.sin(2 * math.pi * frequency * index / sample_rate))
            frames.extend(struct.pack("<hh", sample, sample))
        handle.writeframes(frames)


def create_test_project(root: Path) -> Path:
    for relative in (
        "config", "assets", "input/song", "input/reference",
        "input/batch", "input/album", "reports", "exports", "logs",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)
    settings = json.loads((SOURCE_ROOT / "config" / "settings.json").read_text(encoding="utf-8"))
    settings["previews"]["duration_seconds"] = 1
    settings["analysis"]["waveform_points"] = 48
    settings["analysis"]["spectral_workers"] = 2
    settings["analysis"]["spectral_bands_hz"] = {
        "bass": [60, 150],
        "mid": [400, 1200],
        "high": [4000, 10000],
    }
    (root / "config" / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
    shutil.copy2(SOURCE_ROOT / "assets" / "report.css", root / "assets" / "report.css")
    shutil.copy2(SOURCE_ROOT / "START_ANALYZER_PRO.bat", root / "START_ANALYZER_PRO.bat")
    shutil.copytree(SOURCE_ROOT / "app", root / "app")

    write_tone(root / "input" / "song" / "primary.wav", 440, 0.35)
    write_tone(root / "input" / "reference" / "reference.wav", 330, 0.25)
    write_tone(root / "input" / "batch" / "batch_a.wav", 220, 0.20)
    write_tone(root / "input" / "batch" / "batch_b.wav", 660, 0.40)
    write_tone(root / "input" / "album" / "album_a.wav", 261.6, 0.28)
    write_tone(root / "input" / "album" / "album_b.wav", 523.2, 0.32)
    return root

