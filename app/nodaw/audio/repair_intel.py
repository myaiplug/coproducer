"""
Repair intelligence for CoProducer Studio.

Artifact Hunter (RX-class path):
  - Multi-feature click detection with fused confidence
  - Full multi-band (Linkwitz-Riley) per-band detect + repair + sum
  - Spectral / pixel repair: STFT bin masking + 2D neighbor inpaint
  - Click class labels (digital / mouth / clip_edge)
  - Repair ladder: multi_band | spectral | cubic | ar
  - Bleed program-safe auto search + spectral quiet duck
  - RepairPlan JSON for reproducible live + offline decisions

Designed to stay local, explainable, and music-safe (dry when clean).
"""

from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


@dataclass
class ArtifactHit:
    """One detected glitch with time bounds and confidence."""

    start_s: float
    end_s: float
    peak_db: float
    confidence: float  # 0..1
    kind: str = "digital"  # digital | mouth | clip_edge | dropout
    method: str = "cubic"  # cubic | ar | spectral | multi_band
    score: float = 0.0
    band: int | None = None  # multi-band index when applicable
    tf_mask: bool = False  # true when spectral/pixel region recommended

    def as_legacy_click(self) -> tuple[float, float, float]:
        return (self.start_s, self.end_s, self.peak_db)


@dataclass
class ArtifactHuntV2:
    hits: list[ArtifactHit] = field(default_factory=list)
    dropout_edges: list[tuple[float, float]] = field(default_factory=list)
    dc_offset: float = 0.0
    clipped_estimate: int = 0
    clipped_runs: list[tuple[int, int]] = field(default_factory=list)
    duration_s: float = 0.0
    error: str | None = None
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def clicks(self) -> list[tuple[float, float, float]]:
        """Legacy tuple form for LiveFX / older callers."""
        return [h.as_legacy_click() for h in self.hits if h.kind != "dropout"]

    @property
    def summary(self) -> str:
        if self.error:
            return f"artifact hunt failed: {self.error}"
        parts: list[str] = []
        by_kind: dict[str, int] = {}
        for h in self.hits:
            by_kind[h.kind] = by_kind.get(h.kind, 0) + 1
        for k, n in sorted(by_kind.items()):
            parts.append(f"{n} {k}")
        if self.dropout_edges:
            parts.append(f"{len(self.dropout_edges)} dropout edge(s)")
        if abs(self.dc_offset) > 0.004:
            parts.append(f"DC {self.dc_offset:+.4f}")
        if self.clipped_estimate:
            parts.append(f"{self.clipped_estimate} clipped")
        return "; ".join(parts) or "clean"


@dataclass
class RepairPlan:
    """Reproducible decision package for live + offline."""

    version: str = "2.0"
    created: float = field(default_factory=time.time)
    path: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    bleed: dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "RepairPlan":
        return cls(
            version=str(d.get("version", "2.0")),
            created=float(d.get("created") or time.time()),
            path=d.get("path"),
            artifacts=dict(d.get("artifacts") or {}),
            bleed=dict(d.get("bleed") or {}),
            note=str(d.get("note") or ""),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _db(v: float) -> float:
    return 20.0 * math.log10(max(float(v), 1e-12))


def _mono(audio: np.ndarray) -> np.ndarray:
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        return a.astype(np.float64)
    if a.shape[0] <= 8 and a.shape[0] < a.shape[1]:
        return np.mean(a.astype(np.float64), axis=0)
    return np.mean(a.astype(np.float64), axis=1)


def _channels(audio: np.ndarray) -> list[np.ndarray]:
    a = np.asarray(audio, dtype=np.float32)
    if a.ndim == 1:
        return [a]
    if a.shape[0] <= 8 and a.shape[0] < a.shape[1]:
        return [a[c] for c in range(a.shape[0])]
    return [np.mean(a, axis=1).astype(np.float32)]


def _rms_frames_db(x: np.ndarray, sr: int, win_ms: float = 10.0, hop_ms: float = 5.0) -> np.ndarray:
    n = x.shape[0]
    win = max(8, int(sr * win_ms / 1000.0))
    hop = max(1, int(sr * hop_ms / 1000.0))
    if n < win:
        win = max(1, n)
        hop = 1
    try:
        segs = np.lib.stride_tricks.sliding_window_view(x, win)[::hop]
    except Exception:
        segs = np.stack([x[s : s + win] for s in range(0, max(1, n - win + 1), hop)])
    rms = np.sqrt(np.mean(segs.astype(np.float64) ** 2, axis=1))
    return 20.0 * np.log10(np.maximum(rms, 1e-12))


# ---------------------------------------------------------------------------
# Multi-feature click detection
# ---------------------------------------------------------------------------


def _feature_diff_ratio(mono: np.ndarray, sr: int) -> np.ndarray:
    """Local |diff| / median(|diff|) — classic digital click cue."""
    try:
        from scipy.ndimage import median_filter
    except Exception:
        d = np.abs(np.diff(mono, prepend=mono[0]))
        return d / (np.median(d) + 1e-12)
    d = np.abs(np.diff(mono, prepend=mono[0]))
    win = max(9, int(sr * 0.0012))
    med = median_filter(d, size=win)
    return d / (med + 1e-12)


def _feature_hf_energy(mono: np.ndarray, sr: int) -> np.ndarray:
    """High-frequency residual energy ratio (mouth clicks / ticks)."""
    n = mono.size
    # 1st-order HF emphasis
    hp = np.diff(mono, prepend=mono[0])
    win = max(16, int(sr * 0.002))
    try:
        from scipy.ndimage import uniform_filter1d

        e_hf = uniform_filter1d(hp * hp, size=win)
        e_all = uniform_filter1d(mono * mono, size=win) + 1e-12
    except Exception:
        # crude block energy
        e_hf = np.convolve(hp * hp, np.ones(win) / win, mode="same")
        e_all = np.convolve(mono * mono, np.ones(win) / win, mode="same") + 1e-12
    return np.sqrt(e_hf / e_all)


def _feature_ar_residual(mono: np.ndarray, sr: int, order: int = 8) -> np.ndarray:
    """One-step AR prediction residual magnitude (impulse outliers)."""
    n = mono.size
    if n < order * 4:
        return np.zeros(n, dtype=np.float64)
    # Levinson-style via least squares on a short global model
    try:
        x = mono.astype(np.float64)
        # Build Toeplitz-ish LS for AR
        Y = x[order:]
        A = np.column_stack([x[order - k - 1 : n - k - 1] for k in range(order)])
        coef, *_ = np.linalg.lstsq(A, Y, rcond=None)
        pred = A @ coef
        resid = np.zeros(n, dtype=np.float64)
        resid[order:] = np.abs(Y - pred)
        # Normalize by local MAD
        try:
            from scipy.ndimage import median_filter

            med = median_filter(resid, size=max(9, int(sr * 0.002)))
        except Exception:
            med = np.median(resid) + np.zeros_like(resid)
        return resid / (med + 1e-12)
    except Exception:
        return np.zeros(n, dtype=np.float64)


def _merge_runs(
    mask: np.ndarray, max_gap: int = 6, max_len: int = 32
) -> list[tuple[int, int]]:
    n = mask.size
    runs: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and mask[j + 1]:
            j += 1
        runs.append((i, j))
        i = j + 1
    merged: list[tuple[int, int]] = []
    for s, e in runs:
        if merged and s - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))
    return [(s, e) for s, e in merged if (e - s + 1) <= max_len]


def detect_clicks_v2(mono: np.ndarray, sr: int) -> list[ArtifactHit]:
    """Fuse multi-feature detectors into confidence-ranked hits."""
    mono = np.asarray(mono, dtype=np.float64)
    n = mono.size
    if n < sr // 20:
        return []

    r_diff = _feature_diff_ratio(mono, sr)
    r_hf = _feature_hf_energy(mono, sr)
    r_ar = _feature_ar_residual(mono, sr)
    absx = np.abs(mono)

    # Normalize features to ~0..1-ish scores
    s_diff = np.clip((r_diff - 5.0) / 10.0, 0.0, 1.0)
    s_hf = np.clip((r_hf - 0.35) / 0.5, 0.0, 1.0)
    s_ar = np.clip((r_ar - 4.0) / 8.0, 0.0, 1.0)
    s_amp = np.clip((absx - 0.08) / 0.4, 0.0, 1.0)

    # Fused score — digital clicks need amp+diff; mouth needs HF
    fused = 0.45 * s_diff + 0.25 * s_ar + 0.20 * s_hf + 0.10 * s_amp
    mask = (fused > 0.42) & (absx > 0.04)
    max_len = int(sr * 0.0008) + 4
    runs = _merge_runs(mask, max_gap=6, max_len=max_len)

    hits: list[ArtifactHit] = []
    for s, e in runs:
        lo, hi = max(0, s - 2), min(n - 1, e + 2)
        conf = float(np.max(fused[lo : hi + 1]))
        peak = float(np.max(absx[lo : hi + 1]))
        if peak < 0.06 or conf < 0.40:
            continue
        # Classify
        hf_loc = float(np.mean(s_hf[lo : hi + 1]))
        diff_loc = float(np.mean(s_diff[lo : hi + 1]))
        length = e - s + 1
        if peak >= 0.98:
            kind = "clip_edge"
            method = "ar"
        elif hf_loc > 0.55 and diff_loc < 0.55 and length <= max(4, int(sr * 0.0004)):
            kind = "mouth"
            method = "cubic"
        else:
            kind = "digital"
            method = "cubic" if length <= 12 else "ar"
        # Confidence boost for multi-feature agreement
        agree = (diff_loc > 0.4) + (hf_loc > 0.4) + (float(np.mean(s_ar[lo : hi + 1])) > 0.4)
        conf = float(min(1.0, conf + 0.08 * max(0, agree - 1)))
        hits.append(
            ArtifactHit(
                start_s=lo / sr,
                end_s=hi / sr,
                peak_db=_db(peak),
                confidence=conf,
                kind=kind,
                method=method,
                score=conf * peak,
            )
        )
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:250]


def detect_dropouts(mono: np.ndarray, sr: int) -> list[tuple[float, float]]:
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


def hunt_artifacts_v2(
    audio: np.ndarray,
    sr: int,
    *,
    algorithm: str = "auto",
    sensitivity: float = 1.0,
) -> ArtifactHuntV2:
    """
    Full hunt. algorithm:
      auto | single | multi_band — multi_band runs LR4 per-band detection (RX-like).
    """
    a = np.asarray(audio, dtype=np.float32)
    hunt = ArtifactHuntV2(duration_s=float(a.shape[-1] / max(1, sr)))
    try:
        n = a.shape[-1]
        chans = _channels(a)
        mono = _mono(a)
        algo = (algorithm or "auto").lower()
        use_mb = algo in ("multi_band", "multiband", "mb", "auto")
        all_hits: list[ArtifactHit] = []
        for ch in chans:
            if use_mb:
                try:
                    all_hits.extend(
                        detect_clicks_multiband(ch.astype(np.float64), sr, sensitivity=sensitivity)
                    )
                except Exception:
                    all_hits.extend(detect_clicks_v2(ch.astype(np.float64), sr))
            else:
                all_hits.extend(detect_clicks_v2(ch.astype(np.float64), sr))
        # Merge overlapping across channels
        all_hits.sort(key=lambda h: h.start_s)
        merged: list[ArtifactHit] = []
        for h in all_hits:
            if merged and h.start_s <= merged[-1].end_s + 0.0003:
                prev = merged[-1]
                method = prev.method
                if h.method == "spectral" or prev.method == "spectral":
                    method = "spectral"
                elif h.method == "multi_band" or prev.method == "multi_band":
                    method = "multi_band"
                merged[-1] = ArtifactHit(
                    start_s=prev.start_s,
                    end_s=max(prev.end_s, h.end_s),
                    peak_db=max(prev.peak_db, h.peak_db),
                    confidence=max(prev.confidence, h.confidence),
                    kind=prev.kind if prev.confidence >= h.confidence else h.kind,
                    method=method,
                    score=max(prev.score, h.score),
                    band=None if prev.band != h.band else prev.band,
                    tf_mask=prev.tf_mask or h.tf_mask or method == "spectral",
                )
            else:
                merged.append(h)
        # Confidence floor for auto-repair
        hunt.hits = [h for h in merged if h.confidence >= 0.42][:300]
        # Promote long/mouth hits toward spectral method
        for h in hunt.hits:
            if h.kind == "mouth" or (h.end_s - h.start_s) > 0.0015:
                h.method = "spectral"
                h.tf_mask = True
        hunt.dropout_edges = detect_dropouts(mono, sr)
        hunt.dc_offset = float(np.mean(mono))
        ab = np.abs(a)
        hunt.clipped_estimate = int(np.sum(ab >= 0.9999))
        clip_mask = ab >= 0.9999
        flat = clip_mask.any(axis=0) if clip_mask.ndim == 2 else clip_mask
        runs: list[tuple[int, int]] = []
        i = 0
        while i < n:
            if not flat[i]:
                i += 1
                continue
            j = i
            while j + 1 < n and flat[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        hunt.clipped_runs = runs[:500]
        hunt.metrics = {
            "n_hits": len(hunt.hits),
            "n_digital": sum(1 for h in hunt.hits if h.kind == "digital"),
            "n_mouth": sum(1 for h in hunt.hits if h.kind == "mouth"),
            "n_clip_edge": sum(1 for h in hunt.hits if h.kind == "clip_edge"),
            "n_spectral": sum(1 for h in hunt.hits if h.method == "spectral"),
            "n_multi_band": sum(1 for h in hunt.hits if h.method == "multi_band"),
            "mean_confidence": float(np.mean([h.confidence for h in hunt.hits])) if hunt.hits else 0.0,
            "dc_offset": hunt.dc_offset,
            "algorithm": algo if use_mb else "single",
            "crest_db": float(
                20 * math.log10(max(float(np.max(np.abs(mono))), 1e-12))
                - 20 * math.log10(max(float(np.sqrt(np.mean(mono * mono))), 1e-12))
            ),
        }
    except Exception as exc:
        hunt.error = str(exc)
    return hunt


def hunt_file_v2(path: Path | str) -> ArtifactHuntV2:
    try:
        data, sr = sf.read(str(path), always_2d=True, dtype="float32")
        return hunt_artifacts_v2(data.T, int(sr))
    except Exception as exc:
        return ArtifactHuntV2(error=str(exc))


# ---------------------------------------------------------------------------
# Repair ladder
# ---------------------------------------------------------------------------


def _cubic_repair(x: np.ndarray, start: int, end: int, ctx: int = 3) -> None:
    n = x.shape[0]
    a0 = max(0, start - ctx)
    b0 = min(n - 1, end + ctx)
    if b0 - a0 < 2:
        x[start : end + 1] = 0.0
        return
    # Hermite-ish: endpoint values + slopes from outer context
    left = float(x[a0])
    right = float(x[b0])
    m0 = float(x[min(n - 1, a0 + 1)] - x[a0]) if a0 + 1 < n else 0.0
    m1 = float(x[b0] - x[max(0, b0 - 1)]) if b0 > 0 else 0.0
    seg = b0 - a0 + 1
    t = np.linspace(0.0, 1.0, seg)
    # Cubic Hermite basis
    h00 = 2 * t**3 - 3 * t**2 + 1
    h10 = t**3 - 2 * t**2 + t
    h01 = -2 * t**3 + 3 * t**2
    h11 = t**3 - t**2
    x[a0 : b0 + 1] = (h00 * left + h10 * m0 * seg + h01 * right + h11 * m1 * seg).astype(np.float32)


def _ar_repair(x: np.ndarray, start: int, end: int, order: int = 12) -> None:
    """Fill gap with AR forward/backward average."""
    n = x.shape[0]
    if end < start:
        return
    # Need context
    left_ctx = x[max(0, start - order * 3) : start]
    right_ctx = x[end + 1 : min(n, end + 1 + order * 3)]
    gap = end - start + 1
    if left_ctx.size < order and right_ctx.size < order:
        _cubic_repair(x, start, end)
        return
    fill_f = np.zeros(gap, dtype=np.float64)
    fill_b = np.zeros(gap, dtype=np.float64)
    try:
        if left_ctx.size >= order:
            Y = left_ctx[order:].astype(np.float64)
            A = np.column_stack(
                [left_ctx[order - k - 1 : left_ctx.size - k - 1] for k in range(order)]
            )
            coef, *_ = np.linalg.lstsq(A, Y, rcond=None)
            hist = list(left_ctx[-order:].astype(np.float64))
            for i in range(gap):
                pred = float(np.dot(coef, hist[::-1][:order]))
                fill_f[i] = pred
                hist.append(pred)
                hist = hist[-order:]
        if right_ctx.size >= order:
            rev = right_ctx[::-1]
            Y = rev[order:].astype(np.float64)
            A = np.column_stack([rev[order - k - 1 : rev.size - k - 1] for k in range(order)])
            coef, *_ = np.linalg.lstsq(A, Y, rcond=None)
            hist = list(rev[-order:].astype(np.float64))
            tmp = []
            for i in range(gap):
                pred = float(np.dot(coef, hist[::-1][:order]))
                tmp.append(pred)
                hist.append(pred)
                hist = hist[-order:]
            fill_b = np.array(tmp[::-1], dtype=np.float64)
        if left_ctx.size >= order and right_ctx.size >= order:
            w = np.linspace(0.0, 1.0, gap)
            fill = fill_f * (1 - w) + fill_b * w
        elif left_ctx.size >= order:
            fill = fill_f
        else:
            fill = fill_b
        x[start : end + 1] = fill.astype(np.float32)
    except Exception:
        _cubic_repair(x, start, end)


# ---------------------------------------------------------------------------
# Multi-band (Linkwitz-Riley style) + spectral pixel repair
# ---------------------------------------------------------------------------


def _lr_sos(sr: int, freq: float, btype: str):
    from scipy.signal import butter

    # 2nd-order butter twice → LR4 magnitude
    return butter(2, freq, btype=btype, fs=sr, output="sos")


def lr4_split_bands(x: np.ndarray, sr: int, cuts: tuple[float, ...] = (200.0, 2000.0, 8000.0)) -> list[np.ndarray]:
    """
    Complementary multi-band split via cascaded LR4 crossovers.
    Default 4 bands: LO / LM / HM / HI (RX-like multi-band declick).
    Sum of bands ≈ x (zero-phase filtfilt).
    """
    from scipy.signal import sosfiltfilt

    x = np.asarray(x, dtype=np.float64)
    cuts = tuple(sorted(float(c) for c in cuts if 20.0 < c < sr * 0.45))
    if not cuts:
        return [x.astype(np.float32)]
    remaining = x
    bands: list[np.ndarray] = []
    for fc in cuts:
        try:
            sos_lp = _lr_sos(sr, fc, "low")
            low = sosfiltfilt(sos_lp, remaining)
            low = sosfiltfilt(sos_lp, low)  # second pass → LR4
            high = remaining - low
            bands.append(low.astype(np.float32))
            remaining = high
        except Exception:
            bands.append(remaining.astype(np.float32))
            remaining = np.zeros_like(remaining)
            break
    bands.append(remaining.astype(np.float32))
    return bands


def lr4_sum_bands(bands: list[np.ndarray]) -> np.ndarray:
    if not bands:
        return np.zeros(0, dtype=np.float32)
    y = np.zeros_like(bands[0], dtype=np.float64)
    for b in bands:
        y = y + b.astype(np.float64)
    return y.astype(np.float32)


def detect_clicks_multiband(
    mono: np.ndarray,
    sr: int,
    *,
    cuts: tuple[float, ...] = (200.0, 2000.0, 8000.0),
    sensitivity: float = 1.0,
) -> list[ArtifactHit]:
    """
    Per-band detect then merge. Short broadband impulses light up many bands;
    mouth clicks prefer mid/high; rumble clicks prefer low.
    """
    mono = np.asarray(mono, dtype=np.float64)
    bands = lr4_split_bands(mono, sr, cuts)
    # Band-specific sensitivity (low less sensitive to kick thump)
    band_scale = []
    n_b = len(bands)
    for bi in range(n_b):
        # higher bands slightly more sensitive
        band_scale.append(0.85 + 0.15 * bi / max(1, n_b - 1))
    all_hits: list[ArtifactHit] = []
    for bi, b in enumerate(bands):
        # Temporarily lower/raise thresholds via scaling mono energy
        hits = detect_clicks_v2(b.astype(np.float64) * (1.0 / max(0.5, sensitivity * band_scale[bi])), sr)
        for h in hits:
            h.band = bi
            # mouth preference in high bands
            if bi >= n_b - 2 and h.kind == "digital" and h.confidence > 0.5:
                # reclassify borderline high-band as mouth if HF-like
                if h.peak_db < -6:
                    h.kind = "mouth"
            h.method = "multi_band"
            all_hits.append(h)
    # Merge time-overlapping hits across bands → broadband digital
    all_hits.sort(key=lambda h: h.start_s)
    merged: list[ArtifactHit] = []
    for h in all_hits:
        if merged and h.start_s <= merged[-1].end_s + 0.0004:
            prev = merged[-1]
            bands_hit = {prev.band, h.band}
            kind = prev.kind
            if len(bands_hit) >= 2 and prev.kind != "clip_edge":
                kind = "digital"
                method = "spectral" if (h.end_s - h.start_s) > 0.0006 else "multi_band"
            else:
                method = prev.method
            merged[-1] = ArtifactHit(
                start_s=min(prev.start_s, h.start_s),
                end_s=max(prev.end_s, h.end_s),
                peak_db=max(prev.peak_db, h.peak_db),
                confidence=min(1.0, max(prev.confidence, h.confidence) + 0.05 * (len(bands_hit) - 1)),
                kind=kind,
                method=method,
                score=max(prev.score, h.score),
                band=None if len(bands_hit) > 1 else prev.band,
                tf_mask=method == "spectral",
            )
        else:
            merged.append(h)
    merged.sort(key=lambda h: h.score, reverse=True)
    return merged[:300]


def _stft_frames(x: np.ndarray, n_fft: int, hop: int) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Return complex STFT (n_frames, n_bins), window, frame starts."""
    n = x.size
    win = np.hanning(n_fft).astype(np.float64)
    starts = list(range(0, max(1, n - n_fft + 1), hop))
    if not starts:
        starts = [0]
    frames = []
    for s in starts:
        seg = np.zeros(n_fft, dtype=np.float64)
        take = min(n_fft, n - s)
        if take > 0:
            seg[:take] = x[s : s + take] * win[:take]
        frames.append(np.fft.rfft(seg))
    return np.stack(frames, axis=0), win, starts


def _istft_frames(F: np.ndarray, win: np.ndarray, starts: list[int], n: int, n_fft: int, hop: int) -> np.ndarray:
    y = np.zeros(n, dtype=np.float64)
    wsum = np.zeros(n, dtype=np.float64)
    for i, s in enumerate(starts):
        seg = np.fft.irfft(F[i], n=n_fft).real * win
        e = min(n, s + n_fft)
        y[s:e] += seg[: e - s]
        wsum[s:e] += win[: e - s]
    wsum = np.maximum(wsum, 1e-8)
    return (y / wsum).astype(np.float32)


def spectral_pixel_repair_region(
    x: np.ndarray,
    start: int,
    end: int,
    sr: int,
    *,
    n_fft: int | None = None,
    hop: int | None = None,
    freq_skew: float = 0.5,
    pad_ms: float = 12.0,
) -> None:
    """
    RX-style spectral / pixel repair for a time region.

    - STFT of neighborhood
    - Mask frames that cover the glitch (optionally HF-weighted via freq_skew)
    - Inpaint magnitude from time neighbors; keep phase structure from edges
    - OLA resynthesis with edge crossfade
    """
    n = x.shape[0]
    gap = end - start + 1
    if gap <= 0:
        return
    # Long damage → AR first then light spectral polish
    if gap > int(sr * 0.05):
        _ar_repair(x, start, end)
        return

    n_fft = int(n_fft or (512 if sr <= 48000 else 1024))
    hop = int(hop or max(64, n_fft // 4))
    pad = max(n_fft, int(sr * pad_ms / 1000.0))
    a0 = max(0, start - pad)
    b0 = min(n, end + 1 + pad)
    seg = x[a0:b0].astype(np.float64)
    local_start = start - a0
    local_end = end - a0

    F, win, starts = _stft_frames(seg, n_fft, hop)
    n_frames, n_bins = F.shape
    mag = np.abs(F) + 1e-12
    phase = np.angle(F)

    # Frames overlapping the glitch interval
    bad = np.zeros(n_frames, dtype=bool)
    for i, s in enumerate(starts):
        fr0, fr1 = s, s + n_fft
        if fr1 > local_start and fr0 <= local_end:
            bad[i] = True
    if not np.any(bad):
        _cubic_repair(x, start, end)
        return

    # Frequency weighting: freq_skew 0 = all bins, 1 = emphasize highs (mouth)
    freqs = np.linspace(0.0, 1.0, n_bins)
    w_f = 0.35 + 0.65 * (freqs ** (0.5 + 1.5 * float(np.clip(freq_skew, 0.0, 1.0))))

    mag_out = mag.copy()
    # For each bad frame, interpolate magnitude from nearest good frames
    # + RX-style 2D "pixel" fill (time-neighbors + frequency median blur)
    good_idx = np.where(~bad)[0]
    if good_idx.size == 0:
        _ar_repair(x, start, end)
        return
    bad_idx = np.where(bad)[0]
    for i in bad_idx:
        # nearest previous / next good
        prev = good_idx[good_idx < i]
        nxt = good_idx[good_idx > i]
        if prev.size and nxt.size:
            p, q = int(prev[-1]), int(nxt[0])
            t = (i - p) / max(1, q - p)
            mag_i = (1 - t) * mag[p] + t * mag[q]
        elif prev.size:
            mag_i = mag[int(prev[-1])].copy()
        else:
            mag_i = mag[int(nxt[0])].copy()
        # 2D pixel patch: median of temporal neighbors + local freq smoothing
        if prev.size and nxt.size:
            stack = np.stack([mag[int(prev[-1])], mag_i, mag[int(nxt[0])]], axis=0)
            mag_i = np.median(stack, axis=0)
        # Frequency-axis 3-bin median (spectral "paint" continuity)
        if n_bins >= 3:
            pad = np.pad(mag_i, (1, 1), mode="edge")
            mag_i = np.median(
                np.stack([pad[:-2], pad[1:-1], pad[2:]], axis=0), axis=0
            )
        # Soft floor: never invent energy above local max of neighbors
        if prev.size and nxt.size:
            ceiling = np.maximum(mag[int(prev[-1])], mag[int(nxt[0])]) * 1.15
            mag_i = np.minimum(mag_i, ceiling)
        # Blend toward original lows if freq_skew high (preserve body / kick)
        mag_out[i] = mag[i] * (1.0 - w_f) + mag_i * w_f

    # Phase: linear interp of unwrapped phase at edges for bad frames
    phase_out = phase.copy()
    for i in bad_idx:
        prev = good_idx[good_idx < i]
        nxt = good_idx[good_idx > i]
        if prev.size and nxt.size:
            p, q = int(prev[-1]), int(nxt[0])
            t = (i - p) / max(1, q - p)
            # unwrap along time for each bin
            pp = np.unwrap(np.stack([phase[p], phase[q]], axis=0), axis=0)
            phase_out[i] = (1 - t) * pp[0] + t * pp[1]
        elif prev.size:
            phase_out[i] = phase[int(prev[-1])]
        else:
            phase_out[i] = phase[int(nxt[0])]

    F_out = mag_out * np.exp(1j * phase_out)
    y = _istft_frames(F_out, win, starts, seg.size, n_fft, hop)

    # Crossfade repaired segment into original at region bounds
    out = np.asarray(x, dtype=np.float32).copy()
    out[a0:b0] = y.astype(np.float32)
    fade = min(64, max(8, gap // 2), local_start, max(0, seg.size - local_end - 1))
    if fade >= 4:
        w = np.linspace(0.0, 1.0, fade).astype(np.float32)
        # only blend the repaired gap edges inside original x
        gs, ge = start, end + 1
        if gs - fade >= 0:
            out[gs - fade : gs] = x[gs - fade : gs] * (1 - w) + out[gs - fade : gs] * w
        if ge + fade <= n:
            out[ge : ge + fade] = out[ge : ge + fade] * (1 - w) + x[ge : ge + fade] * w
    # Write back in-place (works for band views and full channel buffers)
    x[:] = out[: x.shape[0]]


def multi_band_repair_channel(
    x: np.ndarray,
    sr: int,
    hits: list[ArtifactHit],
    *,
    cuts: tuple[float, ...] = (200.0, 2000.0, 8000.0),
    min_confidence: float = 0.45,
    freq_skew: float = 0.5,
) -> int:
    """
    Split → repair only the band(s) where the click lives → sum.
    Broadband hits repair all bands with spectral or cubic per length.
    Returns number of hit applications.
    """
    x64 = x.astype(np.float64)
    bands = lr4_split_bands(x64, sr, cuts)
    n_b = len(bands)
    used = 0
    for h in hits:
        if h.confidence < min_confidence:
            continue
        si, ei = int(h.start_s * sr), int(h.end_s * sr)
        si, ei = max(0, si), min(x.size - 1, ei)
        if ei <= si:
            continue
        if h.band is not None and 0 <= int(h.band) < n_b:
            targets = [int(h.band)]
            # Also lightly clean adjacent band for broadband bleed of the impulse
            if int(h.band) > 0 and (h.kind == "digital" or h.tf_mask):
                targets.append(int(h.band) - 1)
            if int(h.band) < n_b - 1 and (h.kind == "digital" or h.tf_mask):
                targets.append(int(h.band) + 1)
            targets = sorted(set(targets))
        else:
            targets = list(range(n_b))
        skew = 0.75 if h.kind == "mouth" else float(freq_skew)
        for bi in targets:
            # Higher bands prefer spectral/pixel; lows prefer cubic (preserve body)
            use_spectral = (
                h.method == "spectral"
                or h.tf_mask
                or (ei - si) > int(sr * 0.0012)
                or bi >= n_b - 2
            )
            if use_spectral:
                spectral_pixel_repair_region(
                    bands[bi], si, ei, sr, freq_skew=skew if bi >= n_b // 2 else skew * 0.5
                )
            elif h.method == "ar":
                _ar_repair(bands[bi], si, ei)
            else:
                _cubic_repair(bands[bi], si, ei)
        used += 1
    y = lr4_sum_bands(bands)
    # Match original length
    if y.size == x.size:
        x[:] = y.astype(np.float32)
    else:
        n = min(y.size, x.size)
        x[:n] = y[:n].astype(np.float32)
    return used


def spectral_pixel_repair_channel(
    x: np.ndarray,
    sr: int,
    hits: list[ArtifactHit],
    *,
    min_confidence: float = 0.45,
    freq_skew: float = 0.5,
) -> int:
    used = 0
    for h in hits:
        if h.confidence < min_confidence:
            continue
        si, ei = int(h.start_s * sr), int(h.end_s * sr)
        si, ei = max(0, si), min(x.size - 1, ei)
        if ei <= si:
            continue
        skew = 0.75 if h.kind == "mouth" else freq_skew
        spectral_pixel_repair_region(x, si, ei, sr, freq_skew=skew)
        used += 1
    return used


def repair_hit(x: np.ndarray, start: int, end: int, sr: int, method: str) -> None:
    method = (method or "cubic").lower()
    if method in ("spectral", "pixel", "tf"):
        spectral_pixel_repair_region(x, start, end, sr)
    elif method in ("multi_band", "multiband", "mb"):
        # Single-hit multi-band: treat as broadband spectral if long else cubic on fullband
        if end - start > int(sr * 0.001):
            spectral_pixel_repair_region(x, start, end, sr)
        else:
            _cubic_repair(x, start, end)
    elif method == "ar":
        _ar_repair(x, start, end)
    else:
        _cubic_repair(x, start, end)


def remove_artifacts_v2(
    audio: np.ndarray,
    sr: int,
    hunt: ArtifactHuntV2,
    *,
    declick: bool = True,
    dedc: bool = True,
    deedge: bool = True,
    declip: bool = True,
    min_confidence: float = 0.45,
    algorithm: str = "auto",  # auto | single | multi_band | spectral
    freq_skew: float = 0.5,
    sensitivity: float = 1.0,
) -> tuple[np.ndarray, list[str]]:
    """
    RX-class artifact removal.

    algorithm:
      auto       — multi_band for dense/digital; spectral for mouth/long; single otherwise
      single     — classic per-hit cubic/AR/spectral ladder
      multi_band — LR4 split, per-band repair, sum
      spectral   — full STFT pixel repair for all hits
    """
    x = np.ascontiguousarray(np.array(audio, dtype=np.float32, copy=True))
    applied: list[str] = []
    if x.ndim == 1:
        x = x.reshape(1, -1)
    if x.shape[0] > 8 and x.shape[0] > x.shape[1]:
        x = x.T
    ch, n = x.shape
    algorithm = (algorithm or "auto").lower()

    if declick and hunt.hits:
        hits = [h for h in hunt.hits if h.confidence >= min_confidence]
        # Auto pick algorithm
        algo = algorithm
        if algo == "auto":
            n_mouth = sum(1 for h in hits if h.kind == "mouth")
            n_dig = sum(1 for h in hits if h.kind == "digital")
            longish = sum(1 for h in hits if (h.end_s - h.start_s) > 0.0012)
            if n_mouth >= max(2, len(hits) // 3) or longish >= 2:
                algo = "spectral"
            elif n_dig >= 3 or len(hits) >= 6:
                algo = "multi_band"
            else:
                algo = "single"

        used = 0
        by_method: dict[str, int] = {}
        if algo in ("multi_band", "multiband", "mb"):
            for c in range(ch):
                used = max(
                    used,
                    multi_band_repair_channel(
                        x[c],
                        sr,
                        hits,
                        min_confidence=min_confidence,
                        freq_skew=freq_skew,
                    ),
                )
            by_method["multi_band"] = used
            algo = "multi_band"
        elif algo in ("spectral", "pixel", "tf"):
            for c in range(ch):
                used = max(
                    used,
                    spectral_pixel_repair_channel(
                        x[c], sr, hits, min_confidence=min_confidence, freq_skew=freq_skew
                    ),
                )
            by_method["spectral"] = used
            algo = "spectral"
        else:
            algo = "single"
            for h in hits:
                si, ei = int(h.start_s * sr), int(h.end_s * sr)
                si, ei = max(0, si), min(n - 1, ei)
                if ei <= si:
                    continue
                # Per-hit ladder: promote mouth/long → spectral; clip → AR; else cubic
                if h.kind == "clip_edge" or h.method == "ar":
                    meth = "ar"
                elif h.kind == "mouth" or h.tf_mask or h.method == "spectral" or (ei - si) > int(sr * 0.0015):
                    meth = "spectral"
                else:
                    meth = "cubic"
                for c in range(ch):
                    repair_hit(x[c], si, ei, sr, meth)
                used += 1
                by_method[meth] = by_method.get(meth, 0) + 1
        if used:
            detail = "+".join(f"{m}×{k}" for m, k in by_method.items())
            applied.append(f"declick-{algo} {used} ({detail})")

    if declip and hunt.clipped_runs:
        fixed = 0
        for s, e in hunt.clipped_runs:
            s, e = max(0, s), min(n - 1, e)
            if e <= s:
                continue
            for c in range(ch):
                # Pixel repair for clip runs when short; AR when long
                if e - s <= int(sr * 0.004):
                    spectral_pixel_repair_region(x[c], s, e, sr, freq_skew=0.3)
                else:
                    _ar_repair(x[c], s, e)
            fixed += 1
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
                for k in range(fade):
                    p = si - fade + k
                    if 0 <= p < n:
                        w = 0.5 * (1.0 + math.cos(math.pi * (k + 1) / fade))
                        x[c, p] *= w
                for k in range(fade):
                    p = ei + 1 + k
                    if 0 <= p < n:
                        w = 0.5 * (1.0 - math.cos(math.pi * (k + 1) / fade))
                        x[c, p] *= w
        applied.append(f"deedge {len(hunt.dropout_edges)}")

    if not applied:
        applied.append("anull")
    return x, applied


def _coerce_hits(hits: list[Any]) -> list[ArtifactHit]:
    out: list[ArtifactHit] = []
    for h in hits or []:
        if isinstance(h, ArtifactHit):
            out.append(h)
            continue
        if not isinstance(h, dict):
            continue
        try:
            out.append(
                ArtifactHit(
                    start_s=float(h["start_s"]),
                    end_s=float(h["end_s"]),
                    peak_db=float(h.get("peak_db", -12.0)),
                    confidence=float(h.get("confidence", 0.6)),
                    kind=str(h.get("kind") or "digital"),
                    method=str(h.get("method") or "cubic"),
                    band=h.get("band"),
                    tf_mask=bool(h.get("tf_mask")),
                    score=float(h.get("score") or 0.0),
                )
            )
        except Exception:
            continue
    return out


def bake_rx_live_patches(
    audio: np.ndarray,
    sr: int,
    hits: list[Any],
    *,
    algorithm: str = "auto",
    freq_skew: float = 0.5,
    min_confidence: float = 0.45,
    context_ms: float = 20.0,
    max_patches: int = 120,
) -> list[dict[str, Any]]:
    """
    Pre-bake RX-class repairs for live splicing.

    Each patch is a short multi-band / spectral repair of a hit neighborhood.
    Live audio path only copies samples — zero STFT / LR4 cost under the clock.
    Quality matches offline remove_artifacts_v2 for those regions.

    Returns list of dicts:
      {start:int, end:int, audio:float32 (ch, n), method:str, kind:str, confidence:float}
    where start/end are absolute inclusive sample indices.
    """
    x = np.ascontiguousarray(np.array(audio, dtype=np.float32, copy=True))
    if x.ndim == 1:
        x = x.reshape(1, -1)
    if x.shape[0] > 8 and x.shape[0] > x.shape[1]:
        x = x.T
    ch, n = x.shape
    hit_list = _coerce_hits(hits)
    hit_list = [h for h in hit_list if h.confidence >= float(min_confidence)]
    hit_list.sort(key=lambda h: h.start_s)
    if not hit_list:
        return []

    algo_global = (algorithm or "auto").lower()
    ctx = max(int(sr * context_ms / 1000.0), int(sr * 0.008), 256)
    patches: list[dict[str, Any]] = []
    covered_until = -1

    for h in hit_list:
        if len(patches) >= max_patches:
            break
        si = int(h.start_s * sr)
        ei = int(h.end_s * sr)
        si, ei = max(0, si), min(n - 1, max(si, ei))
        if ei <= si:
            continue
        # Skip if already covered by previous patch (merged neighborhoods)
        if si <= covered_until:
            continue
        a0 = max(0, si - ctx)
        b0 = min(n, ei + 1 + ctx)
        # Grow window to absorb nearby hits (cluster bake — fewer seams)
        cluster = [h]
        for h2 in hit_list:
            if h2 is h:
                continue
            s2, e2 = int(h2.start_s * sr), int(h2.end_s * sr)
            if s2 > b0 + int(sr * 0.002):
                break
            if e2 + ctx >= a0 and s2 - ctx <= b0:
                a0 = min(a0, max(0, s2 - ctx))
                b0 = max(b0, min(n, e2 + 1 + ctx))
                cluster.append(h2)
        region = np.ascontiguousarray(x[:, a0:b0].copy())
        rel_hits: list[ArtifactHit] = []
        for hc in cluster:
            rs = max(0.0, (int(hc.start_s * sr) - a0) / float(sr))
            re = max(rs, (int(hc.end_s * sr) - a0) / float(sr))
            rel_hits.append(
                ArtifactHit(
                    start_s=rs,
                    end_s=re,
                    peak_db=hc.peak_db,
                    confidence=hc.confidence,
                    kind=hc.kind,
                    method=hc.method,
                    band=hc.band,
                    tf_mask=hc.tf_mask,
                    score=hc.score,
                )
            )
        # Per-cluster algorithm preference
        algo = algo_global
        if algo == "auto":
            n_mouth = sum(1 for z in rel_hits if z.kind == "mouth")
            n_dig = sum(1 for z in rel_hits if z.kind == "digital")
            longish = sum(1 for z in rel_hits if (z.end_s - z.start_s) > 0.0012)
            if n_mouth >= max(1, len(rel_hits) // 2) or longish >= 1:
                algo = "spectral"
            elif n_dig >= 2 or len(rel_hits) >= 3:
                algo = "multi_band"
            else:
                # Use hit methods if present
                if any(z.method == "spectral" or z.tf_mask for z in rel_hits):
                    algo = "spectral"
                elif any(z.method == "multi_band" for z in rel_hits):
                    algo = "multi_band"
                else:
                    algo = "single"
        mini = ArtifactHuntV2(hits=rel_hits, duration_s=region.shape[1] / float(sr))
        try:
            wet, applied = remove_artifacts_v2(
                region,
                sr,
                mini,
                declick=True,
                dedc=False,
                deedge=False,
                declip=False,
                min_confidence=0.0,  # already filtered
                algorithm=algo,
                freq_skew=freq_skew,
            )
        except Exception:
            continue
        if wet.ndim == 1:
            wet = wet.reshape(1, -1)
        if wet.shape[0] != ch:
            # channel mismatch: mono→N or trim
            if wet.shape[0] == 1 and ch > 1:
                wet = np.repeat(wet, ch, axis=0)
            else:
                wet = wet[:ch]
        # Edge safety: micro-crossfade patch into original region ends
        pn = min(wet.shape[1], region.shape[1])
        wet = wet[:, :pn].astype(np.float32, copy=True)
        fade = min(48, max(8, pn // 16))
        if fade >= 4 and pn > fade * 2:
            w = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            wet[:, :fade] = region[:, :fade] * (1.0 - w) + wet[:, :fade] * w
            wet[:, -fade:] = wet[:, -fade:] * (1.0 - w) + region[:, -fade:] * w
        end_i = a0 + pn - 1
        patches.append(
            {
                "start": int(a0),
                "end": int(end_i),
                "audio": wet,
                "method": algo,
                "kind": cluster[0].kind,
                "confidence": float(max(z.confidence for z in cluster)),
                "n_hits": len(cluster),
                "applied": list(applied),
            }
        )
        covered_until = end_i
    return patches


def suggest_artifact_settings_v2(hunt: ArtifactHuntV2) -> dict[str, Any]:
    if hunt is None or hunt.error:
        return {
            "declick": False,
            "dedc": False,
            "deedge": False,
            "any": False,
            "note": "scan failed — left dry",
            "severity": 0.0,
            "min_confidence": 0.45,
            "hits": [],
        }
    strong = [h for h in hunt.hits if h.confidence >= 0.55]
    weak = [h for h in hunt.hits if 0.45 <= h.confidence < 0.55]
    declick = len(strong) > 0 or len(weak) >= 3
    deedge = len(hunt.dropout_edges or []) > 0
    dedc = abs(float(hunt.dc_offset or 0.0)) > 0.004
    min_conf = 0.50 if len(strong) >= 5 else 0.45
    severity = 0.0
    if hunt.hits:
        severity = float(min(1.0, np.mean([h.confidence for h in hunt.hits]) * (1 + 0.02 * len(hunt.hits))))
    if dedc:
        severity = max(severity, min(1.0, abs(hunt.dc_offset) * 25.0))
    parts = []
    # Prefer algorithm for bake/live
    n_mouth = sum(1 for h in hunt.hits if h.kind == "mouth")
    n_dig = sum(1 for h in hunt.hits if h.kind == "digital")
    longish = sum(1 for h in hunt.hits if (h.end_s - h.start_s) > 0.0012)
    if n_mouth >= max(2, len(hunt.hits) // 3) or longish >= 2:
        algorithm = "spectral"
    elif n_dig >= 3 or len(hunt.hits) >= 6:
        algorithm = "multi_band"
    else:
        algorithm = "single"

    if declick:
        parts.append(f"de-click×{len(hunt.hits)}")
        kinds = {}
        for h in hunt.hits:
            kinds[h.kind] = kinds.get(h.kind, 0) + 1
        parts.append("{" + ",".join(f"{k}:{v}" for k, v in kinds.items()) + "}")
        parts.append(f"algo={algorithm}")
    if deedge:
        parts.append(f"edges×{len(hunt.dropout_edges)}")
    if dedc:
        parts.append(f"DC {hunt.dc_offset:+.4f}")
    note = "auto · " + (" · ".join(parts) if parts else "clean · repairs off")
    return {
        "declick": declick,
        "dedc": dedc,
        "deedge": deedge,
        "any": declick or dedc or deedge,
        "note": note,
        "severity": severity,
        "min_confidence": min_conf,
        "algorithm": algorithm,
        "freq_skew": 0.7 if n_mouth > n_dig else 0.45,
        "sensitivity": 1.0,
        "n_clicks": len(hunt.hits),
        "n_drops": len(hunt.dropout_edges or []),
        "dc_offset": float(hunt.dc_offset or 0.0),
        "hits": [
            {
                "start_s": h.start_s,
                "end_s": h.end_s,
                "kind": h.kind,
                "confidence": h.confidence,
                "method": h.method,
                "peak_db": h.peak_db,
                "band": h.band,
            }
            for h in hunt.hits[:80]
        ],
        "metrics": dict(hunt.metrics or {}),
    }


# ---------------------------------------------------------------------------
# BleedFix intelligence
# ---------------------------------------------------------------------------


def _smooth_db(x_db: np.ndarray, attack_ms: float, release_ms: float, frame_rate: float) -> np.ndarray:
    a_up = 1.0 - math.exp(-1.0 / (max(attack_ms, 0.1) / 1000.0 * frame_rate))
    a_dn = 1.0 - math.exp(-1.0 / (max(release_ms, 1.0) / 1000.0 * frame_rate))
    out = np.empty_like(x_db)
    if x_db.size == 0:
        return out
    out[0] = x_db[0]
    for i in range(1, x_db.size):
        p, v = out[i - 1], x_db[i]
        out[i] = p + (a_up * (v - p) if v > p else a_dn * (v - p))
    return out


def _smooth_gain_db(g_db: np.ndarray, attack_ms: float, release_ms: float, frame_rate: float) -> np.ndarray:
    a_dn = 1.0 - math.exp(-1.0 / (max(attack_ms, 0.1) / 1000.0 * frame_rate))
    a_up = 1.0 - math.exp(-1.0 / (max(release_ms, 1.0) / 1000.0 * frame_rate))
    out = np.empty_like(g_db)
    if g_db.size == 0:
        return out
    out[0] = g_db[0]
    for i in range(1, g_db.size):
        p, v = out[i - 1], g_db[i]
        out[i] = p + (a_dn * (v - p) if v < p else a_up * (v - p))
    return out


def _expander_gain_db(env_db: np.ndarray, thr_db: float, ratio: float, hyst_db: float = 2.0) -> np.ndarray:
    """Downward expander with hysteresis (open thr = thr, close thr = thr+hyst)."""
    g = np.zeros_like(env_db)
    open_thr = thr_db
    close_thr = thr_db + max(0.0, float(hyst_db))
    gated = False
    r = max(float(ratio), 1.01)
    for i, e in enumerate(env_db):
        if gated:
            if e > close_thr:
                gated = False
        else:
            if e < open_thr:
                gated = True
        if gated and e < open_thr:
            g[i] = (e - open_thr) * (1.0 - 1.0 / r)
        elif gated:
            # hold region between open and close
            g[i] = min(0.0, (e - open_thr) * (1.0 - 1.0 / r) * 0.35)
        else:
            g[i] = 0.0
    return g


def _lookahead_env(env_db: np.ndarray, frames: int) -> np.ndarray:
    if frames <= 0 or env_db.size == 0:
        return env_db
    # causal-looking min of current and future (for gain decisions)
    out = env_db.copy()
    for i in range(env_db.size):
        j = min(env_db.size, i + frames + 1)
        out[i] = float(np.min(env_db[i:j]))
    return out


def suggest_bleedfix_v2(
    path: Path | str | None = None,
    *,
    audio: np.ndarray | None = None,
    sr: int | None = None,
    sidechain: np.ndarray | None = None,
    profile: str = "balanced",  # safe | balanced | aggressive
) -> dict[str, Any]:
    """
    Program-safe BleedFix auto-dial with hysteresis + look-ahead.

    profile:
      safe       — only arm with large gap; gentler duck
      balanced   — default
      aggressive — smaller gap allowed; deeper duck
    """
    profile = (profile or "balanced").lower()
    gap_need = {"safe": 14.0, "balanced": 10.0, "aggressive": 7.0}.get(profile, 10.0)
    target_gate = {"safe": 0.12, "balanced": 0.18, "aggressive": 0.26}.get(profile, 0.18)
    prog_protect = {"safe": 5.0, "balanced": 4.0, "aggressive": 3.0}.get(profile, 4.0)

    out: dict[str, Any] = {
        "on": False,
        "mode": "auto",
        "profile": profile,
        "threshold_db": -46.0,
        "ratio": 8.0,
        "attack_ms": 8.0,
        "release_ms": 160.0,
        "margin_db": 8.0,
        "hysteresis_db": 2.5,
        "lookahead_ms": 5.0,
        "bands": 1,
        "wet": 1.0,
        "spectral": False,
        "note": "bleedfix auto · idle",
        "floor_db": None,
        "program_db": None,
        "gap_db": None,
        "gated_fraction": 0.0,
        "est_bleed_reduction_db": 0.0,
        "report": {},
    }
    try:
        if audio is None:
            if not path:
                out["note"] = "bleedfix auto · no source"
                return out
            data, file_sr = sf.read(str(path), always_2d=True, dtype="float32")
            audio = np.asarray(data.T, dtype=np.float32)
            sr = int(file_sr)
        else:
            audio = np.asarray(audio, dtype=np.float32)
            sr = int(sr or 44100)

        mono = _mono(audio)
        key = _mono(sidechain) if sidechain is not None else mono
        if mono.size < int(sr * 0.4):
            out["note"] = "bleedfix auto · file too short"
            return out

        env = _rms_frames_db(key, sr)
        if env.size < 8:
            out["note"] = "bleedfix auto · no frames"
            return out

        floor = float(np.percentile(env, 18.0))
        mid = float(np.median(env))
        loud = env[env >= mid]
        program = float(np.percentile(loud if loud.size else env, 70.0))
        gap = program - floor
        out["floor_db"] = floor
        out["program_db"] = program
        out["gap_db"] = gap

        if gap < gap_need:
            out["note"] = f"bleedfix {profile} · gap {gap:.1f} dB < {gap_need:.0f} · left off"
            out["report"] = {"reason": "gap_too_small", "gap_db": gap, "need_db": gap_need}
            return out

        peakish = float(np.percentile(env, 95.0)) - mid
        if peakish > 12.0:
            attack_ms, release_ms, la_ms = 3.0, 110.0, 3.0
        elif peakish > 7.0:
            attack_ms, release_ms, la_ms = 7.0, 150.0, 5.0
        else:
            attack_ms, release_ms, la_ms = 12.0, 210.0, 8.0

        # Spectral mode candidate: washy beds with high HF floor
        spectral = False
        try:
            # simple high-band energy share in quiet frames
            hp = np.diff(mono, prepend=mono[0])
            quiet_mask = env < (floor + 3.0)
            # map frames roughly
            if quiet_mask.mean() > 0.05:
                spectral = peakish < 8.0 and gap > gap_need + 2.0
        except Exception:
            spectral = False

        use_3band = False
        try:
            from .studio_fx import _band_split, _noise_floor_db

            bands = _band_split(mono.astype(np.float32), sr, 3)
            floors_b = [_noise_floor_db(b, sr) for b in bands]
            if max(floors_b) - min(floors_b) > 6.0:
                use_3band = True
        except Exception:
            pass

        frame_rate = max(1.0, len(env) / max(1e-3, mono.size / sr))
        la_frames = max(0, int(round(la_ms / 1000.0 * frame_rate)))
        env_la = _lookahead_env(env, la_frames)

        best = None
        best_score = -1e9
        hyst = 2.0 if profile == "aggressive" else 2.5 if profile == "balanced" else 3.5
        for margin in (3.0, 5.0, 7.0, 9.0, 11.0, 13.0):
            thr = floor + margin
            if thr > program - prog_protect:
                continue
            for ratio in (4.0, 6.0, 8.0, 10.0, 14.0):
                env_s = _smooth_db(env_la, attack_ms, release_ms, frame_rate)
                g = _expander_gain_db(env_s, thr, ratio, hyst_db=hyst)
                g = _smooth_gain_db(g, attack_ms, release_ms, frame_rate)
                if g.size == 0:
                    continue
                gated = float(np.count_nonzero(g < -1.0)) / float(g.size)
                quiet = env_s < (floor + 3.0)
                deep = float(np.mean(g[quiet])) if np.any(quiet) else 0.0
                prog_mask = env_s > (program - 3.0)
                prog_hit = float(np.mean(g[prog_mask])) if np.any(prog_mask) else 0.0
                score = (
                    -abs(gated - target_gate) * 45.0
                    + min(0.0, deep) * 0.7
                    - max(0.0, -prog_hit - 1.2) * 10.0
                )
                if score > best_score:
                    best_score = score
                    best = {
                        "threshold_db": thr,
                        "ratio": ratio,
                        "margin_db": margin,
                        "gated_fraction": gated,
                        "deep_quiet_db": deep,
                        "prog_hit_db": prog_hit,
                        "est_bleed_reduction_db": float(-deep) if deep < 0 else 0.0,
                    }

        if best is None:
            thr = min(floor + 8.0, program - prog_protect - 1.0)
            best = {
                "threshold_db": thr,
                "ratio": 8.0,
                "margin_db": thr - floor,
                "gated_fraction": 0.0,
                "est_bleed_reduction_db": 0.0,
            }

        mode = "sidechain" if sidechain is not None else ("spectral" if spectral else "auto")
        out.update(
            {
                "on": True,
                "mode": mode,
                "profile": profile,
                "threshold_db": float(best["threshold_db"]),
                "ratio": float(best["ratio"]),
                "attack_ms": float(attack_ms),
                "release_ms": float(release_ms),
                "margin_db": float(best["margin_db"]),
                "hysteresis_db": float(hyst),
                "lookahead_ms": float(la_ms),
                "bands": 3 if use_3band else 1,
                "wet": 1.0,
                "spectral": bool(spectral),
                "gated_fraction": float(best.get("gated_fraction") or 0.0),
                "est_bleed_reduction_db": float(best.get("est_bleed_reduction_db") or 0.0),
                "note": (
                    f"bleedfix {profile} · floor {floor:.1f} · thr {best['threshold_db']:.1f} · "
                    f"r{best['ratio']:.0f}:1 · hyst {hyst:.1f} · la {la_ms:.0f}ms · "
                    f"{'3-band · ' if use_3band else ''}"
                    f"{'spectral · ' if spectral else ''}"
                    f"gap {gap:.1f} dB · est −{best.get('est_bleed_reduction_db', 0):.1f} dB quiet"
                ),
                "report": {
                    "floor_db": floor,
                    "program_db": program,
                    "gap_db": gap,
                    "gated_fraction": best.get("gated_fraction"),
                    "prog_hit_db": best.get("prog_hit_db"),
                    "deep_quiet_db": best.get("deep_quiet_db"),
                    "profile": profile,
                    "mode": mode,
                },
            }
        )
        return out
    except Exception as exc:
        out["note"] = f"bleedfix auto failed: {exc}"
        return out


def apply_spectral_bleed_duck(
    audio: np.ndarray,
    sr: int,
    *,
    reduction_db: float = 10.0,
    floor_percentile: float = 18.0,
    wet: float = 1.0,
) -> tuple[np.ndarray, str]:
    """
    Music-aware quiet-frame spectral duck:
    learn magnitude profile from quiet frames; attenuate that profile
    when program is near the floor (not a hard gate).
    """
    x = np.asarray(audio, dtype=np.float32)
    mono_in = x.ndim == 1
    if mono_in:
        x = x.reshape(1, -1)
    if x.shape[0] > 8 and x.shape[0] > x.shape[1]:
        x = x.T
    mono = _mono(x)
    n = mono.size
    n_fft = 512
    hop = 128
    if n < n_fft * 4:
        return audio, "spectral duck skipped (short)"

    # Quiet frame mask from RMS
    env = _rms_frames_db(mono, sr)
    floor = float(np.percentile(env, floor_percentile))
    # STFT-like frames
    frames = []
    positions = list(range(0, n - n_fft + 1, hop))
    for s in positions:
        frames.append(mono[s : s + n_fft] * np.hanning(n_fft))
    F = np.fft.rfft(np.stack(frames), axis=1)
    mag = np.abs(F) + 1e-12
    # Map env frames to STFT frames roughly
    quiet = []
    for i, s in enumerate(positions):
        t = (s + n_fft / 2) / sr
        fi = int(t / max(1e-6, n / sr) * max(1, env.size - 1))
        fi = min(env.size - 1, max(0, fi))
        quiet.append(env[fi] < floor + 4.0)
    quiet = np.asarray(quiet, dtype=bool)
    if quiet.sum() < 4:
        return audio, "spectral duck skipped (no quiet frames)"
    profile = np.median(mag[quiet], axis=0)
    profile = profile / (np.max(profile) + 1e-12)

    # Per-frame attenuation when quiet
    red = 10.0 ** (-abs(reduction_db) / 20.0)
    out_mono = np.zeros(n, dtype=np.float64)
    norm = np.zeros(n, dtype=np.float64)
    win = np.hanning(n_fft)
    for i, s in enumerate(positions):
        if quiet[i]:
            # Attenuate bins proportional to bleed profile
            scale = 1.0 - (1.0 - red) * profile
            Yi = F[i] * scale
        else:
            Yi = F[i]
        y = np.fft.irfft(Yi, n=n_fft).real * win
        out_mono[s : s + n_fft] += y
        norm[s : s + n_fft] += win
    norm = np.maximum(norm, 1e-6)
    ducked = (out_mono / norm).astype(np.float32)

    # Apply same gain envelope to all channels (ratio duck)
    g = ducked / (mono.astype(np.float32) + 1e-8)
    g = np.clip(g, 0.05, 1.5)
    # Smooth gain
    try:
        from scipy.ndimage import uniform_filter1d

        g = uniform_filter1d(g, size=max(32, int(sr * 0.002)))
    except Exception:
        pass
    y = x * g[np.newaxis, :]
    wet = max(0.0, min(1.0, float(wet)))
    if wet < 1.0:
        y = x * (1.0 - wet) + y * wet
    if mono_in:
        y = y[0]
    return y.astype(np.float32), f"spectral duck −{abs(reduction_db):.0f} dB quiet"


def build_repair_plan(
    path: Path | str,
    *,
    bleed_profile: str = "balanced",
    sidechain_path: Path | str | None = None,
) -> RepairPlan:
    """One-shot plan: hunt artifacts + suggest bleed (optional key file)."""
    path = Path(path)
    hunt = hunt_file_v2(path)
    art = suggest_artifact_settings_v2(hunt)
    sc = None
    if sidechain_path and Path(sidechain_path).exists():
        try:
            d, sr = sf.read(str(sidechain_path), always_2d=True, dtype="float32")
            sc = d.T
        except Exception:
            sc = None
    bleed = suggest_bleedfix_v2(path, sidechain=sc, profile=bleed_profile)
    note_parts = [art.get("note", ""), bleed.get("note", "")]
    return RepairPlan(
        path=str(path),
        artifacts=art,
        bleed=bleed,
        note=" | ".join(p for p in note_parts if p),
    )
