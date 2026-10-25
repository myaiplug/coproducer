"""
ITU-R BS.1770-4 / EBU R128 loudness and true-peak measurement.

Everything here runs with **all channels intact** - no mono downmix and no
analysis-rate resample before the peak stage (both used to hide or invent
intersample peaks).

* Integrated loudness / LRA: FFmpeg ``ebur128=peak=true+sample`` (EBU R128 /
  Tech 3342 reference implementation, channel-weighted), falling back to
  pyloudnorm on the native multichannel array.
* True peak (dBTP): BS.1770-4 Annex 2 method - each channel oversampled 4x
  with a polyphase low-pass interpolator at the file's native rate, absolute
  max per channel, overall = max over channels. Measured natively because
  FFmpeg only prints its true peak at 0.1 dB resolution (and its per-channel
  frame metadata only to 3 linear decimals). FFmpeg's overall true peak is
  kept as an independent cross-check, and used if the native meter fails.
* Sample peak (dBFS): the file's own samples at native rate, per channel,
  always reported separately so it is never confused with true peak.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

TRUE_PEAK_OVERSAMPLE = 4
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?|-?inf|nan"
SILENCE_DB = -120.0


@dataclass(slots=True)
class R128Measurement:
    integrated_lufs: float | None = None
    threshold_lufs: float | None = None
    loudness_range_lu: float | None = None
    true_peak_dbtp: float | None = None
    true_peak_dbtp_per_channel: list[float] = field(default_factory=list)
    sample_peak_dbfs: float | None = None
    sample_peak_dbfs_per_channel: list[float] = field(default_factory=list)
    ffmpeg_true_peak_dbtp: float | None = None  # independent cross-check (0.1 dB resolution)
    method: str = "none"


def lin_to_db(value: float) -> float:
    if value is None or not math.isfinite(value) or value <= 0.0:
        return SILENCE_DB
    return max(SILENCE_DB, 20.0 * math.log10(value))


def _as_2d(data: np.ndarray) -> np.ndarray:
    arr = np.asarray(data)
    if not np.issubdtype(arr.dtype, np.floating):
        arr = arr.astype(np.float64)
    if arr.ndim == 1:
        arr = arr[:, None]
    return arr


# ---------------------------------------------------------------------------
# Native (numpy / scipy) meters
# ---------------------------------------------------------------------------


def sample_peak_per_channel_dbfs(data: np.ndarray) -> list[float]:
    """Per-channel sample peak in dBFS on the samples as stored (no resampling)."""
    arr = _as_2d(data)
    if arr.size == 0:
        return []
    return [round(lin_to_db(float(np.max(np.abs(arr[:, c])))), 2) for c in range(arr.shape[1])]


def _true_peak_linear_1d(x: np.ndarray, oversample: int) -> float:
    from scipy.signal import resample_poly

    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return 0.0
    sample_pk = float(np.max(np.abs(x)))
    if oversample <= 1 or x.size < 4:
        return sample_pk
    # Zero padding at both ends lets the interpolator see the edges; blocks
    # overlap by more than the filter length so no intersample peak is missed.
    block = 1 << 19
    overlap = 256
    mx = sample_pk
    start = 0
    n = x.size
    while start < n:
        lo = max(0, start - overlap)
        hi = min(n, start + block + overlap)
        chunk = x[lo:hi]
        y = resample_poly(chunk, oversample, 1)
        mx = max(mx, float(np.max(np.abs(y))))
        start += block
    return mx


def true_peak_per_channel_dbtp(
    data: np.ndarray, oversample: int = TRUE_PEAK_OVERSAMPLE
) -> list[float]:
    """
    BS.1770-4 style true peak per channel: 4x polyphase oversampling of each
    channel at the native sample rate, then absolute max. Never downmixes.
    """
    arr = _as_2d(data)
    if arr.size == 0:
        return []
    out = []
    for c in range(arr.shape[1]):
        try:
            lin = _true_peak_linear_1d(arr[:, c], oversample)
        except Exception:
            lin = float(np.max(np.abs(arr[:, c])))
        out.append(round(lin_to_db(lin), 2))
    return out


def measure_native(data: np.ndarray, sr: int, loudness: bool = True) -> R128Measurement:
    """Fallback: pyloudnorm (multichannel BS.1770) + native true/sample peak."""
    arr = _as_2d(data)
    m = R128Measurement(method=f"native-bs1770-polyphase-{TRUE_PEAK_OVERSAMPLE}x")
    if arr.size == 0:
        return m
    try:
        import scipy.signal  # noqa: F401
    except Exception:
        m.method = "sample-peak-only (scipy unavailable)"
    m.true_peak_dbtp_per_channel = true_peak_per_channel_dbtp(arr)
    m.true_peak_dbtp = max(m.true_peak_dbtp_per_channel)
    m.sample_peak_dbfs_per_channel = sample_peak_per_channel_dbfs(arr)
    m.sample_peak_dbfs = max(m.sample_peak_dbfs_per_channel)
    if loudness and arr.shape[0] >= int(sr * 0.4):
        try:
            import pyloudnorm as pyln

            meter = pyln.Meter(sr)
            # pyloudnorm applies per-channel K-weighting and sums channel
            # powers (G=1.0 for L/R) - pass the native (n, ch) array.
            src = arr if arr.shape[1] > 1 else arr[:, 0]
            integ = meter.integrated_loudness(src)
            if integ is not None and math.isfinite(integ) and integ > -70.0:
                m.integrated_lufs = round(float(integ), 2)
            try:
                lra = meter.loudness_range(src)
                if lra is not None and math.isfinite(lra):
                    m.loudness_range_lu = round(float(lra), 2)
            except Exception:
                pass
        except Exception:
            pass
    return m


# ---------------------------------------------------------------------------
# FFmpeg ebur128 path
# ---------------------------------------------------------------------------


def _parse_float(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        v = float(text)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_ebur128_output(stderr: str) -> R128Measurement | None:
    """Parse the ``Summary:`` block that FFmpeg's ``ebur128`` filter logs."""
    idx = stderr.rfind("Summary:")
    if idx < 0:
        return None
    summary = stderr[idx:]
    m = R128Measurement(method="ffmpeg-ebur128")

    integ_sec = re.search(
        rf"Integrated loudness:\s*I:\s*({_NUMBER})\s*LUFS\s*Threshold:\s*({_NUMBER})", summary
    )
    if integ_sec:
        integ = _parse_float(integ_sec.group(1))
        # ffmpeg reports -70.0 LUFS for silence / too short to gate
        m.integrated_lufs = round(integ, 2) if integ is not None and integ > -70.0 else None
        m.threshold_lufs = _parse_float(integ_sec.group(2))
    lra = re.search(rf"Loudness range:\s*LRA:\s*({_NUMBER})\s*LU", summary)
    if lra:
        m.loudness_range_lu = _parse_float(lra.group(1))
    tp = re.search(rf"True peak:\s*Peak:\s*({_NUMBER})\s*dBFS", summary)
    if tp:
        v = _parse_float(tp.group(1))
        m.true_peak_dbtp = round(v, 2) if v is not None else SILENCE_DB
    sp = re.search(rf"Sample peak:\s*Peak:\s*({_NUMBER})\s*dBFS", summary)
    if sp:
        v = _parse_float(sp.group(1))
        m.sample_peak_dbfs = round(v, 2) if v is not None else SILENCE_DB
    if m.true_peak_dbtp is None and m.integrated_lufs is None:
        return None
    return m


def ebur128_command(path: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    return [
        ffmpeg,
        "-hide_banner",
        "-nostats",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        # No -ac / -ar: channels stay discrete; ebur128 handles weighting and
        # oversamples each channel itself for true peak.
        "-af",
        "ebur128=peak=true+sample",
        "-f",
        "null",
        "-",
    ]


def measure_ebur128_ffmpeg(
    path: Path, ffmpeg: str | None = None, timeout: int = 900
) -> R128Measurement | None:
    exe = ffmpeg or shutil.which("ffmpeg")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            ebur128_command(Path(path), exe),
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return parse_ebur128_output(proc.stderr)


def measure_r128(
    path: Path | None, data: np.ndarray | None, sr: int | None, use_ffmpeg: bool = True
) -> R128Measurement:
    """
    Best available BS.1770-4 measurement for a file.

    ``data``/``sr`` must be the native-rate, all-channel samples (``(n, ch)``).
    """
    ff = measure_ebur128_ffmpeg(path) if (use_ffmpeg and path is not None) else None
    have_data = data is not None and sr and np.asarray(data).size > 0
    if not have_data:
        if ff is None:
            return R128Measurement()
        ff.ffmpeg_true_peak_dbtp = ff.true_peak_dbtp
        return ff

    native = measure_native(data, sr, loudness=ff is None or ff.integrated_lufs is None)
    if ff is not None:
        native.ffmpeg_true_peak_dbtp = ff.true_peak_dbtp
        if ff.integrated_lufs is not None:
            native.integrated_lufs = ff.integrated_lufs
            native.threshold_lufs = ff.threshold_lufs
            native.loudness_range_lu = ff.loudness_range_lu
            native.method += "+ffmpeg-ebur128-loudness"
    if not native.true_peak_dbtp_per_channel and ff is not None:
        native.true_peak_dbtp = ff.true_peak_dbtp
    return native
