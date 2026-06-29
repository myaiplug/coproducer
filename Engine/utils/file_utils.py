# -*- coding: utf-8 -*-
"""Engine/utils/file_utils.py — File discovery, FFprobe metadata, JSON helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .audio_utils import SUPPORTED_EXTS


def which_or_none(name: str) -> str | None:
    return shutil.which(name)


def require_binary(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"{name} not found on PATH.")
    return p


def collect_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted([p for p in input_path.rglob("*") if p.suffix.lower() in SUPPORTED_EXTS])
    raise FileNotFoundError(str(input_path))


def ffprobe_json(path: Path) -> dict:
    if not which_or_none("ffprobe"):
        return {}
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration,bit_rate,size:stream=codec_type,codec_name,sample_rate,channels,bits_per_sample",
        "-of", "json", str(path),
    ]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode != 0:
        return {}
    try:
        return json.loads(p.stdout)
    except Exception:
        return {}


def run_cmd(cmd: list[str], dry_run: bool = False) -> str:
    printable = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    print(f"[Engine] {printable}")
    if dry_run:
        return printable
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed: {printable}\n{p.stdout}")
    return printable


def save_json(path: Path, data: Any):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
