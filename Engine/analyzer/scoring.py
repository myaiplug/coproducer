# -*- coding: utf-8 -*-
"""Engine/analyzer/scoring.py — Strict 5-module scoring, gating, and grading."""

from __future__ import annotations

from typing import Any


def status(penalty: float) -> str:
    if penalty <= 1.5:
        return "excellent"
    if penalty <= 4:
        return "good"
    if penalty <= 8:
        return "watch"
    if penalty <= 14:
        return "problem"
    return "critical"


def metric(value: Any, penalty: float, note: str) -> dict:
    return {
        "value": value,
        "penalty": round(penalty, 3),
        "status": status(penalty),
        "note": note,
    }


def compute_score(penalties: dict[str, float], issue_flags: list[str]) -> tuple[int, str, str, dict]:
    total_penalty = sum(float(v) for v in penalties.values())
    score_raw = 100 - total_penalty

    serious = sum(1 for v in penalties.values() if float(v) >= 9)
    critical_c = sum(1 for v in penalties.values() if float(v) >= 16)
    score = int(round(max(0, min(100, score_raw))))

    if critical_c >= 2:
        score = min(score, 58)
    elif critical_c >= 1 and serious >= 3:
        score = min(score, 64)
    elif serious >= 5:
        score = min(score, 68)
    elif serious >= 4:
        score = min(score, 74)
    elif serious >= 3:
        score = min(score, 80)
    elif serious >= 2:
        score = min(score, 86)

    if score >= 95:
        grade = "A+"
        verdict = "Luxury-grade technical read. Release-ready by measured checks."
    elif score >= 90:
        grade = "A"
        verdict = "High-quality technical read. Release-ready with normal listening verification."
    elif score >= 82:
        grade = "B"
        verdict = "Good, but not elite. Repair/polish recommended."
    elif score >= 72:
        grade = "C"
        verdict = "Usable source. Needs meaningful cleanup."
    elif score >= 60:
        grade = "D"
        verdict = "Poor source. Repair needed before release."
    else:
        grade = "F"
        verdict = "Bad source. Replacement source may beat repair."

    module_scores = {}
    module_scores["technical_integrity"] = int(max(0, min(100, 100 - (
        penalties.get("peak_headroom", 0) + penalties.get("clipping", 0) +
        penalties.get("dc_offset", 0) + penalties.get("dropouts", 0)
    ) * 2.0)))
    module_scores["loudness_dynamics"] = int(max(0, min(100, 100 - (
        penalties.get("loudness", 0) + penalties.get("crest_factor", 0) +
        penalties.get("dynamic_spread", 0)
    ) * 2.0)))
    module_scores["noise_artifacts"] = int(max(0, min(100, 100 - (
        penalties.get("noise_floor", 0) + penalties.get("hiss_grit", 0) +
        penalties.get("click_pop_probability", 0) + penalties.get("codec_damage_risk", 0)
    ) * 1.7)))
    module_scores["spectral_balance"] = int(max(0, min(100, 100 - penalties.get("spectral_balance", 0) * 3.0)))
    module_scores["stereo_phase"] = int(max(0, min(100, 100 - penalties.get("stereo_phase", 0) * 4.0)))

    hard_flags = {"heavy_clipping", "codec_damage_risk", "dropouts", "phase_cancellation", "crushed_dynamics"}
    hard_count = len(set(issue_flags) & hard_flags)
    if hard_count >= 3:
        restoration = "low"
        likely_ceiling = min(78, score + 18)
    elif hard_count == 2:
        restoration = "medium"
        likely_ceiling = min(86, score + 22)
    elif hard_count == 1:
        restoration = "good"
        likely_ceiling = min(92, score + 20)
    else:
        restoration = "high"
        likely_ceiling = min(96, score + 18)

    return score, grade, verdict, {
        "total_penalty": round(total_penalty, 2),
        "serious_issues": serious,
        "critical_issues": critical_c,
        "module_scores": module_scores,
        "restoration_potential": restoration,
        "likely_ceiling": likely_ceiling,
    }
