"""
Per-metric quality bands - same 4-tier color language as overall mix score.

Returns a score-like 0-100 for color mapping via score_color(), plus a band name.
Thresholds follow streaming / mastering practice (Spotify ~−14 LUFS, TP ≤ −1 dBTP).
"""

from __future__ import annotations

from typing import Any

from .theme import Color, score_color

# band labels for tooltips; color always from score_color(pseudo_score)
Band = str


def _band_from_score(s: int | None) -> Band:
    if s is None:
        return "neutral"
    if s >= 90:
        return "excellent"
    if s >= 75:
        return "good"
    if s >= 60:
        return "fair"
    return "poor"


def band_color(band: Band) -> str:
    """Map band → same continuous scale midpoints (unified algorithm)."""
    mid = {"excellent": 95, "good": 80, "fair": 62, "poor": 30, "neutral": None}.get(band)
    if mid is None:
        return Color.MUTED
    return score_color(mid)


def score_for_band(band: Band) -> int | None:
    return {
        "excellent": 95,
        "good": 80,
        "fair": 65,
        "poor": 40,
        "neutral": None,
    }.get(band)


def metric_status(kind: str, value: Any) -> tuple[Band, int | None, str]:
    """
    Evaluate a metric.
    Returns (band, pseudo_score 0-100|None, hint).
    """
    if value is None or value == "" or value == "-":
        return "neutral", None, ""

    try:
        v = float(value)
    except (TypeError, ValueError):
        # categorical
        s = str(value).lower()
        if s in ("good", "pass", "ok", "excellent"):
            return "excellent", 95, ""
        if s in ("warn", "warning", "fair"):
            return "fair", 65, ""
        if s in ("fail", "bad", "poor", "critical"):
            return "poor", 40, ""
        return "neutral", None, ""

    k = kind.lower()

    # Integrated LUFS - streaming target ~-14 (Spotify/YT), Apple ~-16
    if k in ("lufs", "integrated_lufs", "loudness"):
        # distance from -14
        dist = abs(v - (-14.0))
        if dist <= 1.0:
            return "excellent", 96, "On streaming target"
        if dist <= 2.5:
            return "good", 82, "Close to −14 LUFS"
        if dist <= 5.0:
            return "fair", 66, "Loudness off target"
        return "poor", 38, "Far from streaming loudness"

    # True peak dBTP
    if k in ("tp", "true_peak", "true_peak_dbtp"):
        if v <= -1.0:
            return "excellent", 95, "Safe intersample headroom"
        if v <= -0.1:
            return "good", 80, "Acceptable peak"
        if v <= 0.0:
            return "fair", 62, "At digital full scale"
        return "poor", 30, "True-peak overshoot / clip risk"

    # Sample peak dBFS
    if k in ("peak", "peak_dbfs"):
        if v <= -1.0:
            return "excellent", 94, ""
        if v <= -0.3:
            return "good", 80, ""
        if v <= 0.0:
            return "fair", 60, ""
        return "poor", 28, "Clipping risk"

    # Loudness range LU - musical dynamics
    if k in ("lra", "loudness_range", "loudness_range_lu"):
        if 4.0 <= v <= 12.0:
            return "excellent", 92, "Healthy dynamics"
        if 2.0 <= v < 4.0 or 12.0 < v <= 16.0:
            return "good", 78, ""
        if 1.0 <= v < 2.0 or 16.0 < v <= 20.0:
            return "fair", 64, "Compressed or very dynamic"
        return "poor", 42, "Extreme LRA"

    # RMS dBFS
    if k in ("rms", "rms_dbfs"):
        if -18.0 <= v <= -10.0:
            return "excellent", 90, ""
        if -22.0 <= v < -18.0 or -10.0 < v <= -8.0:
            return "good", 78, ""
        if -26.0 <= v < -22.0 or -8.0 < v <= -6.0:
            return "fair", 64, ""
        return "poor", 40, "Very quiet or very hot"

    # Crest factor (ratio, not always dB - engine may store ratio)
    if k in ("crest", "crest_factor"):
        # If looks like ratio (e.g. 3-20) treat as ratio; if negative-ish dB skip
        if v > 50:  # maybe already scaled weird
            return "neutral", None, ""
        if 6.0 <= v <= 14.0:
            return "excellent", 90, "Good peak-to-RMS"
        if 4.0 <= v < 6.0 or 14.0 < v <= 18.0:
            return "good", 78, ""
        if 2.5 <= v < 4.0 or 18.0 < v <= 24.0:
            return "fair", 62, "Over-limited or very peaky"
        return "poor", 40, ""

    # Dynamic range dB
    if k in ("dr", "dynamic_range", "dynamic_range_db"):
        if v >= 10:
            return "excellent", 92, ""
        if v >= 7:
            return "good", 80, ""
        if v >= 5:
            return "fair", 65, ""
        return "poor", 42, "Heavily limited"

    # Stereo width %
    if k in ("width", "stereo_width", "stereo_width_percent"):
        if 40 <= v <= 90:
            return "excellent", 90, ""
        if 25 <= v < 40 or 90 < v <= 100:
            return "good", 78, ""
        if 10 <= v < 25:
            return "fair", 62, "Narrow image"
        return "poor", 40, "Mono or extreme"

    # Phase correlation −1..1
    if k in ("phase", "phase_correlation"):
        if v >= 0.5:
            return "excellent", 94, "Mono-compatible"
        if v >= 0.2:
            return "good", 80, ""
        if v >= 0.0:
            return "fair", 62, "Some out-of-phase content"
        return "poor", 30, "Phase issues / mono cancel"

    # Noise floor dBFS (lower more negative = quieter floor = better)
    if k in ("noise", "noise_floor", "noise_floor_dbfs"):
        if v <= -60:
            return "excellent", 94, ""
        if v <= -50:
            return "good", 80, ""
        if v <= -40:
            return "fair", 64, ""
        return "poor", 40, "Noisy floor"

    # Clipped samples
    if k in ("clip", "clipped", "clipped_samples", "clipped_samples_estimate"):
        if v <= 0:
            return "excellent", 98, ""
        if v < 50:
            return "good", 78, "Minor near-clip"
        if v < 500:
            return "fair", 60, ""
        return "poor", 28, "Significant clipping"

    # Silence ratio 0-1
    if k in ("silence", "silence_ratio"):
        if v <= 0.05:
            return "excellent", 92, ""
        if v <= 0.15:
            return "good", 80, ""
        if v <= 0.35:
            return "fair", 64, ""
        return "poor", 40, "Long silence / sparse"

    # DC offset absolute
    if k in ("dc", "dc_offset"):
        av = abs(v)
        if av < 0.001:
            return "excellent", 96, ""
        if av < 0.01:
            return "good", 80, ""
        if av < 0.05:
            return "fair", 62, ""
        return "poor", 35, "DC offset"

    # Tempo BPM - any musical tempo is fine
    if k in ("tempo", "tempo_bpm", "bpm"):
        if 60 <= v <= 180:
            return "excellent", 90, ""
        if 40 <= v < 60 or 180 < v <= 220:
            return "good", 78, ""
        return "fair", 65, ""

    # Spectral centroid Hz - brightness proxy
    if k in ("centroid", "spectral_centroid", "spectral_centroid_hz", "brightness"):
        if 1500 <= v <= 4500:
            return "excellent", 88, "Balanced brightness"
        if 800 <= v < 1500 or 4500 < v <= 7000:
            return "good", 76, ""
        if v < 800:
            return "fair", 64, "Dark / muffled"
        return "fair", 64, "Very bright / harsh risk"

    # Sample rate
    if k in ("sr", "sample_rate", "sample_rate_hz"):
        if v >= 44100:
            return "excellent", 95, ""
        if v >= 32000:
            return "fair", 65, ""
        return "poor", 40, "Low sample rate"

    # Bit depth
    if k in ("bit_depth", "bits"):
        if v >= 24:
            return "excellent", 95, ""
        if v >= 16:
            return "good", 82, ""
        return "fair", 60, ""

    # Generic: leave neutral (no false red/green)
    return "neutral", None, ""


def value_color(kind: str, value: Any) -> str:
    band, pseudo, _ = metric_status(kind, value)
    if pseudo is not None:
        return score_color(pseudo)
    return band_color(band)
