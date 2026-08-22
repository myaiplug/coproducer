"""
High-quality offline repair using Spotify Pedalboard.

Production path:
1. Decode with soundfile / pedalboard.io
2. Optional high-pass (Butterworth via Pedalboard)
3. Gain toward target LUFS (pyloudnorm measure + gain)
4. Pedalboard Limiter for true-peak style ceiling
5. Write 24-bit WAV

FFmpeg remains available as fallback (see features.repairs).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

try:
    import pedalboard as pb
    from pedalboard.io import AudioFile

    HAS_PEDALBOARD = True
except Exception:  # pragma: no cover
    HAS_PEDALBOARD = False
    pb = None  # type: ignore
    AudioFile = None  # type: ignore

try:
    import pyloudnorm as pyln

    HAS_PYLOUDNORM = True
except Exception:  # pragma: no cover
    HAS_PYLOUDNORM = False
    pyln = None  # type: ignore


@dataclass
class PedalboardRepairResult:
    ok: bool
    output_path: Path | None
    filters_applied: list[str]
    input_lufs: float | None
    output_lufs: float | None
    input_tp_db: float | None
    output_tp_db: float | None
    engine: str
    error: str | None = None
    sample_rate: int | None = None
    channels: int | None = None


def pedalboard_available() -> bool:
    return bool(HAS_PEDALBOARD and HAS_PYLOUDNORM)


def _to_db(peak: float) -> float:
    return 20.0 * math.log10(max(float(peak), 1e-12))


def _as_n_ch(audio: np.ndarray) -> np.ndarray:
    """Normalize to shape (n_samples, n_channels) for pyloudnorm."""
    a = np.asarray(audio, dtype=np.float64)
    if a.ndim == 1:
        return a.reshape(-1, 1)
    # (ch, n) if channels first (pedalboard convention)
    if a.shape[0] <= 8 and a.shape[0] < a.shape[1]:
        return a.T
    return a


def _measure_lufs(audio: np.ndarray, sr: int) -> float | None:
    if not HAS_PYLOUDNORM or audio.size == 0:
        return None
    try:
        meter = pyln.Meter(sr)
        y = _as_n_ch(audio)
        # Mono downmix for stable integrated loudness on multi-channel
        if y.shape[1] > 1:
            mono = np.mean(y, axis=1)
        else:
            mono = y[:, 0]
        val = meter.integrated_loudness(mono)
        if val is None or not math.isfinite(val):
            return None
        return round(float(val), 2)
    except Exception:
        return None


def _peak_linear(audio: np.ndarray) -> float:
    if audio.size == 0:
        return 0.0
    return float(np.max(np.abs(audio)))


def _load_audio(path: Path) -> tuple[np.ndarray, int]:
    """Return float32 array shape (channels, samples), sample_rate."""
    if HAS_PEDALBOARD and AudioFile is not None:
        with AudioFile(str(path)) as f:
            audio = f.read(f.frames)  # (ch, n)
            sr = int(f.samplerate)
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio.reshape(1, -1)
        return audio, sr
    data, sr = sf.read(str(path), always_2d=True, dtype="float32")
    # soundfile: (n, ch) → (ch, n)
    return data.T, int(sr)


def _write_wav(path: Path, audio: np.ndarray, sr: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # audio (ch, n) → (n, ch)
    if audio.ndim == 1:
        out = audio
    else:
        out = audio.T
    sf.write(str(path), out, sr, subtype="PCM_24")


def apply_pedalboard_repair(
    input_path: Path | str,
    output_path: Path | str,
    *,
    target_lufs: float = -14.0,
    tp_ceiling_db: float = -1.0,
    highpass_hz: float | None = None,
    do_loudness: bool = True,
    do_limit: bool = True,
) -> PedalboardRepairResult:
    """
    Render a repaired WAV with Pedalboard.

    Never overwrites input. Always writes to output_path.
    """
    src = Path(input_path).expanduser().resolve()
    dst = Path(output_path).expanduser().resolve()
    applied: list[str] = []

    if not pedalboard_available():
        return PedalboardRepairResult(
            ok=False,
            output_path=None,
            filters_applied=[],
            input_lufs=None,
            output_lufs=None,
            input_tp_db=None,
            output_tp_db=None,
            engine="none",
            error="pedalboard or pyloudnorm not installed",
        )
    if not src.is_file():
        return PedalboardRepairResult(
            ok=False,
            output_path=None,
            filters_applied=[],
            input_lufs=None,
            output_lufs=None,
            input_tp_db=None,
            output_tp_db=None,
            engine="pedalboard",
            error=f"input missing: {src}",
        )
    if src.resolve() == dst.resolve():
        return PedalboardRepairResult(
            ok=False,
            output_path=None,
            filters_applied=[],
            input_lufs=None,
            output_lufs=None,
            input_tp_db=None,
            output_tp_db=None,
            engine="pedalboard",
            error="refusing to overwrite original file",
        )

    try:
        audio, sr = _load_audio(src)
        in_lufs = _measure_lufs(audio, sr)
        in_tp = _to_db(_peak_linear(audio))

        # Process chain step-by-step so LUFS gain is measured accurately
        rendered = np.asarray(audio, dtype=np.float32)

        # High-pass first when requested
        if highpass_hz and highpass_hz > 0:
            rendered = np.asarray(
                pb.Pedalboard(
                    [pb.HighpassFilter(cutoff_frequency_hz=float(highpass_hz))]
                )(rendered, sr),
                dtype=np.float32,
            )
            applied.append(f"highpass@{highpass_hz:.0f}Hz")
            in_lufs = _measure_lufs(rendered, sr) or in_lufs

        # Loudness: measure → apply gain (transparent) before limiting
        if do_loudness and in_lufs is not None and math.isfinite(in_lufs):
            gain_db = float(target_lufs) - float(in_lufs)
            gain_db = max(-18.0, min(18.0, gain_db))
            if abs(gain_db) >= 0.15:
                rendered = np.asarray(
                    pb.Pedalboard([pb.Gain(gain_db=gain_db)])(rendered, sr),
                    dtype=np.float32,
                )
                applied.append(f"gain={gain_db:+.2f}dB→{target_lufs:.1f}LUFS")

        if do_limit:
            thr = float(tp_ceiling_db) - 0.05
            rendered = np.asarray(
                pb.Pedalboard([pb.Limiter(threshold_db=thr, release_ms=80.0)])(rendered, sr),
                dtype=np.float32,
            )
            applied.append(f"limiter@{thr:.2f}dB")
        elif not applied:
            applied.append("anull")

        rendered = np.asarray(rendered, dtype=np.float32)
        # Soft ceiling only if still over (avoid crushing already-limited audio)
        peak = _peak_linear(rendered)
        limit_lin = 10 ** (float(tp_ceiling_db) / 20.0)
        if peak > limit_lin * 1.02:
            rendered = rendered * (limit_lin / peak)
            applied.append("safety_scale")

        _write_wav(dst, rendered, sr)
        out_lufs = _measure_lufs(rendered, sr)
        out_tp = _to_db(_peak_linear(rendered))

        return PedalboardRepairResult(
            ok=True,
            output_path=dst,
            filters_applied=applied,
            input_lufs=in_lufs,
            output_lufs=out_lufs,
            input_tp_db=round(in_tp, 2),
            output_tp_db=round(out_tp, 2),
            engine="pedalboard",
            sample_rate=sr,
            channels=int(rendered.shape[0]) if rendered.ndim > 1 else 1,
        )
    except Exception as exc:
        return PedalboardRepairResult(
            ok=False,
            output_path=None,
            filters_applied=applied,
            input_lufs=None,
            output_lufs=None,
            input_tp_db=None,
            output_tp_db=None,
            engine="pedalboard",
            error=str(exc),
        )


def plan_to_pedalboard_kwargs(plan: Any) -> dict[str, Any]:
    """Map RepairPlan actions → apply_pedalboard_repair kwargs."""
    ids = set()
    try:
        ids = {a.id for a in (plan.actions or [])}
    except Exception:
        ids = set()
    hp = 25.0 if "highpass" in ids else None
    do_loud = "loudnorm" in ids or "true_peak_limit" in ids
    # Always limit if any level action, or true_peak alone
    do_limit = bool(ids)
    target = float(getattr(plan, "target_lufs", -14.0) or -14.0)
    ceiling = float(getattr(plan, "tp_ceiling", -1.0) or -1.0)
    return {
        "target_lufs": target,
        "tp_ceiling_db": ceiling,
        "highpass_hz": hp,
        "do_loudness": do_loud or "loudnorm" in ids,
        "do_limit": do_limit,
    }
