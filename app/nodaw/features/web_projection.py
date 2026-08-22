from __future__ import annotations

import copy
from typing import Any

from ..core.models import AudioInfo, AudioMetrics, LoudnessMetrics, TrackAnalysis
from ..core.scoring import evaluate_track, floor_score_after_repair, rating
from .repairs import detect_repair_plan

PROMO = {"code": "WEB30", "label": "30% off CoProducer Pro", "was": 49, "now": 34}

LUFS_PASS_LU = 1.5
TP_EPSILON_DB = 0.15


def track_from_report(report: dict[str, Any]) -> TrackAnalysis:
    t = report["track"]
    a = t["audio"]
    m = t["metrics"]
    lm = m.get("loudness") or {}
    return TrackAnalysis(
        audio=AudioInfo(
            file_name=str(a.get("file_name") or "upload"),
            path=str(a.get("path") or ""),
            size_bytes=int(a.get("size_bytes") or 0),
            duration_seconds=float(a.get("duration_seconds") or 0.0),
            format_name=str(a.get("format_name") or "unknown"),
            codec_name=str(a.get("codec_name") or "unknown"),
            codec_long_name=str(a.get("codec_long_name") or "unknown"),
            sample_rate_hz=int(a.get("sample_rate_hz") or 0),
            channels=int(a.get("channels") or 0),
            channel_layout=str(a.get("channel_layout") or "unknown"),
            bit_rate_bps=a.get("bit_rate_bps"),
            bit_depth=a.get("bit_depth"),
        ),
        metrics=AudioMetrics(
            loudness=LoudnessMetrics(
                integrated_lufs=lm.get("integrated_lufs"),
                loudness_range_lu=lm.get("loudness_range_lu"),
                true_peak_dbtp=lm.get("true_peak_dbtp"),
                threshold_lufs=lm.get("threshold_lufs"),
                sample_peak_dbfs=lm.get("sample_peak_dbfs"),
            ),
            peak_dbfs=m.get("peak_dbfs"),
            rms_dbfs=m.get("rms_dbfs"),
            dynamic_range_db=m.get("dynamic_range_db"),
            crest_factor=m.get("crest_factor"),
            clipped_samples_estimate=int(m.get("clipped_samples_estimate") or 0),
            noise_floor_dbfs=m.get("noise_floor_dbfs"),
            stereo_width_percent=m.get("stereo_width_percent"),
            phase_correlation=m.get("phase_correlation"),
            spectral_balance_db=dict(m.get("spectral_balance_db") or {}),
            waveform=list(m.get("waveform") or []),
        ),
        extra=dict(t.get("extra") or {}),
    )


def _apply_plan_to_metrics(metrics: dict[str, Any], plan, extra: dict[str, Any]) -> dict[str, Any]:
    m = copy.deepcopy(metrics)
    lm = m.setdefault("loudness", {})
    ids = {a.id for a in plan.actions}
    target, ceiling = float(plan.target_lufs), float(plan.tp_ceiling)
    if "loudnorm" in ids:
        cur = lm.get("integrated_lufs")
        if cur is not None and m.get("rms_dbfs") is not None:
            m["rms_dbfs"] = round(float(m["rms_dbfs"]) + (target - float(cur)), 2)
        lm["integrated_lufs"] = target
        lm["true_peak_dbtp"] = ceiling
        lm["sample_peak_dbfs"] = ceiling
        m["peak_dbfs"] = ceiling
        m["clipped_samples_estimate"] = 0
    elif "true_peak_limit" in ids:
        lm["true_peak_dbtp"] = ceiling
        lm["sample_peak_dbfs"] = ceiling
        m["peak_dbfs"] = ceiling
        m["clipped_samples_estimate"] = 0
    if "highpass" in ids:
        faults = extra.setdefault("technical_faults", {})
        if isinstance(faults, dict):
            faults["dc_offset"] = 0.0
        noise = m.get("noise_floor_dbfs")
        if noise is not None and -70.0 <= float(noise) <= -42.0:
            m["noise_floor_dbfs"] = round(float(noise) - 3.0, 2)
    return m


def project_from_report(report: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    plan = detect_repair_plan(report, settings=settings)
    track = track_from_report(report)
    pre = int(report.get("score") or 0)
    metrics = copy.deepcopy(track.to_dict()["metrics"])
    extra = copy.deepcopy(track.extra)
    if plan.actions:
        metrics = _apply_plan_to_metrics(metrics, plan, extra)
        projected_track = track_from_report({"track": {"audio": track.to_dict()["audio"], "metrics": metrics, "extra": extra}})
        raw, _rt, _sm, findings = evaluate_track(projected_track, settings)
        score, _f = floor_score_after_repair(
            pre, raw, findings=findings,
            applied_filters=plan.filter_chain,
        )
    else:
        score = pre
        metrics = track.to_dict()["metrics"]
    return {
        "needed": bool(plan.actions),
        "score": int(score),
        "rating": rating(int(score)),
        "metrics": metrics,
        "plan": {
            "needed": bool(plan.actions),
            "summary": plan.summary if plan.actions else "No automatic repair needed",
            "actions": [
                {"id": a.id, "label": a.label, "reason": a.reason, "confidence": a.confidence, "severity": a.severity}
                for a in plan.actions
            ],
            "cautions": list(plan.cautions),
        },
    }


def _status(kind: str, yours, target) -> str:
    if yours is None or target is None:
        return "unknown"
    if kind == "lufs":
        return "pass" if abs(float(yours) - float(target)) <= LUFS_PASS_LU else "off"
    if kind == "tp":
        return "pass" if float(yours) <= float(target) + TP_EPSILON_DB else "off"
    if kind == "clips":
        return "pass" if int(yours) == 0 else "off"
    if kind == "sr":
        return "pass" if int(yours) >= int(target) else "off"
    if kind == "phase":
        return "pass" if float(yours) >= float(target) else "off"
    if kind == "score":
        return "pass" if int(yours) >= int(target) else "off"
    return "off"


def vs_target_rows(
    yours_metrics: dict[str, Any],
    projected_metrics: dict[str, Any],
    yours_score: int,
    projected_score: int,
    audio: dict[str, Any],
    settings: dict[str, Any],
) -> list[dict[str, Any]]:
    target_lufs = float(settings["analysis"]["target_lufs"])
    ceiling = float(settings["analysis"]["true_peak_ceiling_dbtp"])
    min_sr = int(settings["analysis"]["minimum_sample_rate_hz"])
    y_lufs = (yours_metrics.get("loudness") or {}).get("integrated_lufs")
    p_lufs = (projected_metrics.get("loudness") or {}).get("integrated_lufs")
    y_tp = (yours_metrics.get("loudness") or {}).get("true_peak_dbtp")
    p_tp = (projected_metrics.get("loudness") or {}).get("true_peak_dbtp")
    rows = [
        ("Integrated LUFS", y_lufs, p_lufs, target_lufs, "lufs"),
        ("True peak dBTP", y_tp, p_tp, ceiling, "tp"),
        ("Clipped samples", yours_metrics.get("clipped_samples_estimate") or 0,
         projected_metrics.get("clipped_samples_estimate") or 0, 0, "clips"),
        ("Sample rate Hz", audio.get("sample_rate_hz"), audio.get("sample_rate_hz"), min_sr, "sr"),
        ("Phase correlation", yours_metrics.get("phase_correlation"),
         projected_metrics.get("phase_correlation"), 0.2, "phase"),
        ("Score", yours_score, projected_score, 90, "score"),
    ]
    out = []
    for name, y, p, tgt, kind in rows:
        dy = None if y is None or tgt is None else round(float(y) - float(tgt), 2)
        dp = None if p is None or tgt is None else round(float(p) - float(tgt), 2)
        out.append({
            "metric": name,
            "yours": y,
            "projected": p,
            "target": tgt,
            "distance_yours": dy,
            "distance_projected": dp,
            "status_yours": _status(kind, y, tgt),
            "status_projected": _status(kind, p, tgt),
        })
    return out


def public_analyze_payload(report: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    projected = project_from_report(report, settings)
    track = copy.deepcopy(report.get("track") or {})
    audio = dict(track.get("audio") or {})
    audio.pop("path", None)
    track["audio"] = audio
    metrics = track.get("metrics") or {}
    streaming = report.get("streaming_analysis") or report.get("streaming") or {}
    platforms = streaming.get("platforms") if isinstance(streaming, dict) else streaming
    return {
        "score": int(report.get("score") or 0),
        "rating": report.get("rating"),
        "summary": report.get("summary"),
        "track": track,
        "findings": report.get("findings") or [],
        "streaming": platforms or [],
        "plan": projected["plan"],
        "projected": {
            "score": projected["score"],
            "rating": projected["rating"],
            "metrics": projected["metrics"],
        },
        "vs_target": vs_target_rows(
            metrics, projected["metrics"],
            int(report.get("score") or 0), projected["score"],
            audio, settings,
        ),
        "promo": dict(PROMO),
    }
