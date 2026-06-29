# -*- coding: utf-8 -*-
"""Engine/repair/presets.py — Preset-to-backend-chain mapping."""

from __future__ import annotations

PRESETS: dict[str, dict] = {
    "auto": {"mode": "auto", "backend": "auto", "strength": 0.62, "chain": "full"},
    "podcast": {"mode": "noise", "backend": "ffmpeg", "strength": 0.6, "chain": "noise", "hp": 80, "target_lufs": -16},
    "vocal": {"mode": "noise", "backend": "ffmpeg", "strength": 0.55, "chain": "noise", "hp": 75, "target_lufs": -16},
    "music": {"mode": "enhance", "backend": "pedalboard", "strength": 0.5, "chain": "enhance", "hp": 28, "target_lufs": -14},
    "stem": {"mode": "artifact", "backend": "pedalboard", "strength": 0.55, "chain": "artifact", "hp": 28},
    "old_mp3": {"mode": "full", "backend": "hybrid", "strength": 0.65, "chain": "full", "hp": 28, "target_lufs": -14},
    "youtube": {"mode": "full", "backend": "hybrid", "strength": 0.7, "chain": "full", "hp": 30, "target_lufs": -14},
    "cassette": {"mode": "full", "backend": "hybrid", "strength": 0.75, "chain": "full", "hp": 40},
    "vinyl": {"mode": "full", "backend": "hybrid", "strength": 0.7, "chain": "full", "hp": 45},
    "mastering": {"mode": "enhance", "backend": "pedalboard", "strength": 0.45, "chain": "enhance", "hp": 22, "target_lufs": -14},
}


def get(name: str) -> dict:
    return PRESETS.get(name, PRESETS["auto"])


def list_names() -> list[str]:
    return list(PRESETS.keys())


def choose_from_analysis(analysis: dict) -> str:
    flags = analysis.get("issue_flags", [])
    if "high_noise_floor" in flags or "moderate_noise_floor" in flags:
        return "podcast"
    if "heavy_clipping" in flags or "clipping" in flags or "clicks_pops" in flags:
        return "stem"
    if "dull_bandlimited" in flags or "codec_damage_risk" in flags:
        return "old_mp3"
    if "rumble" in flags or "mud" in flags:
        return "music"
    return "mastering"
