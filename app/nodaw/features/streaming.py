from __future__ import annotations

from pathlib import Path
from typing import Any

from ..audio.ffmpeg import FFmpeg
from ..core.models import TrackAnalysis
from ..core.scoring import streaming_compatibility
from ..utils.files import safe_name


def analyze_streaming(
    track: TrackAnalysis,
    source: Path,
    output_dir: Path,
    ffmpeg: FFmpeg,
    settings: dict[str, Any],
    generate_previews: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = streaming_compatibility(track, settings)
    duration = int(settings["previews"].get("duration_seconds", 30))
    for item in results:
        item["preview_path"] = None
        if not generate_previews:
            continue
        platform = item["platform"]
        destination = output_dir / f"{safe_name(source.stem)}_{safe_name(platform)}_preview.m4a"
        audio_filter = (
            f"loudnorm=I={item['target_lufs']}:"
            f"TP={item['true_peak_ceiling_dbtp']}:LRA=11"
        )
        ffmpeg.run_checked([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(source), "-t", str(duration), "-vn",
            "-af", audio_filter, "-c:a", "aac", "-b:a", "256k", str(destination),
        ])
        item["preview_path"] = str(destination)
        item["preview_size_bytes"] = destination.stat().st_size
    return {"platforms": results, "preview_duration_seconds": duration}

