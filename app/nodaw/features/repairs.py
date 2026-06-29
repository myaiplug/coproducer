from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.models import RepairRecommendation, TrackAnalysis
from ..utils.files import safe_name


def build_repairs(track: TrackAnalysis, settings: dict[str, Any], output_dir: Path) -> list[RepairRecommendation]:
    metrics = track.metrics
    target = float(settings["analysis"]["target_lufs"])
    ceiling = float(settings["analysis"]["true_peak_ceiling_dbtp"])
    filters: list[str] = []
    reasons: list[str] = []
    cautions: list[str] = []

    lufs = metrics.loudness.integrated_lufs
    peak = metrics.loudness.true_peak_dbtp
    if lufs is not None and (abs(lufs - target) > 1.0 or (peak is not None and peak > ceiling)):
        filters.append(f"loudnorm=I={target}:TP={ceiling}:LRA=11")
        reasons.append("Loudness or true peak is outside the configured delivery target.")
    elif peak is not None and peak > ceiling:
        filters.append(f"alimiter=limit={10 ** (ceiling / 20):.4f}")
        reasons.append("True peak exceeds the configured ceiling.")
    if metrics.noise_floor_dbfs is not None and metrics.noise_floor_dbfs > -40:
        filters.insert(0, "highpass=f=25")
        reasons.append("A conservative high-pass filter may reduce subsonic contamination.")
    if metrics.clipped_samples_estimate:
        cautions.append("The source appears clipped. This command controls output level but cannot reconstruct clipped transients.")
    if metrics.phase_correlation is not None and metrics.phase_correlation < 0:
        cautions.append("Negative phase correlation requires mix-level correction; no automatic stereo repair is applied.")

    if not filters:
        filters = ["anull"]
        reasons.append("No automatic correction is required by the configured thresholds.")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{safe_name(Path(track.audio.file_name).stem)}_repaired.wav"
    filter_chain = ",".join(filters)
    command = f'ffmpeg -y -i "{track.audio.path}" -af "{filter_chain}" "{output}"'
    return [RepairRecommendation(
        title="Conservative delivery repair",
        reason=" ".join(reasons),
        ffmpeg_filter=filter_chain,
        command=command,
        caution=" ".join(cautions) or "Review the rendered file by ear before replacing any master.",
    )]


def write_repair_launcher(repairs: list[RepairRecommendation], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    commands = "\n".join(item.command for item in repairs)
    content = (
        "@echo off\n"
        "setlocal EnableExtensions\n"
        "title NoDAW Audio Quality Analyzer PRO - Apply Repair\n"
        "where ffmpeg >nul 2>nul || (echo [ERROR] FFmpeg is not on PATH.& exit /b 1)\n"
        f"{commands}\n"
        "if errorlevel 1 exit /b %errorlevel%\n"
        "echo Repair export completed. Review the output before release.\n"
    )
    destination.write_text(content, encoding="utf-8")
    return destination


def build_reference_repairs(
    user: TrackAnalysis,
    reference: TrackAnalysis,
    output_dir: Path,
) -> list[RepairRecommendation]:
    filters: list[str] = []
    reasons: list[str] = []
    centers = {
        "sub_bass": 45,
        "bass": 100,
        "low_mid": 250,
        "mid": 800,
        "presence": 2500,
        "high": 7000,
        "air": 13000,
    }
    for band, user_value in user.metrics.spectral_balance_db.items():
        reference_value = reference.metrics.spectral_balance_db.get(band)
        if user_value is None or reference_value is None:
            continue
        difference = user_value - reference_value
        if abs(difference) < 2:
            continue
        gain = max(-2.5, min(2.5, round(-difference * 0.35, 2)))
        if abs(gain) >= 0.5 and band in centers:
            filters.append(f"equalizer=f={centers[band]}:t=q:w=1:g={gain}")
            reasons.append(f"{band.replace('_', ' ')} differs from the reference by {difference:.1f} dB.")
    user_lufs = user.metrics.loudness.integrated_lufs
    reference_lufs = reference.metrics.loudness.integrated_lufs
    reference_peak = reference.metrics.loudness.true_peak_dbtp
    if user_lufs is not None and reference_lufs is not None and abs(user_lufs - reference_lufs) > 1:
        target = max(-18.0, min(-8.0, reference_lufs))
        ceiling = min(-1.0, reference_peak if reference_peak is not None else -1.0)
        filters.append(f"loudnorm=I={target}:TP={ceiling}:LRA=11")
        reasons.append(f"Program loudness differs from the reference by {user_lufs - reference_lufs:.1f} LU.")
    if not filters:
        filters.append("anull")
        reasons.append("No conservative automatic correction is required for the measured differences.")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{safe_name(Path(user.audio.file_name).stem)}_reference_matched.wav"
    chain = ",".join(filters)
    command = f'ffmpeg -y -i "{user.audio.path}" -af "{chain}" "{output}"'
    return [RepairRecommendation(
        title="Conservative reference-match repair",
        reason=" ".join(reasons),
        ffmpeg_filter=chain,
        command=command,
        caution="Reference matching is not mastering. Compare the result by ear and retain the original source.",
    )]

