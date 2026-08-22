"""
Shared FFmpeg convert helpers — used by Home Quick Convert and Studio Player.

Production rules:
- Never overwrite the source (caller chooses dest path).
- MP4 = audio-in-MP4 (AAC); falls back if encoder missing.
- Returns structured results for multi-format batch.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


# (fmt_key, label shown in UI)
CONVERT_FORMATS: list[tuple[str, str]] = [
    ("wav", "WAV 24-bit"),
    ("mp3", "MP3 320k"),
    ("flac", "FLAC"),
    ("m4a", "M4A 256k"),
    ("mp4", "MP4 (AAC)"),
    ("ogg", "OGG"),
]


def ffmpeg_bin() -> str:
    return shutil.which("ffmpeg") or "ffmpeg"


def codec_args_for(ext_or_fmt: str) -> list[str]:
    """Return primary ffmpeg audio args for a format key or file extension."""
    key = ext_or_fmt.lower().lstrip(".")
    table: dict[str, list[str]] = {
        "wav": ["-c:a", "pcm_s24le"],
        "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
        "flac": ["-c:a", "flac"],
        # M4A / MP4: force audio-only, AAC, faststart for players
        "m4a": ["-vn", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"],
        "mp4": ["-vn", "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart"],
        "aac": ["-vn", "-c:a", "aac", "-b:a", "256k"],
        "ogg": ["-c:a", "libvorbis", "-q:a", "6"],
        "opus": ["-c:a", "libopus", "-b:a", "160k"],
    }
    return list(table.get(key, ["-c:a", "pcm_s16le"]))


def _fallback_args(fmt: str) -> list[list[str]]:
    """Ordered fallback encoder chains if the primary fails."""
    key = fmt.lower().lstrip(".")
    if key in {"mp4", "m4a", "aac"}:
        return [
            ["-vn", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
            ["-vn", "-c:a", "aac", "-b:a", "128k"],
            # last resort: re-encode via libmp3lame into m4a is wrong; use wav then note fail
        ]
    if key == "mp3":
        return [
            ["-c:a", "libmp3lame", "-b:a", "256k"],
            ["-c:a", "libmp3lame", "-q:a", "2"],
        ]
    if key == "ogg":
        return [["-c:a", "libvorbis", "-q:a", "5"]]
    return []


def convert_one(
    source: Path | str,
    dest: Path | str,
    *,
    timeout: int = 300,
) -> dict[str, Any]:
    """
    Convert source → dest with FFmpeg. Dest extension selects codec.

    Returns {ok, source, dest, fmt, error, command, attempts}.
    """
    src = Path(source).expanduser().resolve()
    out = Path(dest).expanduser().resolve()
    fmt = out.suffix.lower().lstrip(".") or "wav"
    result: dict[str, Any] = {
        "ok": False,
        "source": str(src),
        "dest": str(out),
        "fmt": fmt,
        "error": None,
        "command": None,
        "attempts": 0,
    }
    if not src.is_file():
        result["error"] = f"Source missing: {src}"
        return result
    if src.resolve() == out.resolve():
        result["error"] = "Refusing to overwrite the source file"
        return result
    out.parent.mkdir(parents=True, exist_ok=True)

    chains = [codec_args_for(fmt)] + _fallback_args(fmt)
    last_err = ""
    for args in chains:
        result["attempts"] += 1
        cmd = [ffmpeg_bin(), "-hide_banner", "-y", "-i", str(src), *args, str(out)]
        result["command"] = " ".join(cmd)
        try:
            pr = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            if pr.returncode == 0 and out.is_file() and out.stat().st_size > 64:
                result["ok"] = True
                result["error"] = None
                return result
            last_err = (pr.stderr or pr.stdout or f"exit {pr.returncode}")[-800:]
            # remove partial failed file
            try:
                if out.is_file() and out.stat().st_size < 64:
                    out.unlink(missing_ok=True)
            except Exception:
                pass
        except subprocess.TimeoutExpired:
            last_err = f"Timed out after {timeout}s"
        except FileNotFoundError:
            result["error"] = "FFmpeg not found on PATH (install or use the bundled runtime)."
            return result
        except Exception as exc:
            last_err = str(exc)

    result["error"] = last_err or "Convert failed"
    return result


def default_dest(source: Path, fmt: str, out_dir: Path) -> Path:
    """Unique dest path under out_dir: stem_convert.fmt (with counter if exists)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(source).stem
    fmt = fmt.lower().lstrip(".")
    candidate = out_dir / f"{stem}_convert.{fmt}"
    n = 2
    while candidate.exists():
        candidate = out_dir / f"{stem}_convert_{n}.{fmt}"
        n += 1
    return candidate
