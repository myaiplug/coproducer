from __future__ import annotations

from ..core.models import TrackAnalysis


def difference(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return round(left - right, 3)


def comparison_rows(user: TrackAnalysis, reference: TrackAnalysis) -> list[dict]:
    metrics = [
        (
            "Integrated LUFS",
            user.metrics.loudness.integrated_lufs,
            reference.metrics.loudness.integrated_lufs,
            1.5,
            3.0,
        ),
        (
            "True Peak dBTP",
            user.metrics.loudness.true_peak_dbtp,
            reference.metrics.loudness.true_peak_dbtp,
            0.75,
            1.5,
        ),
        (
            "Dynamic Range dB",
            user.metrics.dynamic_range_db,
            reference.metrics.dynamic_range_db,
            1.5,
            3.0,
        ),
        (
            "Stereo Width %",
            user.metrics.stereo_width_percent,
            reference.metrics.stereo_width_percent,
            10.0,
            20.0,
        ),
        (
            "Phase Correlation",
            user.metrics.phase_correlation,
            reference.metrics.phase_correlation,
            0.15,
            0.3,
        ),
    ]
    rows = []
    for label, user_value, reference_value, notice, warning in metrics:
        delta = difference(user_value, reference_value)
        magnitude = abs(delta) if delta is not None else None
        severity = (
            "unknown"
            if magnitude is None
            else "warning"
            if magnitude >= warning
            else "notice"
            if magnitude >= notice
            else "pass"
        )
        penalty = (
            8
            if severity == "warning"
            else 3
            if severity == "notice"
            else 4
            if severity == "unknown"
            else 0
        )
        rows.append(
            {
                "metric": label,
                "user_value": user_value,
                "reference_value": reference_value,
                "difference": delta,
                "severity": severity,
                "score_penalty": penalty,
            }
        )
    for band, user_value in user.metrics.spectral_balance_db.items():
        reference_value = reference.metrics.spectral_balance_db.get(band)
        delta = difference(user_value, reference_value)
        severity = (
            "unknown"
            if delta is None
            else "warning"
            if abs(delta) >= 3
            else "notice"
            if abs(delta) >= 1.5
            else "pass"
        )
        rows.append(
            {
                "metric": f"{band.replace('_', ' ').title()} balance",
                "user_value": user_value,
                "reference_value": reference_value,
                "difference": delta,
                "severity": severity,
                "score_penalty": 4 if severity == "warning" else 1 if severity == "notice" else 0,
            }
        )
    return rows
