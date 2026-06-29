# -*- coding: utf-8 -*-
"""Engine/backends/pedalboard_backend.py — Pedalboard-based high-quality DSP backend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..utils.audio_utils import clamp


def available() -> bool:
    try:
        import pedalboard
        return True
    except ImportError:
        return False


def process(
    input_file: Path, output_file: Path,
    plugins_cfg: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> str:
    if dry_run:
        return "pedalboard: (dry run)"

    try:
        from pedalboard import (
            Pedalboard, HighpassFilter, LowpassFilter, LowShelfFilter,
            HighShelfFilter, Compressor, Gain, Limiter, NoiseGate,
        )
        from pedalboard.io import AudioFile
    except ImportError:
        raise RuntimeError("pedalboard not installed. Run: pip install pedalboard")

    board = Pedalboard()
    if plugins_cfg:
        for cfg in plugins_cfg:
            t = cfg.get("type", "")
            if t == "highpass":
                board.append(HighpassFilter(cutoff_frequency_hz=cfg.get("cutoff", 28)))
            elif t == "lowpass":
                board.append(LowpassFilter(cutoff_frequency_hz=cfg.get("cutoff", 18000)))
            elif t == "lowshelf":
                board.append(LowShelfFilter(cutoff_frequency_hz=cfg.get("cutoff", 100), gain_db=cfg.get("gain", 0), q=cfg.get("q", 0.7)))
            elif t == "highshelf":
                board.append(HighShelfFilter(cutoff_frequency_hz=cfg.get("cutoff", 8500), gain_db=cfg.get("gain", 0), q=cfg.get("q", 0.65)))
            elif t == "compressor":
                board.append(Compressor(threshold_db=cfg.get("threshold", -20), ratio=cfg.get("ratio", 2), attack_ms=cfg.get("attack", 14), release_ms=cfg.get("release", 130)))
            elif t == "noisegate":
                board.append(NoiseGate(threshold_db=cfg.get("threshold", -50), ratio=cfg.get("ratio", 2), attack_ms=cfg.get("attack", 5), release_ms=cfg.get("release", 150)))
            elif t == "gain":
                board.append(Gain(gain_db=cfg.get("gain", 0)))
            elif t == "limiter":
                board.append(Limiter(threshold_db=cfg.get("threshold", -1)))

    with AudioFile(str(input_file)) as f:
        audio = f.read(f.frames)
        sr = f.samplerate

    processed = board(audio, sr)
    with AudioFile(str(output_file), "w", sr, processed.shape[0]) as f:
        f.write(processed)
    return "pedalboard: processed"


def artifact_chain(strength: float = 0.5) -> list[dict]:
    s = clamp(strength, 0.05, 1.0)
    return [
        {"type": "highpass", "cutoff": 25},
        {"type": "lowpass", "cutoff": 18500},
        {"type": "compressor", "threshold": -18, "ratio": 2.5, "attack": 10, "release": 120},
        {"type": "gain", "gain": -1},
        {"type": "limiter", "threshold": -1},
    ]


def noise_chain(strength: float = 0.5) -> list[dict]:
    s = clamp(strength, 0.05, 1.0)
    return [
        {"type": "highpass", "cutoff": 70},
        {"type": "noisegate", "threshold": -45 + s * 10, "ratio": 2, "attack": 5, "release": 150},
        {"type": "lowpass", "cutoff": 18000},
        {"type": "compressor", "threshold": -22, "ratio": 2, "attack": 14, "release": 130},
        {"type": "limiter", "threshold": -1},
    ]


def enhancer_chain(strength: float = 0.5) -> list[dict]:
    s = clamp(strength, 0.05, 1.0)
    return [
        {"type": "highpass", "cutoff": 28},
        {"type": "lowshelf", "cutoff": 100, "gain": 0.5 + s * 1.5},
        {"type": "highshelf", "cutoff": 8500, "gain": 0.5 + s * 2.0},
        {"type": "compressor", "threshold": -18 + s * 3, "ratio": 2, "attack": 14, "release": 130},
        {"type": "gain", "gain": 0.5 + s * 0.5},
        {"type": "limiter", "threshold": -1},
    ]
