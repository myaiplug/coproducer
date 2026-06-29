# -*- coding: utf-8 -*-
"""Engine/backends/sox_backend.py — SoX-based audio repair backend."""

from __future__ import annotations

from pathlib import Path
from ..utils.file_utils import which_or_none, run_cmd


def available() -> bool:
    return which_or_none("sox") is not None


def noise_profile(input_file: Path, output_file: Path, strength: float = 0.5, dry_run: bool = False) -> list[str]:
    if not which_or_none("sox"):
        return ["sox not available"]
    amount = max(0.08, min(0.28, 0.10 + strength * 0.16))
    profile_sample = output_file.parent / "_noise_sample.wav"
    profile = output_file.parent / "_noise.prof"
    cmds = [
        ["sox", str(input_file), str(profile_sample), "trim", "0", "1.25"],
        ["sox", str(profile_sample), "-n", "noiseprof", str(profile)],
        ["sox", str(input_file), str(output_file), "noisered", str(profile), f"{amount:.3f}", "highpass", "35", "gain", "-3"],
    ]
    results = []
    for c in cmds:
        results.append(run_cmd(c, dry_run))
    for tmp in [profile_sample, profile]:
        if tmp.exists():
            tmp.unlink()
    return results


def artifact_prep(input_file: Path, output_file: Path, dry_run: bool = False) -> str:
    if not which_or_none("sox"):
        return "sox not available"
    cmd = ["sox", str(input_file), str(output_file), "highpass", "25", "lowpass", "18500", "gain", "-3"]
    return run_cmd(cmd, dry_run)


def enhancer(input_file: Path, output_file: Path, dry_run: bool = False) -> str:
    if not which_or_none("sox"):
        return "sox not available"
    cmd = [
        "sox", str(input_file), str(output_file),
        "highpass", "28", "bass", "+2", "90", "treble", "+2", "9000",
        "compand", "0.3,1", "6:-70,-60,-20", "-5", "-90", "0.2",
        "gain", "-n",
    ]
    return run_cmd(cmd, dry_run)
