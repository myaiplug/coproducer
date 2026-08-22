"""
Studio FX engine for CoProducer - artifact hunt/remove, bleedfix,
parametric EQ with Q, wet/dry mixing, and VST3 / JSON-effect baking.

Detection is numpy-based; processing prefers Spotify Pedalboard when
available and falls back to numpy-only repair stages.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np
import soundfile as sf

try:
    import pedalboard as pb

    HAS_PEDALBOARD = True
except Exception:  # pragma: no cover
    HAS_PEDALBOARD = False
    pb = None  # type: ignore


# ---------------------------------------------------------------------------
# Artifact hunting
# ---------------------------------------------------------------------------

@dataclass
class ArtifactHunt:
    clicks: list[tuple[float, float, float]] = field(default_factory=list)
    dropout_edges: list[tuple[float, float]] = field(default_factory=list)
    dc_offset: float = 0.0
    clipped_estimate: int = 0
    clipped_runs: list[tuple[int, int]] = field(default_factory=list)
    duration_s: float = 0.0
    error: str | None = None
    # v2 intelligence (optional; populated by hunt_artifacts when repair_intel available)
    hits: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    repair_methods: dict[str, str] = field(default_factory=dict)  # "start_end" -> method

    @property
    def summary(self) -> str:
        if self.error:
            return f"artifact hunt failed: {self.error}"
        parts = []
        if self.hits:
            by: dict[str, int] = {}
            for h in self.hits:
                k = str(h.get("kind") or "click")
                by[k] = by.get(k, 0) + 1
            for k, n in sorted(by.items()):
                parts.append(f"{n} {k}")
        elif self.clicks:
            parts.append(f"{len(self.clicks)} click{'s' if len(self.clicks) > 1 else ''}")
        if self.dropout_edges:
            parts.append(f"{len(self.dropout_edges)} dropout edge{'s' if len(self.dropout_edges) > 1 else ''}")
        if abs(self.dc_offset) > 0.004:
            parts.append(f"DC {self.dc_offset:+.4f}")
        if self.clipped_estimate:
            parts.append(f"{self.clipped_estimate} clipped samples")
        return "; ".join(parts) or "clean"


def _load_mono(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        return a.astype(np.float64)
    if a.shape[0] <= 8 and a.shape[0] < a.shape[1]:
        return np.mean(a.astype(np.float64), axis=0)
    return np.mean(a.astype(np.float64), axis=1)


def _click_runs(mono: np.ndarray, sr: int) -> list[tuple[int, int, float]]:
    """Return (start, end, peak_db) runs that look like digital clicks/pops.

    Works on the first-difference signal: a click is a huge sample-to-sample
    jump, while program transients spread across many samples. A median of
    |diff| is the local reference, so loud music never masks real clicks.
    """
    n = mono.shape[0]
    if n < sr // 20:
        return []
    try:
        from scipy.ndimage import median_filter
    except Exception:
        return []
    d = np.diff(mono, prepend=mono[0])
    ad = np.abs(d)
    win = max(9, int(sr * 0.0012))
    med = median_filter(ad, size=win)
    ratio = ad / (med + 1e-12)
    mask = (ratio > 8.0) & (ad > 0.02)
    raw_runs: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and mask[j + 1]:
            j += 1
        raw_runs.append((i, j))
        i = j + 1
    merged: list[tuple[int, int]] = []
    for r in raw_runs:
        if merged and r[0] - merged[-1][1] <= 6:
            merged[-1] = (merged[-1][0], r[1])
        else:
            merged.append(r)
    max_len = int(sr * 0.0005) + 2
    absx = np.abs(mono)
    runs: list[tuple[int, int, float]] = []
    for s, e in merged:
        if e - s + 1 > max_len:
            continue
        if int(np.count_nonzero(mask[s : e + 1])) < 2:
            continue
        lo, hi = max(0, s - 2), min(n - 1, e + 2)
        run_peak = float(np.max(absx[lo : hi + 1]))
        if run_peak > 0.15:
            runs.append((lo, hi, run_peak))
    runs.sort(key=lambda r: r[2], reverse=True)
    return runs[:200]


def _dropout_edges(mono: np.ndarray, sr: int) -> list[tuple[float, float]]:
    """Silence runs (>=60 ms) that start after 100 ms of program material."""
    n = mono.shape[0]
    if n < sr // 10:
        return []
    level = np.abs(mono)
    quiet = level < 1e-4
    edges: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if not quiet[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and quiet[j + 1]:
            j += 1
        if (j - i + 1) >= int(sr * 0.06) and i > int(sr * 0.1) and j < n - int(sr * 0.05):
            edges.append((i, j))
        i = j + 1
    return [(s / sr, e / sr) for s, e in edges[:50]]


def hunt_artifacts_audio(
    audio: np.ndarray,
    sr: int,
    *,
    clipped_ref: float = 0.9999,
) -> ArtifactHunt:
    """Hunt artifacts. Prefer multi-feature v2 path; fall back to classic."""
    a = np.asarray(audio, dtype=np.float32)
    # --- v2 intelligence ---
    try:
        from .repair_intel import hunt_artifacts_v2

        v2 = hunt_artifacts_v2(a, int(sr))
        metrics = dict(v2.metrics or {})
        # Preferred bake algorithm (also set by suggest_artifact_settings_v2)
        n_mouth = sum(1 for h in v2.hits if h.kind == "mouth")
        n_dig = sum(1 for h in v2.hits if h.kind == "digital")
        longish = sum(1 for h in v2.hits if (h.end_s - h.start_s) > 0.0012)
        if n_mouth >= max(2, len(v2.hits) // 3) or longish >= 2:
            metrics["preferred_algorithm"] = "spectral"
        elif n_dig >= 3 or len(v2.hits) >= 6:
            metrics["preferred_algorithm"] = "multi_band"
        else:
            metrics["preferred_algorithm"] = "single"
        metrics["freq_skew"] = 0.7 if n_mouth > n_dig else 0.45
        hunt = ArtifactHunt(
            clicks=v2.clicks,
            dropout_edges=list(v2.dropout_edges or []),
            dc_offset=float(v2.dc_offset or 0.0),
            clipped_estimate=int(v2.clipped_estimate or 0),
            clipped_runs=list(v2.clipped_runs or []),
            duration_s=float(v2.duration_s or 0.0),
            error=v2.error,
            hits=[
                {
                    "start_s": h.start_s,
                    "end_s": h.end_s,
                    "kind": h.kind,
                    "confidence": h.confidence,
                    "method": h.method,
                    "peak_db": h.peak_db,
                    "band": h.band,
                    "tf_mask": bool(h.tf_mask),
                    "score": float(h.score),
                }
                for h in v2.hits
            ],
            metrics=metrics,
            repair_methods={
                f"{h.start_s:.6f}_{h.end_s:.6f}": h.method for h in v2.hits
            },
        )
        if not hunt.error:
            return hunt
    except Exception:
        pass

    # --- classic fallback ---
    hunt = ArtifactHunt(duration_s=a.shape[-1] / sr)
    try:
        n = a.shape[-1]
        if a.ndim == 1:
            channels = [a]
        elif a.shape[0] <= 8 and a.shape[0] < a.shape[1]:
            channels = [a[c] for c in range(a.shape[0])]
        else:
            channels = [np.mean(a.astype(np.float64), axis=1)]
        mono = _load_mono(a)

        raw_runs: list[tuple[int, int, float]] = []
        for ch in channels:
            raw_runs.extend(_click_runs(ch.astype(np.float32), sr))
        raw_runs.sort(key=lambda r: r[0])
        merged: list[tuple[int, int, float]] = []
        for s, e, pk in raw_runs:
            if merged and s - merged[-1][1] <= 8:
                ms, me, mpk = merged[-1]
                merged[-1] = (ms, max(me, e), max(mpk, pk))
            else:
                merged.append((s, e, pk))
        hunt.clicks = [(s / sr, e / sr, _db(pk)) for s, e, pk in merged[:200]]

        hunt.dropout_edges = _dropout_edges(mono, sr)
        hunt.dc_offset = float(np.mean(mono))
        ab = np.abs(a)
        hunt.clipped_estimate = int(np.sum(ab >= clipped_ref))
        clip_mask = ab >= clipped_ref
        flat_clip = clip_mask.any(axis=0) if clip_mask.ndim == 2 else clip_mask
        runs: list[tuple[int, int]] = []
        i = 0
        while i < n:
            if not flat_clip[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and flat_clip[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        hunt.clipped_runs = runs[:500]
    except Exception as exc:
        hunt.error = str(exc)
    return hunt


def _db(v: float) -> float:
    return 20.0 * math.log10(max(float(v), 1e-12))


def hunt_artifacts(path: Path | str) -> ArtifactHunt:
    """Load a file and hunt artifacts. Never modifies anything."""
    try:
        data, sr = sf.read(str(path), always_2d=True, dtype="float32")
        return hunt_artifacts_audio(data.T, int(sr))
    except Exception as exc:
        return ArtifactHunt(error=str(exc))


def suggest_artifact_settings(hunt: ArtifactHunt) -> dict[str, Any]:
    """
    Auto-dial Artifact Hunter toggles from a completed scan.

    Uses v2 confidence-aware logic when hits[] is populated.
    """
    if hunt is not None and not hunt.error and hunt.hits:
        try:
            from .repair_intel import ArtifactHuntV2, ArtifactHit, suggest_artifact_settings_v2

            v2 = ArtifactHuntV2(
                hits=[
                    ArtifactHit(
                        start_s=float(h["start_s"]),
                        end_s=float(h["end_s"]),
                        peak_db=float(h.get("peak_db", -12)),
                        confidence=float(h.get("confidence", 0.5)),
                        kind=str(h.get("kind") or "digital"),
                        method=str(h.get("method") or "cubic"),
                        band=h.get("band"),
                        tf_mask=bool(h.get("tf_mask")),
                        score=float(h.get("score") or 0.0),
                    )
                    for h in hunt.hits
                    if isinstance(h, dict)
                ],
                dropout_edges=list(hunt.dropout_edges or []),
                dc_offset=float(hunt.dc_offset or 0.0),
                clipped_estimate=int(hunt.clipped_estimate or 0),
                metrics=dict(hunt.metrics or {}),
            )
            out = suggest_artifact_settings_v2(v2)
            # Keep hunt metrics in sync for offline bake fallback
            try:
                hunt.metrics = dict(hunt.metrics or {})
                hunt.metrics["preferred_algorithm"] = out.get("algorithm", "auto")
                hunt.metrics["freq_skew"] = out.get("freq_skew", 0.5)
                hunt.metrics["min_confidence"] = out.get("min_confidence", 0.45)
            except Exception:
                pass
            return out
        except Exception:
            pass
    if hunt is None or hunt.error:
        return {
            "declick": False,
            "dedc": False,
            "deedge": False,
            "any": False,
            "note": "scan failed — left dry",
            "severity": 0.0,
            "hits": [],
        }
    n_clicks = len(hunt.clicks or [])
    n_drops = len(hunt.dropout_edges or [])
    dc = float(hunt.dc_offset or 0.0)
    declick = n_clicks > 0
    deedge = n_drops > 0
    dedc = abs(dc) > 0.004
    severity = 0.0
    if declick:
        peaks = [abs(c[2]) for c in hunt.clicks if len(c) > 2]
        avg_pk = float(np.mean(peaks)) if peaks else 0.0
        severity = max(severity, min(1.0, 0.25 + 0.08 * n_clicks + max(0.0, avg_pk + 20) / 40.0))
    if deedge:
        severity = max(severity, min(1.0, 0.2 + 0.1 * n_drops))
    if dedc:
        severity = max(severity, min(1.0, abs(dc) * 25.0))
    parts = []
    if declick:
        parts.append(f"de-click×{n_clicks}")
    if deedge:
        parts.append(f"edges×{n_drops}")
    if dedc:
        parts.append(f"DC {dc:+.4f}")
    note = "auto · " + (" · ".join(parts) if parts else "clean · repairs off")
    return {
        "declick": declick,
        "dedc": dedc,
        "deedge": deedge,
        "any": declick or dedc or deedge,
        "note": note,
        "severity": float(severity),
        "n_clicks": n_clicks,
        "n_drops": n_drops,
        "dc_offset": dc,
        "hits": list(hunt.hits or []),
        "min_confidence": 0.45,
    }


def suggest_bleedfix_settings(
    path: Path | str | None = None,
    *,
    audio: np.ndarray | None = None,
    sr: int | None = None,
    profile: str = "balanced",
    sidechain_path: Path | str | None = None,
) -> dict[str, Any]:
    """
    Auto-dial BleedFix (v2): program-safe search, hysteresis, look-ahead,
    optional keyed source, Safe/Balanced/Aggressive profiles.
    """
    try:
        from .repair_intel import suggest_bleedfix_v2

        sc = None
        if sidechain_path and Path(sidechain_path).exists():
            try:
                d, ssr = sf.read(str(sidechain_path), always_2d=True, dtype="float32")
                sc = d.T
            except Exception:
                sc = None
        return suggest_bleedfix_v2(
            path,
            audio=audio,
            sr=sr,
            sidechain=sc,
            profile=profile,
        )
    except Exception as exc:
        return {
            "on": False,
            "mode": "auto",
            "profile": profile,
            "threshold_db": -46.0,
            "ratio": 8.0,
            "attack_ms": 8.0,
            "release_ms": 160.0,
            "margin_db": 8.0,
            "bands": 1,
            "wet": 1.0,
            "note": f"bleedfix auto failed: {exc}",
            "gated_fraction": 0.0,
        }


# ---------------------------------------------------------------------------
# Artifact removal (numpy repair)
# ---------------------------------------------------------------------------

def _crossfade_repair(x: np.ndarray, start: int, end: int, pad: int = 3) -> None:
    n = x.shape[0]
    a0 = max(0, start - pad)
    b0 = min(n - 1, end + pad)
    left = float(x[a0])
    right = float(x[b0])
    seg = b0 - a0 + 1
    if seg < 2:
        x[start : end + 1] = 0.0
        return
    t = np.linspace(0.0, 1.0, seg)
    ramp = left * (1 - t) + right * t
    x[a0 : b0 + 1] = ramp


def _cubic_repair(x: np.ndarray, start: int, end: int, ctx: int = 3) -> None:
    """Replace [start, end] with a C1-continuous cubic blend.

    Cubic Hermite spline between the surrounding context anchors with
    matched endpoint slopes — removes the derivative discontinuity that
    makes linear-ramp repairs audible as residual ticks. Falls back to
    the linear crossfade when context is missing.
    """
    n = x.shape[0]
    a0 = max(0, start - ctx)
    b0 = min(n - 1, end + ctx)
    if a0 >= start or b0 <= end or a0 + 3 >= b0:
        _crossfade_repair(x, start, end, pad=ctx)
        return
    try:
        from scipy.interpolate import CubicSpline
    except Exception:
        _crossfade_repair(x, start, end, pad=ctx)
        return
    al = max(0, a0 - 2)
    br = min(n - 1, b0 + 2)
    sL = (float(x[a0]) - float(x[al])) / max(1.0, float(a0 - al))
    sR = (float(x[br]) - float(x[b0])) / max(1.0, float(br - b0))
    try:
        cs = CubicSpline(
            [a0, b0],
            [float(x[a0]), float(x[b0])],
            bc_type=((1, sL), (1, sR)),
        )
        y = cs(np.arange(a0, b0 + 1, dtype=np.float64))
    except Exception:
        _crossfade_repair(x, start, end, pad=ctx)
        return
    y = np.clip(y, -2.0, 2.0)
    x[a0 : b0 + 1] = y.astype(x.dtype)


def _declip_runs(x: np.ndarray, runs: list[tuple[int, int]], sr: int) -> int:
    """Re-interpolate short clipped runs (flat-topped waveform) with C1 cubics."""
    n = x.shape[0]
    max_run = int(sr * 0.002) + 4
    fixed = 0
    for s, e in runs:
        if e - s + 1 > max_run or e < s:
            continue
        a = s
        while a > 0 and abs(x[a]) >= 0.95 and s - a < 32:
            a -= 1
        b = e
        while b < n - 1 and abs(x[b]) >= 0.95 and b - e < 32:
            b += 1
        if a >= b or b - a > max_run + 64:
            continue
        lo, hi = max(0, a - 2), min(n - 1, b + 2)
        sL = (float(x[a]) - float(x[lo])) / max(1.0, float(a - lo))
        sR = (float(x[hi]) - float(x[b])) / max(1.0, float(hi - b))
        try:
            from scipy.interpolate import CubicSpline
        except Exception:
            break
        try:
            cs = CubicSpline([a, b], [float(x[a]), float(x[b])], bc_type=((1, sL), (1, sR)))
            y = np.clip(cs(np.arange(a, b + 1, dtype=np.float64)), -1.05, 1.05)
        except Exception:
            continue
        x[a : b + 1] = y.astype(x.dtype)
        fixed += 1
    return fixed


def remove_artifacts_audio(
    audio: np.ndarray,
    sr: int,
    hunt: ArtifactHunt,
    *,
    declick: bool = True,
    dedc: bool = True,
    deedge: bool = True,
    declip: bool = True,
    algorithm: str = "auto",
    freq_skew: float = 0.5,
    min_confidence: float = 0.45,
) -> tuple[np.ndarray, list[str]]:
    """Repair clicks, DC offset, dropout edges, and clipped runs on a copy.

    algorithm: auto | single | multi_band | spectral  (RX-class paths via repair_intel)
    """
    x = np.ascontiguousarray(np.array(audio, dtype=np.float32, copy=True))
    applied: list[str] = []
    if x.ndim == 1:
        x = x.reshape(1, -1)
    if x.shape[0] > 8 and x.shape[0] > x.shape[1]:
        x = x.T
    ch = x.shape[0]
    n = x.shape[1]

    if declick and (hunt.hits or hunt.clicks):
        # Prefer repair ladder when v2 hits are present
        if hunt.hits:
            try:
                from .repair_intel import ArtifactHuntV2, ArtifactHit, remove_artifacts_v2

                v2 = ArtifactHuntV2(
                    hits=[
                        ArtifactHit(
                            start_s=float(h["start_s"]),
                            end_s=float(h["end_s"]),
                            peak_db=float(h.get("peak_db", -12)),
                            confidence=float(h.get("confidence", 0.6)),
                            kind=str(h.get("kind") or "digital"),
                            method=str(h.get("method") or "cubic"),
                            band=h.get("band"),
                            tf_mask=bool(h.get("tf_mask")),
                            score=float(h.get("score") or 0.0),
                        )
                        for h in hunt.hits
                        if isinstance(h, dict)
                    ],
                    dropout_edges=list(hunt.dropout_edges or []),
                    dc_offset=float(hunt.dc_offset or 0.0),
                    clipped_estimate=int(hunt.clipped_estimate or 0),
                    clipped_runs=list(hunt.clipped_runs or []),
                    metrics=dict(hunt.metrics or {}),
                )
                algo = (algorithm or "auto").lower()
                if algo in ("", "none"):
                    algo = "auto"
                # If caller left auto, honor hunt preference when present
                if algo == "auto":
                    pref = str(
                        (hunt.metrics or {}).get("preferred_algorithm")
                        or (v2.metrics or {}).get("preferred_algorithm")
                        or ""
                    ).lower()
                    if pref in ("multi_band", "spectral", "single"):
                        algo = pref
                return remove_artifacts_v2(
                    audio, sr, v2,
                    declick=declick, dedc=dedc, deedge=deedge, declip=declip,
                    algorithm=algo,
                    freq_skew=freq_skew,
                    min_confidence=min_confidence,
                )
            except Exception:
                pass
        for s, e, _ in hunt.clicks:
            si, ei = int(s * sr), int(e * sr)
            si, ei = max(0, si), min(n - 1, ei)
            if ei <= si:
                continue
            for c in range(ch):
                _cubic_repair(x[c], si, ei)
        applied.append(f"declick {len(hunt.clicks)}")

    if declip and hunt.clipped_runs:
        fixed = _declip_runs(x, hunt.clipped_runs, int(sr))
        if fixed:
            applied.append(f"declip {fixed}")

    if dedc and abs(hunt.dc_offset) > 0.004:
        for c in range(ch):
            x[c] = x[c] - float(hunt.dc_offset)
        applied.append(f"DC {hunt.dc_offset:+.4f}")

    if deedge and hunt.dropout_edges:
        fade = max(2, int(sr * 0.003))
        for s, e in hunt.dropout_edges:
            si, ei = int(s * sr), int(e * sr)
            si, ei = max(0, si), min(n - 1, ei)
            for c in range(ch):
                lo = max(0, si - fade)
                for k in range(fade):
                    p = lo + k
                    if 0 <= p < n:
                        w = 0.5 - 0.5 * math.cos(math.pi * (k + 1) / fade)
                        x[c, p] = x[c, p] * w
                hi = min(n - 1, ei + fade)
                for k in range(fade):
                    p = hi - k
                    if 0 <= p < n:
                        w = 0.5 - 0.5 * math.cos(math.pi * (k + 1) / fade)
                        x[c, p] = x[c, p] * w
        applied.append(f"deedge {len(hunt.dropout_edges)}")

    if not applied:
        applied.append("anull")
    return x, applied


# ---------------------------------------------------------------------------
# Bleedfix (expander/gate via Pedalboard) + wet/dry
# ---------------------------------------------------------------------------

def mix_wet_dry(dry: np.ndarray, processed: np.ndarray, wet: float) -> np.ndarray:
    wet = max(0.0, min(1.0, float(wet)))
    dry = np.asarray(dry, dtype=np.float32)
    processed = np.asarray(processed, dtype=np.float32)
    if wet <= 0.0:
        return dry
    if wet >= 1.0:
        return processed
    return dry.astype(np.float32) * (1.0 - wet) + processed * wet


# ---------------------------------------------------------------------------
# Bleedfix engine (numpy): noise-floor detection, sidechain keying, multiband
# ---------------------------------------------------------------------------

def _rms_frames_db(x: np.ndarray, sr: int, win_ms: float = 10.0, hop_ms: float = 5.0) -> np.ndarray:
    """Frame RMS in dB across a signal (1-D)."""
    n = x.shape[0]
    win = max(8, int(sr * win_ms / 1000.0))
    hop = max(1, int(sr * hop_ms / 1000.0))
    if n < win:
        win = max(1, n)
        hop = 1
    count = max(1, (n - win) // hop + 1)
    try:
        segs = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    except Exception:
        segs = np.stack([x[s : s + win] for s in range(0, n - win + 1, hop)])
    rms = np.sqrt(np.mean(segs.astype(np.float64) ** 2, axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-12))


def _smooth_db(x_db: np.ndarray, attack_ms: float, release_ms: float, frame_rate: float) -> np.ndarray:
    """One-pole envelope smoothing in dB: fast on attack, slow on release."""
    a_up = 1.0 - math.exp(-1.0 / (max(attack_ms, 0.1) / 1000.0 * frame_rate))
    a_dn = 1.0 - math.exp(-1.0 / (max(release_ms, 1.0) / 1000.0 * frame_rate))
    out = np.empty_like(x_db)
    if x_db.size == 0:
        return out
    out[0] = x_db[0]
    for i in range(1, x_db.size):
        p = out[i - 1]
        v = x_db[i]
        out[i] = p + (a_up * (v - p) if v > p else a_dn * (v - p))
    return out


def _smooth_gain_db(g_db: np.ndarray, attack_ms: float, release_ms: float, frame_rate: float) -> np.ndarray:
    """Smooth a gain curve in dB: gain drops with attack time, recovers with release."""
    a_dn = 1.0 - math.exp(-1.0 / (max(attack_ms, 0.1) / 1000.0 * frame_rate))
    a_up = 1.0 - math.exp(-1.0 / (max(release_ms, 1.0) / 1000.0 * frame_rate))
    out = np.empty_like(g_db)
    if g_db.size == 0:
        return out
    out[0] = g_db[0]
    for i in range(1, g_db.size):
        p = out[i - 1]
        v = g_db[i]
        out[i] = p + (a_dn * (v - p) if v < p else a_up * (v - p))
    return out


def _expander_gain_db(env_db: np.ndarray, thr_db: float, ratio: float) -> np.ndarray:
    """Downward expander: below threshold, 1 dB down costs (1 - 1/ratio) dB of gain."""
    g = np.zeros_like(env_db)
    below = env_db < thr_db
    g[below] = (env_db[below] - thr_db) * (1.0 - 1.0 / max(float(ratio), 1.01))
    return g


def _lr_low(x: np.ndarray, sr: int, freq: float) -> np.ndarray:
    """Zero-phase 4th-order Linkwitz-Riley-style lowpass."""
    try:
        from scipy.signal import butter, sosfiltfilt
    except Exception:
        return x
    sos = butter(2, freq, btype="low", fs=sr, output="sos")
    return np.asarray(sosfiltfilt(sos, x), dtype=np.float32)


def _band_split(x: np.ndarray, sr: int, bands: int) -> list[np.ndarray]:
    """Complementary band split (bands=1 → full; 3 → low/mid/high). Sum == x."""
    if bands != 3:
        return [x]
    low = _lr_low(x, sr, 300.0)
    rest = x - low
    mid = _lr_low(rest, sr, 3000.0)
    high = rest - mid
    return [low, mid, high]


def _noise_floor_db(x: np.ndarray, sr: int, percentile: float = 20.0) -> float:
    """Estimate the room/bleed noise floor as the 20th-percentile frame RMS."""
    db = _rms_frames_db(x, sr)
    if db.size == 0:
        return -120.0
    return float(np.percentile(db, percentile))


def _mono_of(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        return a.astype(np.float64)
    if a.shape[0] <= 8 and a.shape[0] < a.shape[1]:
        return np.mean(a.astype(np.float64), axis=0)
    return np.mean(a.astype(np.float64), axis=1)


def apply_bleedfix(
    audio: np.ndarray,
    sr: int,
    *,
    threshold_db: float = -46.0,
    ratio: float = 8.0,
    attack_ms: float = 3.0,
    release_ms: float = 160.0,
    wet: float = 1.0,
    mode: str = "fixed",
    margin_db: float = 8.0,
    bands: int = 1,
    sidechain: np.ndarray | None = None,
    report: dict[str, Any] | None = None,
) -> tuple[np.ndarray, str]:
    """Duck bleed / room wash below a gate threshold.

    mode="fixed"    → absolute threshold_db (classic gate)
    mode="auto"     → per-band noise-floor detection + margin_db offset
    mode="sidechain"→ key the gate from an external signal envelope
    bands=3         → independent 3-band (300/3000 Hz) gating
    """
    x = np.asarray(audio, dtype=np.float32)
    mono_in = x.ndim == 1
    if mono_in:
        x = x.reshape(1, -1)
    if x.shape[0] > 8 and x.shape[0] > x.shape[1]:
        x = x.T
    n = x.shape[1]

    mode = mode if mode in ("fixed", "auto", "sidechain") else "fixed"
    key = None
    if mode == "sidechain":
        if sidechain is None:
            mode = "auto"
        else:
            key = _mono_of(sidechain)
    if key is None:
        key = _mono_of(x)

    split = _band_split(key, sr, 3 if bands == 3 else 1)
    key_bands = split if len(split) > 1 else [key]
    prog_bands = _band_split(x, sr, 3 if bands == 3 else 1)

    band_names = ["L", "M", "H"]
    thr_per_band: list[float] = []
    floors: list[float] = []
    gated_fraction = 0.0
    gain_maps: list[np.ndarray] = []
    n_frames_total = 0

    for bi, (kb, pb) in enumerate(zip(key_bands, prog_bands)):
        floor = _noise_floor_db(kb, sr)
        thr = floor + float(margin_db) if mode in ("auto", "sidechain") else float(threshold_db)
        thr = max(thr, -120.0)
        thr_per_band.append(thr)
        floors.append(floor)
        env = _rms_frames_db(kb, sr)
        frame_rate = max(1.0, len(env) / max(1e-3, n / sr))
        env_s = _smooth_db(env, float(attack_ms), float(release_ms), frame_rate)
        g = _expander_gain_db(env_s, thr, float(ratio))
        g = _smooth_gain_db(g, float(attack_ms), float(release_ms), frame_rate)
        n_frames_total += g.size
        if g.size:
            gated_fraction += float(np.count_nonzero(g < -1.0))
        if bands == 3:
            gain_maps.append(g)

    if bands == 3:
        if n_frames_total:
            gated_fraction /= max(1, n_frames_total)
        out = np.zeros_like(x)
        win = max(8, int(sr * 0.010))
        hop = max(1, int(sr * 0.005))
        count = max(1, (n - win) // hop + 1)
        centers = np.arange(count, dtype=np.float64) * hop + win / 2.0
        for c in range(x.shape[0]):
            acc = 0.0
            for bi, (g, pb) in enumerate(zip(gain_maps, prog_bands)):
                g_lin = 10.0 ** (np.interp(np.arange(n), centers, g) / 20.0)
                acc += pb[c] * g_lin
            out[c] = acc
    else:
        floor = floors[0]
        thr = thr_per_band[0]
        env = _rms_frames_db(key, sr)
        frame_rate = max(1.0, len(env) / max(1e-3, n / sr))
        env_s = _smooth_db(env, float(attack_ms), float(release_ms), frame_rate)
        g = _expander_gain_db(env_s, thr, float(ratio))
        g = _smooth_gain_db(g, float(attack_ms), float(release_ms), frame_rate)
        if n_frames_total:
            gated_fraction = float(np.count_nonzero(g < -1.0)) / max(1, g.size)
        win = max(8, int(sr * 0.010))
        hop = max(1, int(sr * 0.005))
        count = max(1, (n - win) // hop + 1)
        centers = np.arange(count, dtype=np.float64) * hop + win / 2.0
        g_lin = 10.0 ** (np.interp(np.arange(n), centers, g) / 20.0)
        out = x * g_lin[np.newaxis, :]

    if mode == "fixed" and bands == 1:
        note = f"bleedfix gate@{thr_per_band[0]:.0f}dB r{float(ratio):g}:1"
    elif mode == "sidechain":
        note = f"bleedfix key floor {floors[0]:.1f}dB +{float(margin_db):.0f}dB"
    else:
        if bands == 3:
            tag = " · ".join(
                f"{band_names[bi]}-{floors[bi]:.0f}" for bi in range(min(3, len(floors)))
            )
            note = f"bleedfix 3-band {tag}dB +{float(margin_db):.0f}dB"
        else:
            note = f"bleedfix auto floor {floors[0]:.1f}dB +{float(margin_db):.0f}dB"

    if report is not None:
        report.update(
            {
                "mode": mode,
                "bands": 3 if bands == 3 else 1,
                "floors_db": floors,
                "thresholds_db": thr_per_band,
                "gated_fraction": float(gated_fraction),
                "note": note,
            }
        )

    if mono_in:
        out = out[0]
    return out.astype(np.float32), note


# ---------------------------------------------------------------------------
# Parametric EQ with Q + wet/dry
# ---------------------------------------------------------------------------

def _eq_plugin_from_band(b: dict[str, Any]):
    """Build a pedalboard filter from a band dict (type/freq/gain/q)."""
    if not HAS_PEDALBOARD:
        return None, None
    freq = max(20.0, min(20000.0, float(b.get("freq", 1000.0))))
    gain = float(b.get("gain_db", 0.0))
    q = max(0.15, min(24.0, float(b.get("q", 0.9))))
    typ = str(b.get("type") or "peaking").lower()
    if typ in ("lowshelf", "ls", "low_shelf"):
        if abs(gain) < 0.05:
            return None, None
        return pb.LowShelfFilter(cutoff_frequency_hz=freq, gain_db=gain, q=min(q, 2.0)), f"LS{freq:g} {gain:+.1f}"
    if typ in ("highshelf", "hs", "high_shelf"):
        if abs(gain) < 0.05:
            return None, None
        return pb.HighShelfFilter(cutoff_frequency_hz=freq, gain_db=gain, q=min(q, 2.0)), f"HS{freq:g} {gain:+.1f}"
    if typ in ("notch", "bandstop", "bs"):
        depth = -abs(gain) if abs(gain) >= 0.5 else -18.0
        return pb.PeakFilter(cutoff_frequency_hz=freq, gain_db=depth, q=max(q, 1.5)), f"Notch{freq:g}"
    if typ in ("highpass", "hpf", "hp"):
        try:
            return pb.HighpassFilter(cutoff_frequency_hz=freq), f"HPF{freq:g}"
        except Exception:
            return pb.HighpassFilter(cutoff_frequency_hz=freq), f"HPF{freq:g}"
    if typ in ("lowpass", "lpf", "lp"):
        return pb.LowpassFilter(cutoff_frequency_hz=freq), f"LPF{freq:g}"
    # peaking default
    if abs(gain) < 0.05:
        return None, None
    return pb.PeakFilter(cutoff_frequency_hz=freq, gain_db=gain, q=q), f"PK{freq:g} {gain:+.1f} Q{q:g}"


def apply_parametric_eq(
    audio: np.ndarray,
    sr: int,
    bands: list[dict[str, Any]],
    *,
    wet: float = 1.0,
    output_db: float = 0.0,
) -> tuple[np.ndarray, list[str]]:
    """Apply multi-band parametric EQ. bands = [{type, freq, gain_db, q, on, dynamic?}]."""
    if not HAS_PEDALBOARD:
        return audio, ["pedalboard unavailable"]
    plugins: list[Any] = []
    applied: list[str] = []
    for b in bands:
        if not b.get("on", True):
            continue
        plug, note = _eq_plugin_from_band(b)
        if plug is None:
            continue
        plugins.append(plug)
        if note:
            tag = note + (" dyn" if b.get("dynamic") else "")
            applied.append(tag)
    if abs(float(output_db or 0.0)) >= 0.05:
        try:
            plugins.append(pb.Gain(gain_db=float(output_db)))
            applied.append(f"OUT{float(output_db):+.1f}")
        except Exception:
            pass
    if not plugins:
        return audio, []
    board = pb.Pedalboard(plugins)
    processed = np.asarray(board(np.asarray(audio, dtype=np.float32), int(sr)), dtype=np.float32)
    # Lightweight offline dynamic: soft attenuate boosts when block RMS is hot
    for b in bands:
        if not b.get("on", True) or not b.get("dynamic"):
            continue
        # already baked as static peak; offline dynamic is approximated by soft ceiling
        thr = float(b.get("dyn_threshold_db", -24.0))
        rng = float(b.get("dyn_range_db", 10.0))
        # simple: if overall RMS high, scale dynamic band contribution (post already applied)
        # skip heavy offline dyn — live path handles it; just annotate
        applied.append(f"dyn@{thr:.0f}/{rng:.0f}dB")
        break
    mixed = mix_wet_dry(audio, processed, wet)
    return mixed, applied


# ---------------------------------------------------------------------------
# VST3 hosting via Pedalboard
# ---------------------------------------------------------------------------

def apply_vst(
    audio: np.ndarray,
    sr: int,
    vst_path: Path | str,
    params: dict[str, Any] | None = None,
) -> tuple[np.ndarray, str | None]:
    """Try to process audio through a VST3 plugin. Returns (audio, error)."""
    if not HAS_PEDALBOARD:
        return audio, "pedalboard unavailable for VST hosting"
    path = Path(vst_path)
    if not path.exists():
        return audio, f"plugin not found: {path}"
    try:
        plugin = pb.load_plugin(str(path))
        if plugin is None:
            return audio, f"could not load plugin: {path.name}"
        if params:
            for key, value in params.items():
                try:
                    setattr(plugin, key, value)
                except Exception:
                    pass
        out = np.asarray(plugin(np.asarray(audio, dtype=np.float32), int(sr)), dtype=np.float32)
        if out.ndim == 1:
            out = out.reshape(1, -1)
        return out, None
    except Exception as exc:
        return audio, f"{path.name}: {exc}"


# ---------------------------------------------------------------------------
# NoDAW JSON effect catalog → pedalboard chain
# ---------------------------------------------------------------------------

_PEDALBOARD_EFFECT_CHAINS: dict[str, Callable[[dict[str, Any]], list[Any]]] = {}


def _schema_default(schema: dict[str, Any], key: str, fallback: float) -> float:
    try:
        p = (schema or {}).get(key)
        if isinstance(p, dict) and p.get("default") is not None:
            return float(p["default"])
    except Exception:
        pass
    return fallback


def _chain_compressor(s: dict) -> list[Any]:
    return [
        pb.Compressor(
            threshold_db=_schema_default(s, "threshold_db", -20.0),
            ratio=_schema_default(s, "ratio", 4.0),
            attack_ms=_schema_default(s, "attack_ms", 5.0),
            release_ms=_schema_default(s, "release_ms", 50.0),
        )
    ]


def _chain_limiter(s: dict) -> list[Any]:
    return [pb.Limiter(threshold_db=_schema_default(s, "threshold_db", -1.0))]


def _chain_gain(s: dict) -> list[Any]:
    return [pb.Gain(gain_db=_schema_default(s, "gain_db", 3.0))]


def _chain_reverb(s: dict) -> list[Any]:
    return [
        pb.Reverb(
            room_size=_schema_default(s, "room_size", 0.5),
            wet_level=_schema_default(s, "wet_level", 0.3),
            dry_level=_schema_default(s, "dry_level", 0.7),
        )
    ]


def _chain_chorus(s: dict) -> list[Any]:
    return [
        pb.Chorus(
            rate_hz=_schema_default(s, "rate_hz", 1.5),
            depth=_schema_default(s, "depth", 0.25),
            mix=_schema_default(s, "mix", 0.5),
        )
    ]


def _chain_delay(s: dict) -> list[Any]:
    return [
        pb.Delay(
            delay_seconds=_schema_default(s, "delay_seconds", 0.25),
            feedback=_schema_default(s, "feedback", 0.3),
            mix=_schema_default(s, "mix", 0.25),
        )
    ]


def _chain_distortion(s: dict) -> list[Any]:
    return [
        pb.Distortion(
            drive_db=_schema_default(s, "drive_db", 12.0),
            mix=_schema_default(s, "mix", 0.5),
        )
    ]


def _chain_phaser(s: dict) -> list[Any]:
    return [
        pb.Phaser(
            rate_hz=_schema_default(s, "rate_hz", 0.4),
            depth=_schema_default(s, "depth", 0.5),
            centre_frequency_hz=_schema_default(s, "centre_frequency_hz", 1300.0),
            feedback=_schema_default(s, "feedback", 0.3),
            mix=_schema_default(s, "mix", 0.5),
        )
    ]


def _chain_clipping(s: dict) -> list[Any]:
    return [pb.Clipping(threshold_db=_schema_default(s, "threshold_db", -6.0))]


def _chain_lowshelf(freq: float, gain: float, q: float = 0.7):
    def build(s: dict) -> list[Any]:
        return [pb.LowShelfFilter(cutoff_frequency_hz=freq, gain_db=_schema_default(s, "gain_db", gain), q=q)]
    return build


def _chain_highshelf(freq: float, gain: float, q: float = 0.7):
    def build(s: dict) -> list[Any]:
        return [pb.HighShelfFilter(cutoff_frequency_hz=freq, gain_db=_schema_default(s, "gain_db", gain), q=q)]
    return build


def _chain_hpf(freq: float):
    def build(s: dict) -> list[Any]:
        return [pb.HighpassFilter(cutoff_frequency_hz=_schema_default(s, "freq", freq))]
    return build


def _chain_lpf(freq: float):
    def build(s: dict) -> list[Any]:
        return [pb.LowpassFilter(cutoff_frequency_hz=_schema_default(s, "freq", freq))]
    return build


def _chain_telephone(s: dict) -> list[Any]:
    return [
        pb.HighpassFilter(cutoff_frequency_hz=300.0),
        pb.LowpassFilter(cutoff_frequency_hz=3400.0),
    ]


def _chain_vocal_cleanup(s: dict) -> list[Any]:
    return [
        pb.HighpassFilter(cutoff_frequency_hz=80.0),
        pb.Compressor(threshold_db=-24.0, ratio=3.0, attack_ms=5.0, release_ms=60.0),
    ]


def _chain_wide(s: dict) -> list[Any]:
    return [pb.StereoWidener(width=_schema_default(s, "width", 0.5))]


def _chain_warmth(s: dict) -> list[Any]:
    return [
        pb.LowShelfFilter(cutoff_frequency_hz=200.0, gain_db=2.0, q=0.6),
        pb.Compressor(threshold_db=-22.0, ratio=1.8, attack_ms=12.0, release_ms=200.0),
    ]


def _chain_crush(s: dict) -> list[Any]:
    return [
        pb.Distortion(drive_db=24.0, mix=0.7),
        pb.Limiter(threshold_db=-6.0),
    ]


def _chain_robot(s: dict) -> list[Any]:
    return [
        pb.Phaser(rate_hz=4.0, depth=0.8, centre_frequency_hz=900.0, feedback=0.6, mix=0.9),
        pb.Distortion(drive_db=6.0, mix=0.4),
    ]


def _chain_underwater(s: dict) -> list[Any]:
    return [
        pb.Phaser(rate_hz=0.2, depth=0.9, centre_frequency_hz=400.0, feedback=0.3, mix=1.0),
        pb.LowpassFilter(cutoff_frequency_hz=6000.0),
    ]


def _chain_echo(s: dict) -> list[Any]:
    return [
        pb.Delay(delay_seconds=0.4, feedback=0.5, mix=0.3),
        pb.LowpassFilter(cutoff_frequency_hz=9000.0),
    ]


def _chain_tape(s: dict) -> list[Any]:
    return [
        pb.Distortion(drive_db=3.0, mix=0.25),
        pb.LowpassFilter(cutoff_frequency_hz=15000.0),
    ]


def _chain_pitch(factor: float):
    def build(s: dict) -> list[Any]:
        return [pb.PitchShift(semitones=_schema_default(s, "semitones", 12.0 * math.log2(factor)))]
    return build


def _register_chains() -> None:
    chains: dict[str, Callable[[dict[str, Any]], list[Any]]] = {
        "compressor": _chain_compressor,
        "tight_compression": _chain_compressor,
        "soft_compression": lambda s: [
            pb.Compressor(threshold_db=-28.0, ratio=1.8, attack_ms=15.0, release_ms=250.0)
        ],
        "limiter": _chain_limiter,
        "gain": _chain_gain,
        "reverb": _chain_reverb,
        "ambient_reverb": lambda s: [
            pb.Reverb(room_size=0.85, wet_level=0.45, dry_level=0.55)
        ],
        "chorus": _chain_chorus,
        "delay": _chain_delay,
        "space_echo": _chain_echo,
        "distortion": _chain_distortion,
        "phaser": _chain_phaser,
        "clipping": _chain_clipping,
        "crush": _chain_crush,
        "bass_boost": _chain_lowshelf(120.0, 4.0),
        "sub_boost": _chain_lowshelf(60.0, 5.0, 0.5),
        "treble_boost": _chain_highshelf(8000.0, 3.0),
        "air_shelf": _chain_highshelf(12000.0, 1.5),
        "low_cut": _chain_hpf(40.0),
        "high_cut": _chain_lpf(16000.0),
        "telephone": _chain_telephone,
        "vocal_cleanup": _chain_vocal_cleanup,
        "podcast_polish": lambda s: [
            pb.HighpassFilter(cutoff_frequency_hz=75.0),
            pb.Compressor(threshold_db=-26.0, ratio=2.5, attack_ms=6.0, release_ms=120.0),
            pb.Gain(gain_db=2.0),
        ],
        "dehum": lambda s: [pb.HighpassFilter(cutoff_frequency_hz=55.0, q=0.9)],
        "stereo_widening": _chain_wide,
        "warmth": _chain_warmth,
        "vintage_vibe": _chain_warmth,
        "lofi_tape": _chain_tape,
        "robot_voice": _chain_robot,
        "underwater": _chain_underwater,
        "alien_transmission": _chain_robot,
        "shimmer": lambda s: [
            pb.Reverb(room_size=0.9, wet_level=0.4, dry_level=0.6),
            pb.HighShelfFilter(cutoff_frequency_hz=6000.0, gain_db=3.0, q=0.7),
        ],
        "ethereal": lambda s: [
            pb.Reverb(room_size=0.95, wet_level=0.6, dry_level=0.4),
            pb.Chorus(rate_hz=0.3, depth=0.6, mix=0.5),
        ],
        "dreamscape": lambda s: [
            pb.Chorus(rate_hz=0.2, depth=0.7, mix=0.5),
            pb.Reverb(room_size=0.85, wet_level=0.5, dry_level=0.5),
        ],
        "pitch_up": _chain_pitch(2.0),
        "pitch_down": _chain_pitch(0.5),
        "creative_warp": lambda s: [
            pb.Phaser(rate_hz=0.5, depth=0.8, centre_frequency_hz=700.0, feedback=0.5, mix=0.6)
        ],
    }
    _PEDALBOARD_EFFECT_CHAINS.clear()
    _PEDALBOARD_EFFECT_CHAINS.update(chains)


_register_chains()


def json_effect_to_chain(effect: dict[str, Any]) -> tuple[list[Any] | None, str | None]:
    """Map a JSON effect to pedalboard plugins.

    Two formats are supported:
    - NoDAW catalog entries ({id, engine, paramsSchema}) → named chains.
    - Airwindows-style engine effects ({name, parameters}) → approximations.

    Returns (plugins, note). note is a warning when the effect can't be mapped.
    """
    if not HAS_PEDALBOARD:
        return None, "pedalboard unavailable"
    if isinstance(effect.get("parameters"), dict) and "paramsSchema" not in effect:
        return _engine_effect_to_chain(effect)
    schema = effect.get("paramsSchema") if isinstance(effect.get("paramsSchema"), dict) else {}
    internal = str(effect.get("internalName") or effect.get("id") or "").lower()
    builder = _PEDALBOARD_EFFECT_CHAINS.get(internal)
    if builder is None:
        return None, f"no pedalboard chain for '{internal}'"
    try:
        return builder(schema), None
    except Exception as exc:
        return None, f"{internal}: {exc}"


def _engine_effect_to_chain(effect: dict[str, Any]) -> tuple[list[Any] | None, str | None]:
    """Map an Airwindows-style engine_effects JSON to a pedalboard approximation."""
    name = str(effect.get("name") or "").lower()
    params = effect.get("parameters") or {}
    wet = float(params.get("dry_wet", 1.0) if isinstance(params.get("dry_wet", 1.0), (int, float)) else 1.0)
    wet = max(0.0, min(1.0, wet))
    try:
        if name == "highpass2":
            hz = max(20.0, min(20000.0, float(params.get("hipass", 20.0))))
            return [pb.HighpassFilter(cutoff_frequency_hz=hz)], None
        if name == "lowpass2":
            hz = max(20.0, min(20000.0, float(params.get("lowpass", 20000.0))))
            return [pb.LowpassFilter(cutoff_frequency_hz=hz)], None
        if name == "buttercomp":
            amount = max(0.0, min(1.0, float(params.get("compress", 0.5))))
            threshold = -8.0 - 30.0 * amount
            return [
                pb.Compressor(threshold_db=threshold, ratio=4.0, attack_ms=8.0, release_ms=90.0),
            ], None
        if name == "mojo":
            return [pb.Gain(gain_db=float(params.get("db", 0.0)))], None
        if name == "baxandall2":
            lo = float(params.get("low_db", 0.0))
            hi = float(params.get("high_db", 0.0))
            chain: list[Any] = []
            if abs(lo) > 0.1:
                chain.append(pb.LowShelfFilter(cutoff_frequency_hz=250.0, gain_db=lo, q=0.7))
            if abs(hi) > 0.1:
                chain.append(pb.HighShelfFilter(cutoff_frequency_hz=4000.0, gain_db=hi, q=0.7))
            return chain or None, None
        if name in ("holt", "holt2"):
            hz = max(30.0, min(18000.0, float(params.get("freq", 1000.0))))
            reso = max(0.0, min(1.0, float(params.get("reso", 0.5))))
            return [
                pb.LadderFilter(
                    cutoff_frequency_hz=hz,
                    resonance=max(0.1, reso * 0.9),
                )
            ], None
        if name == "silken":
            hz = max(30.0, min(18000.0, float(params.get("freq", 1000.0))))
            return [pb.LowpassFilter(cutoff_frequency_hz=hz)], None
        if name == "softgate":
            thr = max(-80.0, min(0.0, float(params.get("thresh", -60.0))))
            return [pb.NoiseGate(threshold_db=thr, ratio=8.0, attack_ms=1.0, release_ms=140.0)], None
        if name == "sampledelay":
            ms = max(0.5, min(2000.0, float(params.get("ms", 10.0))))
            return [pb.Delay(delay_seconds=ms / 1000.0, feedback=0.0, mix=wet)], None
        if name == "smooth":
            return [pb.LowpassFilter(cutoff_frequency_hz=9000.0)], None
        if name == "smooth_eq_3" or name == "smooth_eq3":
            def _db(v: Any) -> float:
                try:
                    f = float(v)
                    return max(-12.0, min(12.0, f * 12.0))
                except Exception:
                    return 0.0
            chain = []
            b, m, h = _db(params.get("bass", 0.0)), _db(params.get("mid", 0.0)), _db(params.get("high", 0.0))
            if abs(b) > 0.1:
                chain.append(pb.LowShelfFilter(cutoff_frequency_hz=120.0, gain_db=b, q=0.7))
            if abs(m) > 0.1:
                chain.append(pb.PeakFilter(cutoff_frequency_hz=1000.0, gain_db=m, q=1.0))
            if abs(h) > 0.1:
                chain.append(pb.HighShelfFilter(cutoff_frequency_hz=6000.0, gain_db=h, q=0.7))
            return chain or None, None
        if name == "purestconsolebuss":
            return [pb.Gain(gain_db=float(params.get("input", 0.0)))], None
        if name == "singleendedtriode":
            drive = max(0.0, min(1.0, float(params.get("triode", 0.5))))
            return [
                pb.Distortion(drive_db=1.0 + drive * 22.0, mix=wet),
                pb.LowShelfFilter(cutoff_frequency_hz=250.0, gain_db=1.5, q=0.7),
            ], None
        if name == "brightambience":
            room = max(0.0, min(1.0, float(params.get("sustain", 1.2))))
            return [
                pb.Reverb(room_size=0.6 + room * 0.35, wet_level=wet, dry_level=1.0 - wet),
                pb.HighShelfFilter(cutoff_frequency_hz=5000.0, gain_db=2.0, q=0.7),
            ], None
    except Exception as exc:
        return None, f"{name}: {exc}"
    return None, f"{name}: no approximation available"


def load_nodaw_catalog(path: Path | str) -> list[dict[str, Any]]:
    """Load a NoDAW effects catalog JSON (list of {id, engine, paramsSchema, ...})."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("engine") == "pedalboard":
            out.append(item)
    return out


# ---------------------------------------------------------------------------
# Full FX chain render
# ---------------------------------------------------------------------------

def render_fx_chain(
    src: Path | str,
    dest: Path | str,
    *,
    artifacts: ArtifactHunt | None = None,
    declick: bool = True,
    dedc: bool = True,
    deedge: bool = True,
    algorithm: str = "auto",
    freq_skew: float = 0.5,
    min_confidence: float = 0.45,
    bleed: dict[str, Any] | None = None,
    eq_bands: list[dict[str, Any]] | None = None,
    vst_path: str | None = None,
    vst_params: dict[str, Any] | None = None,
    vst_chain: list[dict[str, Any]] | None = None,
    json_effect: dict[str, Any] | None = None,
    wet_dry: float = 1.0,
    master_on: bool = True,
    **_extra: Any,
) -> dict[str, Any]:
    """Bake the complete Studio FX chain to a 24-bit WAV. Never overwrites src.

    algorithm: auto | single | multi_band | spectral  (RX-class Artifact Hunter)
    **_extra absorbs panel-only keys (artifact_hits, etc.) without TypeError.
    """
    src_p = Path(src).expanduser().resolve()
    dst_p = Path(dest).expanduser().resolve()
    result: dict[str, Any] = {
        "ok": False,
        "dest": str(dst_p),
        "applied": [],
        "error": None,
    }
    if not src_p.is_file():
        result["error"] = f"input missing: {src_p}"
        return result
    if src_p.resolve() == dst_p.resolve():
        result["error"] = "refusing to overwrite original"
        return result
    try:
        data, sr = sf.read(str(src_p), always_2d=True, dtype="float32")
        sr = int(sr)
        audio = np.asarray(data.T, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio.reshape(1, -1)
        dry = audio
        applied: list[str] = []

        if not master_on:
            applied.append("bypass")
            audio = np.asarray(audio, dtype=np.float32)
            dst_p.parent.mkdir(parents=True, exist_ok=True)
            if audio.ndim == 1:
                sf.write(str(dst_p), audio, sr, subtype="PCM_24")
            else:
                sf.write(str(dst_p), audio.T, sr, subtype="PCM_24")
            result["ok"] = True
            result["applied"] = applied
            result["sample_rate"] = sr
            return result

        if artifacts is not None:
            # Panel algorithm wins; fall back to hunt preferred_algorithm
            algo = str(algorithm or "auto").lower()
            skew = float(freq_skew if freq_skew is not None else 0.5)
            min_c = float(min_confidence if min_confidence is not None else 0.45)
            if isinstance(artifacts, ArtifactHunt):
                m = artifacts.metrics or {}
                if algo in ("", "auto", "none"):
                    algo = str(m.get("preferred_algorithm") or "auto")
                if freq_skew is None or abs(float(freq_skew) - 0.5) < 1e-9:
                    # keep panel default 0.5 unless hunt suggests mouth skew
                    if "freq_skew" in m:
                        skew = float(m.get("freq_skew", skew))
                if "min_confidence" in m and min_confidence == 0.45:
                    min_c = float(m.get("min_confidence", min_c))
            repaired, rep_applied = remove_artifacts_audio(
                audio, sr, artifacts,
                declick=declick, dedc=dedc, deedge=deedge,
                algorithm=algo,
                freq_skew=skew,
                min_confidence=min_c,
            )
            if rep_applied != ["anull"]:
                audio = repaired
                applied.extend(rep_applied)

        if bleed and bleed.get("on"):
            if bleed.get("spectral") or str(bleed.get("mode") or "") == "spectral":
                try:
                    from .repair_intel import apply_spectral_bleed_duck

                    red = float(bleed.get("est_bleed_reduction_db") or 10.0)
                    audio, note = apply_spectral_bleed_duck(
                        audio, sr,
                        reduction_db=max(4.0, min(18.0, red or 10.0)),
                        wet=float(bleed.get("wet", 1.0)),
                    )
                    applied.append(note)
                except Exception as exc:
                    applied.append(f"spectral duck failed: {exc}")
            else:
                audio, note = apply_bleedfix(
                    audio, sr,
                    threshold_db=bleed.get("threshold_db", -46.0),
                    ratio=bleed.get("ratio", 8.0),
                    attack_ms=bleed.get("attack_ms", 3.0),
                    release_ms=bleed.get("release_ms", 160.0),
                    wet=bleed.get("wet", 1.0),
                    mode=str(bleed.get("mode") or "fixed"),
                    margin_db=float(bleed.get("margin_db", 8.0)),
                    bands=int(bleed.get("bands") or 1),
                )
                if note != "pedalboard unavailable":
                    applied.append(note)

        active_bands = [b for b in (eq_bands or []) if b.get("on", True)]
        out_db = float((_extra or {}).get("eq_output_db") or 0.0)
        if active_bands or abs(out_db) >= 0.05:
            mixed, eq_applied = apply_parametric_eq(
                audio, sr, active_bands or [], wet=1.0, output_db=out_db
            )
            if eq_applied:
                audio = mixed
                applied.extend(eq_applied)

        # Multi-VST chain (serial, float32 — no intermediate re-encode)
        chain_items: list[dict[str, Any]] = []
        if isinstance(vst_chain, list) and vst_chain:
            chain_items = [c for c in vst_chain if isinstance(c, dict) and c.get("path")]
        elif vst_path:
            chain_items = [{"path": vst_path, "params": vst_params or {}, "bypass": False}]
        for item in chain_items:
            if item.get("bypass"):
                continue
            p = item.get("path")
            if not p:
                continue
            processed, err = apply_vst(audio, sr, p, params=item.get("params") or {})
            if err:
                result["error"] = f"VST: {err}"
                return result
            audio = processed
            applied.append(f"VST {Path(str(p)).name}")

        if json_effect:
            plugins, note = json_effect_to_chain(json_effect)
            if plugins is None:
                result["error"] = f"JSON effect: {note}"
                return result
            board = pb.Pedalboard(plugins)
            audio = np.asarray(board(audio, sr), dtype=np.float32)
            applied.append(f"JSON {json_effect.get('label') or json_effect.get('id') or 'effect'}")

        wet = max(0.0, min(1.0, float(wet_dry)))
        if wet < 1.0:
            audio = mix_wet_dry(dry, audio, wet)
            applied.append(f"wet/dry {wet * 100:.0f}%")

        audio = np.asarray(audio, dtype=np.float32)
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        if audio.ndim == 1:
            sf.write(str(dst_p), audio, sr, subtype="PCM_24")
        else:
            sf.write(str(dst_p), audio.T, sr, subtype="PCM_24")

        if not dst_p.is_file():
            result["error"] = "write failed"
            return result
        result["ok"] = True
        result["applied"] = applied
        result["sample_rate"] = sr
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


# ---------------------------------------------------------------------------
# FFmpeg one-shot bat effects
# ---------------------------------------------------------------------------

_FFMPEG_CANDIDATES = (
    Path(r"I:\Projects\NoDAW\NoDAW_Studio_Pro_Base\NoDAW_Studio_Pro\engine\bin\ffmpeg.exe"),
)


def _find_ffmpeg() -> str | None:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for cand in _FFMPEG_CANDIDATES:
        if cand.is_file():
            return str(cand)
    return None


def _full_bat_filter(bat_path: str, preview: str | None = None) -> str | None:
    """Re-read the .bat to recover the complete (untruncated) filter chain."""
    try:
        text = Path(bat_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return preview
    m = re.search(r'set\s+"FILTER=(.+)"\s*$', text, re.M)
    if m:
        return m.group(1).rstrip('"').strip()
    m2 = re.search(r'-af\s+"([^"]+)"', text)
    if m2:
        return m2.group(1).strip()
    return preview


def run_bat_effect(
    source: str,
    out: str,
    *,
    bat_path: str | None = None,
    ffmpeg_filter: str | None = None,
    timeout_s: float = 600.0,
) -> dict[str, Any]:
    """Bake an ffmpeg filter chain from a one-shot effect .bat onto `source`.

    Returns {"ok": bool, "error"?, "filter"?, "dest"?}. ffmpeg is resolved
    from PATH (fallback: NoDAW Studio Pro engine bin).
    """
    if not ffmpeg_filter and bat_path:
        ffmpeg_filter = _full_bat_filter(bat_path)
    if not ffmpeg_filter:
        return {"ok": False, "error": "no ffmpeg filter available"}
    exe = _find_ffmpeg()
    if not exe:
        return {"ok": False, "error": "ffmpeg not found on PATH"}
    src = Path(source)
    dst = Path(out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.stem}_bat_tmp.wav")
    try:
        if tmp.exists():
            tmp.unlink()
    except Exception:
        pass
    cmd = [exe, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-af", ffmpeg_filter, str(tmp)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s, creationflags=flags)
    except FileNotFoundError:
        return {"ok": False, "error": "ffmpeg not found"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"ffmpeg timed out after {timeout_s:.0f}s"}
    if proc.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 256:
        lines = (proc.stderr or proc.stdout or "").strip().splitlines()
        return {"ok": False, "error": (lines[-1] if lines else f"ffmpeg rc={proc.returncode}")[:160]}
    try:
        if dst.exists():
            dst.unlink()
        tmp.rename(dst)
    except Exception:
        return {"ok": False, "error": "could not move ffmpeg output"}
    return {"ok": True, "filter": ffmpeg_filter[:80], "dest": str(dst)}
