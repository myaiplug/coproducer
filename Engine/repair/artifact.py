# -*- coding: utf-8 -*-
"""Engine/repair/artifact.py — Artifact removal (clicks, pops, clipping, codec grit)."""

from __future__ import annotations

from pathlib import Path

from ..backends import ffmpeg_backend, sox_backend, pedalboard_backend, auto_backend
from ..backends.pedalboard_backend import process as pb_process
from ..utils.audio_utils import clamp


def repair_ffmpeg(input_file: Path, output_file: Path, strength: float = 0.5, dry_run: bool = False) -> list[str]:
    flt = ffmpeg_backend.build_artifact_filter(strength)
    return [ffmpeg_backend.process(input_file, output_file, flt, dry_run)]


def repair_sox(input_file: Path, output_file: Path, dry_run: bool = False) -> list[str]:
    return [sox_backend.artifact_prep(input_file, output_file, dry_run)]


def repair_pedalboard(input_file: Path, output_file: Path, strength: float = 0.5, dry_run: bool = False) -> list[str]:
    cfg = pedalboard_backend.artifact_chain(strength)
    return [pb_process(input_file, output_file, cfg, dry_run)]


def repair(input_file: Path, output_file: Path, backend: str = "auto", strength: float = 0.5, dry_run: bool = False) -> list[str]:
    bk = auto_backend.select({}, backend)
    if bk == "pedalboard" and pedalboard_backend.available():
        return repair_pedalboard(input_file, output_file, strength, dry_run)
    if bk == "sox" and sox_backend.available():
        return repair_sox(input_file, output_file, dry_run)
    return repair_ffmpeg(input_file, output_file, strength, dry_run)
