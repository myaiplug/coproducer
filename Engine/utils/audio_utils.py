# -*- coding: utf-8 -*-
"""Engine/utils/audio_utils.py — Shared audio I/O, DSP utilities, and metric helpers."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import welch

SUPPORTED_EXTS = {".wav", ".flac", ".aiff", ".aif", ".mp3", ".m4a", ".ogg"}


def db(x: float) -> float:
    return 20.0 * math.log10(max(float(x), 1e-12))


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_round(x: Any, n: int = 3) -> Any:
    if x is None:
        return None
    try:
        return round(float(x), n)
    except Exception:
        return x


def read_audio(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), always_2d=True)
    return data.astype(np.float32), sr


def write_audio(path: Path, data: np.ndarray, sr: int):
    path.parent.mkdir(parents=True, exist_ok=True)
    peak = float(np.max(np.abs(data))) if data.size else 0.0
    if peak > 1.0:
        data = data / peak * 0.98
    sf.write(str(path), data, sr, subtype="PCM_24")


def load_audio_safe(path: Path) -> tuple[np.ndarray, int]:
    data, sr = sf.read(str(path), always_2d=True)
    data = data.astype(np.float32)
    if data.shape[0] < sr:
        raise ValueError("Audio shorter than 1 second; quality score would be unreliable.")
    return data, sr


def block_rms_db(mono: np.ndarray, sr: int, block_ms: int = 400) -> np.ndarray:
    block = max(256, int(sr * block_ms / 1000))
    vals = []
    for i in range(0, len(mono) - block, block):
        c = mono[i:i + block]
        vals.append(db(float(np.sqrt(np.mean(c * c) + 1e-12))))
    return np.array(vals, dtype=np.float32)


def spectral_ratios(mono: np.ndarray, sr: int) -> tuple[dict[str, float], dict[str, float], float, float | None]:
    nper = min(8192, max(1024, len(mono) // 8))
    f, pxx = welch(mono, fs=sr, nperseg=nper)
    pxx = np.maximum(pxx, 1e-18)

    def band(lo: float, hi: float) -> float:
        mask = (f >= lo) & (f < hi)
        if not np.any(mask):
            return 1e-18
        return float(np.mean(pxx[mask]))

    bands = {
        "sub_20_40": band(20, 40), "sub_40_80": band(40, 80),
        "bass_80_180": band(80, 180), "mud_180_350": band(180, 350),
        "box_350_700": band(350, 700), "body_700_2000": band(700, 2000),
        "presence_2000_4500": band(2000, 4500), "harsh_4500_8000": band(4500, 8000),
        "sibilance_8000_11000": band(8000, 11000), "air_11000_16000": band(11000, 16000),
        "ultra_16000_20000": band(16000, min(20000, sr / 2)),
    }
    total = sum(bands.values()) + 1e-18
    ratios = {k: v / total for k, v in bands.items()}
    centroid = float(np.sum(f * pxx) / (np.sum(pxx) + 1e-18))
    rolloff_mask = np.cumsum(pxx) >= np.sum(pxx) * 0.95
    rolloff = float(f[np.argmax(rolloff_mask)]) if np.any(rolloff_mask) else None
    return bands, ratios, centroid, rolloff


def integrated_lufs(data: np.ndarray, sr: int) -> float | None:
    try:
        import pyloudnorm as pyln
        return float(pyln.Meter(sr).integrated_loudness(data))
    except Exception:
        mono = np.mean(data, axis=1)
        return db(float(np.sqrt(np.mean(mono * mono) + 1e-12))) - 3.0


def true_peak_estimate(data: np.ndarray) -> float:
    sample_peak = float(np.max(np.abs(data)) + 1e-12)
    peak_db = db(sample_peak)
    if peak_db > -1.0:
        peak_db += 0.35
    return peak_db
