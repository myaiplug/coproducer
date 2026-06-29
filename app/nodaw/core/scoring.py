from __future__ import annotations

from typing import Any

from .models import Finding, TrackAnalysis


LOSSY_CODECS = {"mp3", "aac", "vorbis", "opus", "wmapro", "wmav2"}


def rating(score: int) -> str:
    if score >= 90:
        return "Release ready"
    if score >= 75:
        return "Good with minor corrections"
    if score >= 60:
        return "Usable after technical corrections"
    if score >= 40:
        return "Major corrections required"
    return "Not release ready"


def evaluate_track(track: TrackAnalysis, settings: dict[str, Any]) -> tuple[int, str, str, list[Finding]]:
    target = float(settings["analysis"]["target_lufs"])
    ceiling = float(settings["analysis"]["true_peak_ceiling_dbtp"])
    minimum_rate = int(settings["analysis"]["minimum_sample_rate_hz"])
    metrics = track.metrics
    findings: list[Finding] = []

    def add(severity: str, title: str, message: str, action: str, penalty: int) -> None:
        findings.append(Finding(severity, title, message, action, penalty))

    lufs = metrics.loudness.integrated_lufs
    if lufs is None:
        add("warning", "Loudness unavailable", "Integrated LUFS could not be measured.", "Verify the source file and FFmpeg build.", 10)
    elif lufs > -7:
        add("critical", "Excessive loudness", f"Measured {lufs:.1f} LUFS.", "Reduce limiter drive and restore transient headroom.", 20)
    elif lufs > target + 3:
        add("warning", "Loud for streaming", f"Measured {lufs:.1f} LUFS versus the {target:.1f} LUFS baseline.", "Reduce mastering gain or accept platform attenuation.", 10)
    elif lufs < target - 5:
        add("notice", "Low program loudness", f"Measured {lufs:.1f} LUFS.", "Review level and dynamics before release.", 5)

    true_peak = metrics.loudness.true_peak_dbtp
    if true_peak is None:
        add("warning", "True peak unavailable", "True peak could not be measured.", "Run the dependency check and reanalyze.", 8)
    elif true_peak > ceiling:
        add("warning", "Unsafe true peak", f"Measured {true_peak:.2f} dBTP; ceiling is {ceiling:.2f} dBTP.", "Use a true-peak limiter before lossy encoding.", 14)

    if metrics.clipped_samples_estimate:
        add("critical", "Probable clipping", f"At least {metrics.clipped_samples_estimate} full-scale peak sample(s) were detected.", "Return to the unclipped mix when possible; limiting cannot restore clipped transients.", 18)
    if metrics.dynamic_range_db is not None and metrics.dynamic_range_db < 5:
        add("warning", "Restricted dynamics", f"Crest-based dynamic range is {metrics.dynamic_range_db:.1f} dB.", "Reduce compression or limiting.", 10)
    if metrics.phase_correlation is not None and metrics.phase_correlation < 0:
        add("critical", "Negative phase correlation", f"Average correlation is {metrics.phase_correlation:.2f}.", "Correct polarity and stereo processing before release.", 18)
    elif metrics.phase_correlation is not None and metrics.phase_correlation < 0.2:
        add("warning", "Weak mono compatibility", f"Average correlation is {metrics.phase_correlation:.2f}.", "Audit the mix in mono and reduce out-of-phase widening.", 9)
    if metrics.stereo_width_percent is not None and metrics.stereo_width_percent > 120:
        add("warning", "Extreme stereo width", f"Side-to-mid energy is {metrics.stereo_width_percent:.1f}%.", "Reduce widening and verify low-frequency mono compatibility.", 8)
    if track.audio.sample_rate_hz and track.audio.sample_rate_hz < minimum_rate:
        add("warning", "Low sample rate", f"Source rate is {track.audio.sample_rate_hz} Hz.", f"Use a source at {minimum_rate} Hz or higher.", 8)
    if track.audio.codec_name.casefold() in LOSSY_CODECS:
        add("notice", "Lossy source", f"Source codec is {track.audio.codec_name}.", "Analyze and master from a lossless WAV or FLAC source.", 4)
    if metrics.noise_floor_dbfs is not None and metrics.noise_floor_dbfs > -45:
        add("notice", "Elevated noise floor", f"Estimated noise floor is {metrics.noise_floor_dbfs:.1f} dBFS.", "Inspect quiet sections for noise, hum, or room tone.", 5)

    penalty = sum(item.score_penalty for item in findings)
    score = max(0, min(100, 100 - penalty))
    if not findings:
        findings.append(Finding("pass", "No blocking technical defects", "The measured file is within configured release thresholds.", "Perform a final listening and metadata review.", 0))
    blockers = sum(1 for item in findings if item.severity in {"warning", "critical"})
    summary = (
        f"{track.audio.file_name} scored {score}/100. "
        f"{blockers} release-blocking technical finding(s) were identified. "
        "Use the measurements as engineering guidance and confirm decisions by listening."
    )
    return score, rating(score), summary, findings


def codec_recommendations(track: TrackAnalysis) -> dict[str, Any]:
    codec = track.audio.codec_name.casefold()
    lossless = codec not in LOSSY_CODECS
    return {
        "source_class": "lossless or uncompressed" if lossless else "lossy compressed",
        "source_suitable_for_mastering": lossless,
        "archival_master": "24-bit PCM WAV at the native sample rate",
        "lossless_delivery": "FLAC at the native sample rate",
        "streaming_delivery": "Upload the lossless master; let the platform encode delivery formats",
        "warning": None if lossless else "Do not use a lossy file as the mastering source when a lossless source exists.",
    }


def streaming_compatibility(track: TrackAnalysis, settings: dict[str, Any]) -> list[dict[str, Any]]:
    current_lufs = track.metrics.loudness.integrated_lufs
    current_peak = track.metrics.loudness.true_peak_dbtp
    results = []
    for platform, profile in settings["streaming_profiles"].items():
        target = float(profile["target_lufs"])
        ceiling = float(profile["true_peak_dbtp"])
        gain = round(target - current_lufs, 2) if current_lufs is not None else None
        projected_peak = round(current_peak + gain, 2) if current_peak is not None and gain is not None else None
        ready = gain is not None and abs(gain) <= 2 and projected_peak is not None and projected_peak <= ceiling
        results.append({
            "platform": platform,
            "target_lufs": target,
            "true_peak_ceiling_dbtp": ceiling,
            "estimated_gain_db": gain,
            "projected_true_peak_dbtp": projected_peak,
            "status": "ready" if ready else "adjustment recommended",
        })
    return results

