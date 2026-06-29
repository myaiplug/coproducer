from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from ..core.models import TrackAnalysis
from ..core.scoring import evaluate_track
from .reference import difference


def track_row(path: Path, track: TrackAnalysis, settings: dict[str, Any]) -> dict[str, Any]:
    score, report_rating, _, _ = evaluate_track(track, settings)
    return {
        "file": path.name,
        "score": score,
        "rating": report_rating,
        "integrated_lufs": track.metrics.loudness.integrated_lufs,
        "true_peak_dbtp": track.metrics.loudness.true_peak_dbtp,
        "dynamic_range_db": track.metrics.dynamic_range_db,
        "stereo_width_percent": track.metrics.stereo_width_percent,
        "phase_correlation": track.metrics.phase_correlation,
        "noise_floor_dbfs": track.metrics.noise_floor_dbfs,
        "clipping_estimate": track.metrics.clipped_samples_estimate,
        "codec": track.audio.codec_name,
        "sample_rate_hz": track.audio.sample_rate_hz,
    }


def album_consistency(
    analyzed: list[tuple[Path, TrackAnalysis]],
    settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, float | None], int]:
    rows = [track_row(path, track, settings) for path, track in analyzed]
    lufs_values = [row["integrated_lufs"] for row in rows if row["integrated_lufs"] is not None]
    peak_values = [row["true_peak_dbtp"] for row in rows if row["true_peak_dbtp"] is not None]
    dynamic_values = [row["dynamic_range_db"] for row in rows if row["dynamic_range_db"] is not None]
    medians = {
        "integrated_lufs": statistics.median(lufs_values) if lufs_values else None,
        "true_peak_dbtp": statistics.median(peak_values) if peak_values else None,
        "dynamic_range_db": statistics.median(dynamic_values) if dynamic_values else None,
    }
    penalty = 0
    for row in rows:
        row["lufs_delta"] = difference(row["integrated_lufs"], medians["integrated_lufs"])
        row["true_peak_delta"] = difference(row["true_peak_dbtp"], medians["true_peak_dbtp"])
        row["dynamic_range_delta"] = difference(row["dynamic_range_db"], medians["dynamic_range_db"])
        if row["lufs_delta"] is not None and abs(row["lufs_delta"]) > 1.5:
            penalty += 6
        if row["true_peak_delta"] is not None and abs(row["true_peak_delta"]) > 1:
            penalty += 4
        if row["dynamic_range_delta"] is not None and abs(row["dynamic_range_delta"]) > 2:
            penalty += 4
    return rows, medians, max(0, 100 - penalty)

