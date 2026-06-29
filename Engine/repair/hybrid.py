# -*- coding: utf-8 -*-
"""Engine/repair/hybrid.py — Multi-backend hybrid repair pipeline."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..utils.file_utils import run_cmd
from ..backends import ffmpeg_backend, sox_backend, pedalboard_backend
from ..backends.pedalboard_backend import process as pb_process
from ..analyzer.quality_analyzer_lux import analyze
from . import artifact, noise, enhancer


def hybrid_repair(input_file: Path, output_file: Path, strength: float = 0.5, dry_run: bool = False) -> dict:
    """
    Production hybrid chain:
    1. SoX noise profile (if available)
    2. FFmpeg cleanup
    3. Pedalboard enhancement
    4. FFmpeg loudnorm export
    """
    steps = []
    before = analyze(input_file)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        current = input_file

        # Step 1: SoX noise profile
        if sox_backend.available():
            s1 = td / "01_sox_denoised.wav"
            steps += noise.repair_sox(current, s1, strength, dry_run)
            if s1.exists():
                current = s1

        # Step 2: FFmpeg artifact cleanup
        s2 = td / "02_ffmpeg_clean.wav"
        steps += artifact.repair_ffmpeg(current, s2, strength, dry_run)
        if s2.exists():
            current = s2

        # Step 3: Pedalboard enhancement
        if pedalboard_backend.available():
            s3 = td / "03_pedalboard_enhanced.wav"
            cfg = pedalboard_backend.enhancer_chain(strength * 0.75)
            steps += [pb_process(current, s3, cfg, dry_run)]
            if s3.exists():
                current = s3

        # Step 4: FFmpeg final export
        steps.append(ffmpeg_backend.export_audio(current, output_file, "wav", dry_run))

    after = analyze(output_file) if output_file.exists() and not dry_run else before
    return {
        "steps": steps,
        "before_score": before.get("score", 0),
        "after_score": after.get("score", 0),
        "improvement": after.get("score", 0) - before.get("score", 0),
    }
