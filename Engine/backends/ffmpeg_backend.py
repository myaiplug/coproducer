# -*- coding: utf-8 -*-
"""Engine/backends/ffmpeg_backend.py — FFmpeg-based audio repair backend."""

from __future__ import annotations

from pathlib import Path

from ..utils.file_utils import require_binary, run_cmd


def build_artifact_filter(strength: float) -> str:
    s = max(0.05, min(1.0, strength))
    return (
        f"highpass=f=25,adeclick,adeclip,afftdn=nf={-18 - int(s * 12):d},"
        f"anequalizer=f=3500:t=q:w=1.2:g={-1.5 - s * 2.5:.1f},alimiter=limit=0.97"
    )


def build_noise_filter(strength: float, mode: str = "general") -> str:
    s = max(0.05, min(1.0, strength))
    if mode == "light":
        return f"highpass=f=30,afftdn=nf={-16 - int(s * 10):d},alimiter=limit=0.98"
    if mode == "vocal":
        return (
            f"highpass=f=80,afftdn=nf={-20 - int(s * 14):d},anlmdn=s={6 + int(s * 4):d}:p=0.003,"
            f"acompressor=threshold=-20:ratio=2.5:attack=10:release=120,alimiter=limit=0.97"
        )
    return (
        f"highpass=f=70,afftdn=nf={-18 - int(s * 14):d},anlmdn=s={4 + int(s * 4):d},"
        f"anequalizer=f=60:t=q:w=1:g=-8,anequalizer=f=120:t=q:w=1:g=-5,alimiter=limit=0.97"
    )


def build_enhancer_filter(strength: float) -> str:
    s = max(0.05, min(1.0, strength))
    return (
        f"highpass=f=28,bass=g={0.5 + s * 1.5:.1f}:f=90:w=0.6,"
        f"treble=g={0.5 + s * 2.0:.1f}:f=9000:w=0.45,"
        f"acompressor=threshold={-18 + s * 3:.1f}:ratio={1.5 + s * 0.7:.1f}:attack=14:release=130,"
        f"stereotools=mlev=1.02:slev={1.02 + s * 0.12:.2f},"
        f"loudnorm=I=-14:TP=-1.0:LRA=11,alimiter=limit=0.97"
    )


def process(input_file: Path, output_file: Path, filter_chain: str, dry_run: bool = False) -> str:
    require_binary("ffmpeg")
    cmd = [
        "ffmpeg", "-y", "-hide_banner",
        "-i", str(input_file),
        "-vn", "-af", filter_chain,
        "-c:a", "pcm_s24le",
        str(output_file),
    ]
    return run_cmd(cmd, dry_run)


def convert_to_wav32(input_path: Path, output_path: Path, dry_run: bool = False) -> str:
    require_binary("ffmpeg")
    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(input_path), "-vn", "-acodec", "pcm_f32le", str(output_path)]
    return run_cmd(cmd, dry_run)


def export_audio(input_wav: Path, output_path: Path, fmt: str = "wav", dry_run: bool = False) -> str:
    require_binary("ffmpeg")
    fmt = fmt.lower()
    if fmt == "wav":
        cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(input_wav), "-c:a", "pcm_s24le", str(output_path)]
    elif fmt == "flac":
        cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(input_wav), "-c:a", "flac", str(output_path)]
    elif fmt == "mp3":
        cmd = ["ffmpeg", "-y", "-hide_banner", "-i", str(input_wav), "-c:a", "libmp3lame", "-b:a", "320k", str(output_path)]
    else:
        raise ValueError(f"Unsupported export format: {fmt}")
    return run_cmd(cmd, dry_run)
