"""
Streaming-ready master export for CoProducer.

Measures integrated loudness (pyloudnorm), applies gain to a platform preset
target, limits true peak, and writes the final file (WAV / FLAC / MP3 via the
shared convert pipeline).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from .analyzer import compute_true_peak_dbtp
from .convert import convert_one

try:
    import pyloudnorm as pyln

    HAS_PYLOUDNORM = True
except Exception:  # pragma: no cover
    HAS_PYLOUDNORM = False
    pyln = None  # type: ignore

try:
    import pedalboard as pb

    HAS_PEDALBOARD = True
except Exception:  # pragma: no cover
    HAS_PEDALBOARD = False
    pb = None  # type: ignore


STREAM_PRESETS: dict[str, dict[str, float]] = {
    "Spotify  (−14 LUFS)": {"lufs": -14.0, "tpt": -1.0},
    "YouTube / TikTok  (−14 LUFS)": {"lufs": -14.0, "tpt": -1.0},
    "Apple Music  (−16 LUFS)": {"lufs": -16.0, "tpt": -1.0},
    "Podcast  (−16 LUFS)": {"lufs": -16.0, "tpt": -1.5},
    "Club / Loud  (−9 LUFS)": {"lufs": -9.0, "tpt": -1.0},
    "Broadcast EBU R128  (−23 LUFS)": {"lufs": -23.0, "tpt": -2.0},
}

EXPORT_FORMATS: list[tuple[str, str]] = [
    ("wav", "WAV 24-bit"),
    ("flac", "FLAC"),
    ("mp3", "MP3 320k"),
]


def _measure_lufs(audio: np.ndarray, sr: int) -> float | None:
    if not HAS_PYLOUDNORM:
        return None
    try:
        meter = pyln.Meter(sr)
        x = np.asarray(audio, dtype=np.float32)
        return float(meter.integrated_loudness(x))
    except Exception:
        return None


def master_export(
    source: str,
    dest: str,
    *,
    preset: str = "Spotify  (−14 LUFS)",
    format_key: str = "wav",
    status: Any = None,
) -> dict[str, Any]:
    """Normalize `source` to a streaming preset and write `dest`.

    Returns {"ok": bool, "error"?, "measured_lufs"?, "target_lufs"?,
    "gain_db"?, "true_peak_dbtp"?, "format"?, "dest"?}.
    """
    result: dict[str, Any] = {"ok": False}
    try:
        preset_key = preset if preset in STREAM_PRESETS else "Spotify  (−14 LUFS)"
        target = STREAM_PRESETS[preset_key]
        target_lufs = float(target["lufs"])
        tpt = float(target["tpt"])

        src = Path(source)
        dst = Path(dest)
        if not src.is_file():
            result["error"] = f"source missing: {src}"
            return result
        if format_key not in {f[0] for f in EXPORT_FORMATS}:
            format_key = "wav"
        if dst.suffix.lower() == f".{format_key}":
            final_dst = dst
        else:
            final_dst = dst.with_suffix(f".{format_key}")
        final_dst.parent.mkdir(parents=True, exist_ok=True)

        if status:
            status("reading audio…")
        audio, sr = sf.read(str(src), dtype="float32", always_2d=True)
        n_frames, n_ch = audio.shape

        measured = _measure_lufs(audio, sr)
        if measured is None or not np.isfinite(measured):
            result["error"] = "loudness measurement failed (pyloudnorm unavailable)"
            return result

        gain_db = float(target_lufs - measured)
        if status:
            status(f"gain {gain_db:+.1f} dB to {target_lufs:.0f} LUFS…")
        audio = audio * float(10.0 ** (gain_db / 20.0))

        peak_db = compute_true_peak_dbtp(audio.mean(axis=1), oversample=4)
        if peak_db > tpt:
            if HAS_PEDALBOARD:
                if status:
                    status(f"limiting true peak {peak_db:+.1f} → {tpt:+.1f} dBTP…")
                limiter = pb.Limiter(threshold_db=tpt)
                audio = np.asarray(limiter(audio, sr), dtype=np.float32)
            else:
                limit = float(10.0 ** (tpt / 20.0))
                peak = float(np.max(np.abs(audio)))
                if peak > 0:
                    audio = audio * (limit / peak)
                if status:
                    status("pedalboard unavailable — peak-scaled")
        audio = np.clip(audio, -1.0, 1.0).astype(np.float32)

        if status:
            status("writing file…")
        wav_tmp = final_dst if format_key == "wav" else final_dst.with_name(f"{final_dst.stem}_master_tmp.wav")
        sf.write(str(wav_tmp), audio, sr, subtype="PCM_24")

        if format_key != "wav":
            cres = convert_one(wav_tmp, final_dst)
            if not cres.get("ok"):
                result["error"] = f"convert: {cres.get('error')}"
                return result
        elif not wav_tmp.is_file():
            result["error"] = "write failed"
            return result

        final_tp = compute_true_peak_dbtp(audio.mean(axis=1), oversample=4)
        result.update(
            {
                "ok": True,
                "measured_lufs": round(measured, 1),
                "target_lufs": target_lufs,
                "gain_db": round(gain_db, 1),
                "true_peak_dbtp": round(float(final_tp), 1),
                "format": format_key,
                "channels": n_ch,
                "sample_rate": sr,
                "frames": n_frames,
                "dest": str(final_dst),
            }
        )
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
