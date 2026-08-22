"""
CoProducer technical readiness scoring.

Design rules (v3.2 scoring engine):
- Score measures *technical delivery readiness*, not artistic quality.
- Penalties are graduated (smooth), not cliff-edged, so ±0.01 dB never costs 14 points.
- Successful technical corrections must never *reduce* readiness: call
  ``floor_score_after_repair`` after a CoProducer repair re-analysis.
- Automated technical ceiling remains 97 (100 reserved for human cert).
"""

from __future__ import annotations

import math
from typing import Any

from .models import Finding, TrackAnalysis

LOSSY_CODECS = {"mp3", "aac", "vorbis", "opus", "wmapro", "wmav2", "mp2", "mp3float"}

# True-peak measurement noise / loudnorm residual tolerance (dB)
TP_EPSILON_DB = 0.15
# Automated technical score ceiling
TECHNICAL_SCORE_CEILING = 97


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


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _smooth_penalty(
    excess: float,
    *,
    start: float,
    full: float,
    max_penalty: float,
) -> float:
    """
    Graduated penalty for a continuous excess metric.

    excess <= start → 0
    excess >= full  → max_penalty
    linear ramp between.
    """
    if excess is None or not math.isfinite(excess) or excess <= start:
        return 0.0
    if excess >= full:
        return float(max_penalty)
    span = max(full - start, 1e-9)
    return float(max_penalty) * (excess - start) / span


def evaluate_track(
    track: TrackAnalysis, settings: dict[str, Any]
) -> tuple[int, str, str, list[Finding]]:
    """
    Score a track with multi-component graduated penalties.

    Components (implicit via findings):
    - Loudness fit to streaming target
    - True-peak / sample-peak safety
    - Clipping / dynamics
    - Stereo integrity
    - Source integrity (lossy, sample rate)
    - Noise floor (only when true silence windows exist)
    """
    target = float(settings["analysis"]["target_lufs"])
    ceiling = float(settings["analysis"]["true_peak_ceiling_dbtp"])
    minimum_rate = int(settings["analysis"]["minimum_sample_rate_hz"])
    metrics = track.metrics
    findings: list[Finding] = []

    def add(
        severity: str,
        title: str,
        message: str,
        action: str,
        penalty: float,
    ) -> None:
        pen = int(round(max(0.0, penalty)))
        if pen <= 0 and severity not in {"pass", "notice"}:
            # Keep informational findings even with zero rounded penalty
            pen = 0
        findings.append(Finding(severity, title, message, action, pen))

    # --- Loudness ---------------------------------------------------------
    lufs = metrics.loudness.integrated_lufs
    if lufs is None:
        add(
            "warning",
            "Loudness unavailable",
            "Integrated LUFS could not be measured.",
            "Verify the source file and FFmpeg build.",
            10,
        )
    else:
        delta = lufs - target  # + = louder than target
        if lufs > -6.5:
            # Brickwalled / destroyed dynamics territory
            add(
                "critical",
                "Excessive loudness",
                f"Measured {lufs:.1f} LUFS (target {target:.1f}).",
                "Reduce limiter drive and restore transient headroom.",
                _smooth_penalty(lufs - (-6.5), start=0.0, full=4.0, max_penalty=22),
            )
        elif delta > 1.0:
            # Hot for streaming — graduated, not a cliff at +3
            pen = _smooth_penalty(delta, start=1.0, full=6.0, max_penalty=14)
            sev = "critical" if delta > 4.0 else "warning" if delta > 2.0 else "notice"
            add(
                sev,
                "Loud for streaming",
                f"Measured {lufs:.1f} LUFS versus the {target:.1f} LUFS baseline "
                f"({delta:+.1f} LU).",
                "Reduce mastering gain or accept platform attenuation.",
                pen,
            )
        elif delta < -2.0:
            # Quiet is softer than hot — lower max penalty, graduated
            # Never punish intentional quiet masters as hard as hot ones.
            under = abs(delta)
            pen = _smooth_penalty(under, start=2.0, full=10.0, max_penalty=8)
            sev = "notice" if under < 6.0 else "warning"
            add(
                sev,
                "Low program loudness",
                f"Measured {lufs:.1f} LUFS ({under:.1f} LU below {target:.1f}).",
                "Raise level or accept a quieter delivery; optional loudnorm to target.",
                pen,
            )

    # --- True peak / sample peak ------------------------------------------
    true_peak = metrics.loudness.true_peak_dbtp
    peak = metrics.peak_dbfs
    if true_peak is None and peak is None:
        add(
            "warning",
            "True peak unavailable",
            "True peak could not be measured.",
            "Run the dependency check and reanalyze.",
            8,
        )
    else:
        # Use the hotter of true-peak and sample-peak for safety scoring
        hot = true_peak if true_peak is not None else peak
        if peak is not None and true_peak is not None:
            hot = max(true_peak, peak)
        assert hot is not None
        over = hot - ceiling
        if over > TP_EPSILON_DB:
            pen = _smooth_penalty(over, start=TP_EPSILON_DB, full=2.5, max_penalty=16)
            sev = "critical" if over > 1.0 else "warning"
            add(
                sev,
                "Unsafe true peak",
                f"Measured {hot:.2f} dBTP; ceiling is {ceiling:.2f} dBTP "
                f"(tolerance {TP_EPSILON_DB:.2f} dB).",
                "Use a true-peak limiter before lossy encoding.",
                pen,
            )
        # Soft near-ceiling notice (within epsilon but above ceiling) — no score hit
        elif over > 0:
            add(
                "pass",
                "True peak within measurement tolerance",
                f"Measured {hot:.2f} dBTP vs ceiling {ceiling:.2f} dBTP "
                f"(within {TP_EPSILON_DB:.2f} dB measurement tolerance).",
                "No action required for technical delivery.",
                0,
            )

    # --- Clipping ---------------------------------------------------------
    clips = int(metrics.clipped_samples_estimate or 0)
    if clips > 0:
        # Graduated: a few samples ≠ same as brickwall clipping
        pen = _smooth_penalty(float(clips), start=1.0, full=500.0, max_penalty=18)
        if clips < 8:
            pen = min(pen, 4.0)
            sev = "notice"
        elif clips < 64:
            sev = "warning"
        else:
            sev = "critical"
        add(
            sev,
            "Probable clipping",
            f"At least {clips} full-scale peak sample(s) were detected.",
            "Return to the unclipped mix when possible; limiting cannot restore clipped transients.",
            pen,
        )

    # --- Dynamics ---------------------------------------------------------
    if metrics.dynamic_range_db is not None and metrics.dynamic_range_db < 6.0:
        under = 6.0 - metrics.dynamic_range_db
        pen = _smooth_penalty(under, start=0.0, full=4.0, max_penalty=10)
        sev = "warning" if metrics.dynamic_range_db < 4.5 else "notice"
        add(
            sev,
            "Restricted dynamics",
            f"Crest-based dynamic range is {metrics.dynamic_range_db:.1f} dB.",
            "Reduce compression or limiting.",
            pen,
        )

    # --- Stereo / phase ---------------------------------------------------
    if metrics.phase_correlation is not None and metrics.phase_correlation < 0:
        add(
            "critical",
            "Negative phase correlation",
            f"Average correlation is {metrics.phase_correlation:.2f}.",
            "Correct polarity and stereo processing before release.",
            _smooth_penalty(
                -metrics.phase_correlation, start=0.0, full=0.5, max_penalty=18
            ),
        )
    elif metrics.phase_correlation is not None and metrics.phase_correlation < 0.25:
        under = 0.25 - metrics.phase_correlation
        pen = _smooth_penalty(under, start=0.0, full=0.25, max_penalty=9)
        add(
            "warning",
            "Weak mono compatibility",
            f"Average correlation is {metrics.phase_correlation:.2f}.",
            "Audit the mix in mono and reduce out-of-phase widening.",
            pen,
        )

    if metrics.stereo_width_percent is not None and metrics.stereo_width_percent > 115:
        over = metrics.stereo_width_percent - 115.0
        pen = _smooth_penalty(over, start=0.0, full=40.0, max_penalty=8)
        add(
            "warning",
            "Extreme stereo width",
            f"Side-to-mid energy is {metrics.stereo_width_percent:.1f}%.",
            "Reduce widening and verify low-frequency mono compatibility.",
            pen,
        )

    # --- Sample rate ------------------------------------------------------
    if track.audio.sample_rate_hz and track.audio.sample_rate_hz < minimum_rate:
        add(
            "warning",
            "Low sample rate",
            f"Source rate is {track.audio.sample_rate_hz} Hz.",
            f"Use a source at {minimum_rate} Hz or higher.",
            8,
        )

    # --- Source class (informational, soft) -------------------------------
    codec = (track.audio.codec_name or "").casefold()
    if codec in LOSSY_CODECS:
        add(
            "notice",
            "Lossy source",
            f"Source codec is {track.audio.codec_name}.",
            "Analyze and master from a lossless WAV or FLAC source when available.",
            3,  # softer than before (was 4) — advisory, not a hard gate
        )

    # --- Noise floor (only when credible silence exists) ------------------
    # Analyzer returns -90 when no true silence window; only score elevated floors.
    nf = metrics.noise_floor_dbfs
    if nf is not None and -85.0 < nf <= -38.0:
        # Elevated but real silence — graduated
        over = nf - (-55.0)  # start mild at -55
        if over > 0:
            pen = _smooth_penalty(over, start=0.0, full=15.0, max_penalty=6)
            add(
                "notice",
                "Elevated noise floor",
                f"Estimated noise floor is {nf:.1f} dBFS.",
                "Inspect quiet sections for noise, hum, or room tone.",
                pen,
            )
    elif nf is not None and nf > -38.0 and nf > -85.0:
        # Very high floor — denser program or real noise
        pen = _smooth_penalty(nf - (-38.0), start=0.0, full=12.0, max_penalty=8)
        add(
            "warning",
            "High noise floor",
            f"Estimated noise floor is {nf:.1f} dBFS.",
            "Inspect quiet sections; dense masters may also read high.",
            pen,
        )

    # --- Aggregate --------------------------------------------------------
    penalty = sum(item.score_penalty for item in findings)
    score = int(max(0, min(100, 100 - penalty)))

    if not findings:
        findings.append(
            Finding(
                "pass",
                "No blocking technical defects",
                "The measured file is within configured release thresholds.",
                "Perform a final listening and metadata review.",
                0,
            )
        )

    if score > TECHNICAL_SCORE_CEILING:
        score = TECHNICAL_SCORE_CEILING
        findings.append(
            Finding(
                "notice",
                "Technical score ceiling",
                f"Automated technical score is capped at {TECHNICAL_SCORE_CEILING}/100. "
                "A 100 is reserved for human-certified release quality and is not awarded by measurement alone.",
                "Do a final critical listen on multiple systems (and check metadata) before release.",
                0,
            )
        )

    blockers = sum(1 for item in findings if item.severity in {"warning", "critical"})
    summary = (
        f"{track.audio.file_name} scored {score}/100. "
        f"{blockers} release-blocking technical finding(s) were identified. "
        "Use the measurements as engineering guidance and confirm decisions by listening."
    )
    return score, rating(score), summary, findings


def floor_score_after_repair(
    pre_score: int | None,
    post_score: int | None,
    *,
    findings: list[Finding] | None = None,
    applied_filters: str | None = None,
) -> tuple[int, list[Finding]]:
    """
    Guarantee: a successful CoProducer technical repair never *lowers* score.

    Returns (floored_score, findings_with_optional_floor_note).
    """
    out_findings = list(findings or [])
    if pre_score is None or post_score is None:
        return int(post_score if post_score is not None else pre_score or 0), out_findings
    pre_i = int(pre_score)
    post_i = int(post_score)
    if post_i >= pre_i:
        return post_i, out_findings
    # Floor at pre-repair readiness; annotate why
    filt = (applied_filters or "technical repair").strip()
    out_findings.append(
        Finding(
            "notice",
            "Repair score floor applied",
            f"Raw re-score after repair was {post_i}/100, below pre-repair {pre_i}/100. "
            f"Displayed score is floored at {pre_i} because a successful technical repair "
            f"must not reduce readiness (applied: {filt}). "
            "Residual measurement noise (true-peak epsilon, loudnorm single-pass residuals) "
            "can create false drops; the floor protects against that.",
            "If the raw post-repair measurement looks wrong, re-run analysis or check FFmpeg loudnorm two-pass output.",
            0,
        )
    )
    return pre_i, out_findings


def codec_recommendations(track: TrackAnalysis) -> dict[str, Any]:
    codec = track.audio.codec_name.casefold()
    lossless = codec not in LOSSY_CODECS
    return {
        "source_class": "lossless or uncompressed" if lossless else "lossy compressed",
        "source_suitable_for_mastering": lossless,
        "archival_master": "24-bit PCM WAV at the native sample rate",
        "lossless_delivery": "FLAC at the native sample rate",
        "streaming_delivery": "Upload the lossless master; let the platform encode delivery formats",
        "warning": None
        if lossless
        else "Do not use a lossy file as the mastering source when a lossless source exists.",
    }


def streaming_compatibility(track: TrackAnalysis, settings: dict[str, Any]) -> list[dict[str, Any]]:
    current_lufs = track.metrics.loudness.integrated_lufs
    current_peak = track.metrics.loudness.true_peak_dbtp
    results = []
    for platform, profile in settings["streaming_profiles"].items():
        target = float(profile["target_lufs"])
        ceiling = float(profile["true_peak_dbtp"])
        gain = round(target - current_lufs, 2) if current_lufs is not None else None
        projected_peak = (
            round(current_peak + gain, 2) if current_peak is not None and gain is not None else None
        )
        ready = (
            gain is not None
            and abs(gain) <= 2
            and projected_peak is not None
            and projected_peak <= ceiling + TP_EPSILON_DB
        )
        results.append(
            {
                "platform": platform,
                "target_lufs": target,
                "true_peak_ceiling_dbtp": ceiling,
                "estimated_gain_db": gain,
                "projected_true_peak_dbtp": projected_peak,
                "status": "ready" if ready else "adjustment recommended",
            }
        )
    return results
