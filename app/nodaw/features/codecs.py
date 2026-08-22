from __future__ import annotations

from pathlib import Path
from typing import Any

from ..audio.ffmpeg import FFmpeg
from ..core.models import TrackAnalysis
from ..core.scoring import codec_recommendations
from ..utils.files import safe_name


def analyze_codecs(
    track: TrackAnalysis,
    source: Path,
    output_dir: Path,
    ffmpeg: FFmpeg,
    settings: dict[str, Any],
    generate_previews: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = int(settings["previews"].get("duration_seconds", 30))
    previews = []
    for profile in settings["previews"]["codec_profiles"]:
        encoder = str(profile["codec"])
        available = ffmpeg.encoder_available(encoder)
        item = {
            "name": profile["name"],
            "encoder": encoder,
            "available": available,
            "status": "not generated",
            "path": None,
            "size_bytes": None,
        }
        if generate_previews and available:
            destination = (
                output_dir
                / f"{safe_name(source.stem)}_{safe_name(profile['name'])}{profile['extension']}"
            )
            arguments = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(source),
                "-t",
                str(duration),
                "-vn",
                "-c:a",
                encoder,
                *[str(value) for value in profile.get("args", [])],
                str(destination),
            ]
            ffmpeg.run_checked(arguments)
            item.update(
                status="generated", path=str(destination), size_bytes=destination.stat().st_size
            )
        elif not available:
            item["status"] = "encoder unavailable"
        previews.append(item)

    return {
        "source": codec_recommendations(track),
        "preview_duration_seconds": duration,
        "previews": previews,
    }
