from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._-")
    return cleaned[:120] or "report"


def audio_files(folder: Path, extensions: Iterable[str], recursive: bool = False) -> list[Path]:
    if not folder.exists():
        return []
    allowed = {item.lower() for item in extensions}
    candidates = folder.rglob("*") if recursive else folder.iterdir()
    return sorted(
        (path for path in candidates if path.is_file() and path.suffix.lower() in allowed),
        key=lambda path: path.name.casefold(),
    )


def select_audio(folder: Path, extensions: Iterable[str]) -> Path:
    files = audio_files(folder, extensions)
    if not files:
        raise FileNotFoundError(f"No supported audio files found in {folder}")
    return max(files, key=lambda path: path.stat().st_size)


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())

