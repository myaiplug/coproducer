# -*- coding: utf-8 -*-
"""Engine/analyzer/quality_analyzer_lux.py — High-end quality analyzer with strict scoring."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from scipy.signal import find_peaks

from ..utils.audio_utils import (
    clamp, safe_round, load_audio_safe, block_rms_db,
    spectral_ratios, integrated_lufs, true_peak_estimate, db,
)
from ..utils.file_utils import ffprobe_json, collect_files, save_json
from .scoring import compute_score


def transient_click_score(mono: np.ndarray, sr: int) -> tuple[float, int]:
    diff = np.abs(np.diff(mono))
    if len(diff) < 1000:
        return 0.0, 0
    threshold = np.percentile(diff, 99.85)
    peaks, _ = find_peaks(diff, height=threshold, distance=max(1, sr // 2000))
    local_med = np.median(diff) + 1e-12
    severity = float(np.mean(diff[peaks] / local_med)) if len(peaks) else 0.0
    count = int(len(peaks))
    score_val = clamp((count / max(1, len(mono) / sr)) * 0.35 + severity * 0.03, 0, 20)
    return score_val, count


def dropout_score(mono: np.ndarray, sr: int) -> tuple[float, int]:
    block = max(512, int(sr * 0.05))
    drops = 0
    prev = None
    for i in range(0, len(mono) - block, block):
        rms = np.sqrt(np.mean(mono[i:i + block] ** 2) + 1e-12)
        if prev is not None and prev > 0.02 and rms < prev * 0.08:
            drops += 1
        prev = rms
    return clamp(drops * 2.0, 0, 16), drops


def codec_risk(path: Path, meta: dict, ratios: dict, rolloff: float | None) -> tuple[float, list[str]]:
    notes = []
    penalty = 0.0
    codec = None
    bitrate = 0
    for s in meta.get("streams", []):
        if s.get("codec_type") == "audio":
            codec = s.get("codec_name")
            break
    try:
        bitrate = int(meta.get("format", {}).get("bit_rate", 0) or 0)
    except Exception:
        bitrate = 0

    lossy = path.suffix.lower() in [".mp3", ".m4a", ".ogg"] or codec in ["mp3", "aac", "vorbis", "opus"]
    if lossy:
        penalty += 3
        notes.append("Lossy container/codec detected.")
    if bitrate and lossy:
        if bitrate < 128000:
            penalty += 11; notes.append("Very low bitrate: codec damage likely.")
        elif bitrate < 192000:
            penalty += 7; notes.append("Low/medium bitrate: high-end smear likely.")
        elif bitrate < 256000:
            penalty += 3; notes.append("Lossy bitrate below premium archive quality.")
    if rolloff and rolloff < 14500 and lossy:
        penalty += 5; notes.append("High-frequency rolloff suggests lossy cutoff.")
    if ratios.get("air_11000_16000", 0) < 0.006 and lossy:
        penalty += 5; notes.append("Weak air band suggests dull or encoded source.")
    return clamp(penalty, 0, 18), notes


def analyze(path: Path) -> dict:
    data, sr = load_audio_safe(path)
    mono = np.mean(data, axis=1)
    meta = ffprobe_json(path)

    lufs = integrated_lufs(data, sr)
    peak_db = true_peak_estimate(data)
    sample_peak_db = db(float(np.max(np.abs(data)) + 1e-12))
    rms_db = db(float(np.sqrt(np.mean(mono * mono) + 1e-12)))
    crest = peak_db - rms_db
    dc = float(np.mean(mono))
    clip_count = int(np.sum(np.abs(data) >= 0.999))
    clip_ratio = clip_count / max(1, data.size)

    blocks = block_rms_db(mono, sr)
    noise_floor = float(np.percentile(blocks, 10)) if len(blocks) else -96.0
    loud_floor = float(np.percentile(blocks, 90)) if len(blocks) else rms_db
    dynamic_spread = loud_floor - noise_floor

    bands, ratios, centroid, rolloff = spectral_ratios(mono, sr)
    zcr = float(np.mean(np.abs(np.diff(np.signbit(mono))).astype(np.float32)))
    click_severity, click_count = transient_click_score(mono, sr)
    dropout_penalty_val, dropout_count = dropout_score(mono, sr)

    stereo_corr = None
    side_mid_db = None
    if data.shape[1] >= 2:
        l_ch, r_ch = data[:, 0], data[:, 1]
        if np.std(l_ch) > 1e-8 and np.std(r_ch) > 1e-8:
            stereo_corr = float(np.corrcoef(l_ch, r_ch)[0, 1])
        mid = (l_ch + r_ch) * 0.5
        side = (l_ch - r_ch) * 0.5
        side_mid_db = db(np.sqrt(np.mean(side * side) + 1e-12) / (np.sqrt(np.mean(mid * mid) + 1e-12)))

    penalties = {}
    recs = []
    flags = []

    # 1. Technical Integrity
    p_peak = 0
    if peak_db > 0:
        p_peak = 12; flags.append("inter_sample_peak_risk"); recs.append("Lower ceiling and use true-peak limiting.")
    elif peak_db > -0.1:
        p_peak = 9; flags.append("ceiling_peak")
    elif peak_db > -1.0:
        p_peak = 4; flags.append("peak_close_to_ceiling")
    penalties["peak_headroom"] = p_peak

    p_clip = 0
    if clip_ratio > 0.001:
        p_clip = 22; flags.append("heavy_clipping"); recs.append("Heavy clipping: de-clip may help, but source replacement is better.")
    elif clip_ratio > 0.0001:
        p_clip = 13; flags.append("clipping"); recs.append("Clipping detected. Run artifact repair/de-clip.")
    elif clip_count > 0:
        p_clip = 6; flags.append("light_clipping")
    penalties["clipping"] = p_clip

    p_dc = 8 if abs(dc) > 0.02 else 3 if abs(dc) > 0.005 else 0
    if p_dc:
        flags.append("dc_offset"); recs.append("Remove DC offset / apply safe high-pass.")
    penalties["dc_offset"] = p_dc

    penalties["dropouts"] = dropout_penalty_val
    if dropout_penalty_val:
        flags.append("dropouts"); recs.append("Dropouts or sudden level collapses detected.")

    # 2. Loudness & Dynamics
    p_lufs = 0
    if lufs is not None:
        if lufs > -8:
            p_lufs = 12; flags.append("too_loud"); recs.append("Track is extremely loud. Reduce gain and re-limit.")
        elif lufs > -10:
            p_lufs = 7; flags.append("very_loud")
        elif lufs < -25:
            p_lufs = 8; flags.append("very_quiet"); recs.append("Track is very quiet. Normalize after repair.")
        elif lufs < -19:
            p_lufs = 4; flags.append("quiet")
    penalties["loudness"] = p_lufs

    p_crest = 0
    if crest < 4.5:
        p_crest = 18; flags.append("crushed_dynamics"); recs.append("Dynamics are crushed; avoid more compression.")
    elif crest < 6.5:
        p_crest = 11; flags.append("low_crest")
    elif crest > 24:
        p_crest = 5; flags.append("spiky_transients")
    penalties["crest_factor"] = p_crest

    p_dyn = 0
    if dynamic_spread < 8:
        p_dyn = 8; flags.append("flat_dynamics")
    elif dynamic_spread > 38:
        p_dyn = 5; flags.append("unstable_levels")
    penalties["dynamic_spread"] = p_dyn

    # 3. Noise & Artifacts
    p_noise = 0
    if noise_floor > -36:
        p_noise = 18; flags.append("high_noise_floor"); recs.append("High noise floor. Use profile denoise/adaptive denoise.")
    elif noise_floor > -45:
        p_noise = 11; flags.append("moderate_noise_floor")
    elif noise_floor > -55:
        p_noise = 5; flags.append("light_noise_floor")
    penalties["noise_floor"] = p_noise

    p_zcr = 0
    if zcr > 0.24 and ratios.get("harsh_4500_8000", 0) > 0.10:
        p_zcr = 12; flags.append("hiss_crackle_grit"); recs.append("Hiss/crackle/grit signature detected.")
    elif zcr > 0.17:
        p_zcr = 5; flags.append("zcr_high")
    penalties["hiss_grit"] = p_zcr

    penalties["click_pop_probability"] = click_severity
    if click_severity > 8:
        flags.append("clicks_pops"); recs.append("Click/pop probability high. Use de-click before enhancement.")

    codec_penalty_val, codec_notes = codec_risk(path, meta, ratios, rolloff)
    penalties["codec_damage_risk"] = codec_penalty_val
    if codec_penalty_val > 5:
        flags.append("codec_damage_risk")
    recs.extend(codec_notes)

    # 4. Spectral Balance
    p_spec = 0
    rumble = ratios.get("sub_20_40", 0) + ratios.get("sub_40_80", 0)
    mud = ratios.get("mud_180_350", 0)
    box = ratios.get("box_350_700", 0)
    harsh = ratios.get("harsh_4500_8000", 0)
    sib = ratios.get("sibilance_8000_11000", 0)
    air = ratios.get("air_11000_16000", 0)

    if rumble > 0.24:
        p_spec += 8; flags.append("rumble"); recs.append("Excess rumble/sub energy. High-pass and control low end.")
    if mud > 0.20:
        p_spec += 8; flags.append("mud"); recs.append("Low-mid mud detected around 180-350 Hz.")
    if box > 0.24:
        p_spec += 6; flags.append("boxy"); recs.append("Boxiness detected around 350-700 Hz.")
    if harsh > 0.20:
        p_spec += 9; flags.append("harsh"); recs.append("Harshness detected around 4.5-8 kHz.")
    if sib > 0.18:
        p_spec += 6; flags.append("sibilant"); recs.append("Sibilance/edge detected around 8-11 kHz.")
    if air < 0.006 and centroid < 1700:
        p_spec += 9; flags.append("dull_bandlimited"); recs.append("Dull/band-limited source. Restore air carefully after denoise.")
    p_spec = clamp(p_spec, 0, 24)
    penalties["spectral_balance"] = p_spec

    # 5. Stereo & Phase
    p_stereo = 0
    if stereo_corr is not None:
        if stereo_corr < -0.2:
            p_stereo = 16; flags.append("phase_cancellation"); recs.append("Negative stereo correlation. Collapse/repair phase before release.")
        elif stereo_corr < 0.1:
            p_stereo = 9; flags.append("weak_mono_compatibility")
        elif stereo_corr > 0.998 and data.shape[1] >= 2:
            p_stereo = 2; flags.append("near_mono")
    if side_mid_db is not None and side_mid_db > 2:
        p_stereo += 5; flags.append("excessive_side_energy"); recs.append("Excessive side energy may fold down badly in mono.")
    p_stereo = clamp(p_stereo, 0, 18)
    penalties["stereo_phase"] = p_stereo

    if not recs:
        recs.append("No major technical defects detected. Confirm with A/B listening.")

    score, grade, verdict, scoring_detail = compute_score(penalties, flags)

    return {
        "score": score, "grade": grade, "verdict": verdict,
        "lufs": safe_round(lufs, 2),
        "peak_db": safe_round(peak_db, 2),
        "rms_db": safe_round(rms_db, 2),
        "crest_db": safe_round(crest, 2),
        "clipping_events": clip_count,
        "clipping_ratio": safe_round(clip_ratio, 6),
        "noise_floor_db": safe_round(noise_floor, 2),
        "dynamic_spread_db": safe_round(dynamic_spread, 2),
        "dc_offset": safe_round(dc, 6),
        "zero_crossing_rate": safe_round(zcr, 5),
        "stereo_correlation": safe_round(stereo_corr, 5),
        "side_mid_db": safe_round(side_mid_db, 2),
        "centroid_hz": safe_round(centroid, 1),
        "rolloff_95_hz": safe_round(rolloff, 1),
        "spectral_ratios": {k: safe_round(v, 5) for k, v in ratios.items()},
        "click_count": click_count,
        "dropout_count": dropout_count,
        "codec_penalty": safe_round(codec_penalty_val, 2),
        "penalties": penalties,
        "issue_flags": sorted(set(flags)),
        "recommendations": recs,
        "scoring_detail": scoring_detail,
    }


def analyze_batch(paths: list[Path]) -> list[dict]:
    results = []
    for p in paths:
        try:
            results.append(analyze(p))
        except Exception as e:
            results.append({"file": str(p), "error": str(e), "score": 0, "grade": "ERR"})
    return results


def main():
    p = argparse.ArgumentParser(description="NoDAW Quality Analyzer LUX v3")
    p.add_argument("--input", "-i", required=True)
    p.add_argument("--output", "-o", default="LUX_v3_Reports")
    args = p.parse_args()

    input_path = Path(args.input)
    output_dir = Path(args.output)
    files = collect_files(input_path)

    for f in files:
        result = analyze(f)
        stem = f.stem
        save_json(output_dir / f"{stem}_LUX_v3_analysis.json", result)


if __name__ == "__main__":
    main()
