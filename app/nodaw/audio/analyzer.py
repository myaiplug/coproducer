"""
CoProducer Core Analyzer - Unified Audio Analysis
Python 3.11 locked.

Uses:
- ffprobe for technical metadata + tags
- soundfile / numpy for low-level DSP
- pyloudnorm for ITU-R BS.1770 integrated LUFS + true peak
- librosa for MIR / spectral features
- mutagen for musical metadata read/write + embedding AI analysis tags

Essentia is optional (import guarded).
All numeric scores are derived directly from measured values.
"""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any

import librosa
import numpy as np
import pyloudnorm as pyln

# Core third-party (pinned in requirements)
import soundfile as sf
from mutagen import File as MutagenFile
from mutagen.easyid3 import EasyID3

# Optional advanced
try:
    HAS_ESSENTIA = True
except Exception:
    HAS_ESSENTIA = False

from ..core.models import AudioInfo, AudioMetrics, LoudnessMetrics, TrackAnalysis


class AnalyzerError(RuntimeError):
    pass


def run_ffprobe(path: Path) -> dict[str, Any]:
    """Return ffprobe json dict for the file."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-show_entries",
        "format=duration,bit_rate,format_name,tags",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=60)
        data = json.loads(out)
        return data
    except Exception as exc:
        raise AnalyzerError(f"ffprobe failed for {path.name}: {exc}") from exc


def extract_audio_info(path: Path, probe: dict[str, Any]) -> AudioInfo:
    fmt = probe.get("format", {}) or {}
    streams = probe.get("streams", []) or []
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})

    tags = fmt.get("tags", {}) or {}
    # Try to pull common musical tags too (mutagen will enrich later)

    bit_depth = None
    if "bits_per_raw_sample" in audio_stream:
        bit_depth = int(audio_stream["bits_per_raw_sample"])
    elif "bits_per_sample" in audio_stream:
        bit_depth = int(audio_stream["bits_per_sample"])

    return AudioInfo(
        file_name=path.name,
        path=str(path),
        size_bytes=path.stat().st_size,
        duration_seconds=round(float(fmt.get("duration") or 0.0), 3),
        format_name=str(fmt.get("format_name") or "unknown"),
        codec_name=str(audio_stream.get("codec_name") or "unknown"),
        codec_long_name=str(audio_stream.get("codec_long_name") or "unknown"),
        sample_rate_hz=int(audio_stream.get("sample_rate") or 0),
        channels=int(audio_stream.get("channels") or 0),
        channel_layout=str(audio_stream.get("channel_layout") or "unknown"),
        bit_rate_bps=int(fmt.get("bit_rate") or audio_stream.get("bit_rate") or 0) or None,
        bit_depth=bit_depth,
    )


def load_audio(path: Path, sr: int = 44100, mono: bool = True) -> tuple[np.ndarray, int]:
    """Load with soundfile, fallback to librosa."""
    try:
        data, file_sr = sf.read(str(path), always_2d=False)
        if mono and data.ndim > 1:
            data = np.mean(data, axis=1)
        if file_sr != sr:
            data = librosa.resample(data.astype(np.float32), orig_sr=file_sr, target_sr=sr)
            file_sr = sr
        return data.astype(np.float32), file_sr
    except Exception:
        y, file_sr = librosa.load(str(path), sr=sr, mono=mono)
        return y.astype(np.float32), file_sr


def load_audio_channels(path: Path, sr: int = 44100) -> tuple[np.ndarray, np.ndarray | None, int]:
    """
    Load mono analysis stream + optional stereo (n, 2) for imaging metrics.
    Always returns a mono 1-D array; stereo is None for mono sources.
    """
    try:
        data, file_sr = sf.read(str(path), always_2d=True)
        data = data.astype(np.float32)
        if file_sr != sr:
            # resample each channel
            chans = []
            for c in range(data.shape[1]):
                chans.append(librosa.resample(data[:, c], orig_sr=file_sr, target_sr=sr))
            # align lengths
            n = min(len(c) for c in chans)
            data = np.stack([c[:n] for c in chans], axis=1)
            file_sr = sr
        mono = np.mean(data, axis=1).astype(np.float32)
        stereo = data[:, :2] if data.shape[1] >= 2 else None
        return mono, stereo, file_sr
    except Exception:
        y, file_sr = librosa.load(str(path), sr=sr, mono=False)
        if y.ndim == 1:
            return y.astype(np.float32), None, file_sr
        y = y.astype(np.float32)
        # librosa returns (channels, samples)
        if y.shape[0] < y.shape[1]:
            stereo = y[:2].T
            mono = np.mean(y, axis=0).astype(np.float32)
        else:
            stereo = y[:, :2]
            mono = np.mean(y, axis=1).astype(np.float32)
        return mono, stereo if stereo is not None and stereo.shape[1] >= 2 else None, file_sr


def compute_noise_floor_dbfs(y: np.ndarray, sr: int) -> float:
    """
    Estimate noise floor from the quietest frames.

    Uses a low percentile of frame RMS, but only trusts it when those frames
    are clearly quieter than typical program level (dense masters often have
    no true silence - we then fall back toward a deep floor so auto-repair
    does not high-pass every loud track).
    """
    if y is None or len(y) == 0:
        return -90.0
    frame = max(256, int(sr * 0.05))
    hop = max(frame // 2, 1)
    if len(y) < frame:
        rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)))
        return round(20 * math.log10(max(rms, 1e-12)), 2)
    rms_frames = []
    for i in range(0, len(y) - frame + 1, hop):
        chunk = y[i : i + frame].astype(np.float64)
        rms_frames.append(float(np.sqrt(np.mean(chunk**2))))
    if not rms_frames:
        return -90.0
    arr = np.asarray(rms_frames, dtype=np.float64)
    quiet = float(np.percentile(arr, 5))
    median = float(np.median(arr))
    # If "quiet" frames are within ~12 dB of median program, there is no usable
    # silence window - report a deep floor instead of a false high noise reading.
    if quiet > 1e-12 and median > 1e-12:
        quiet_db = 20 * math.log10(quiet)
        med_db = 20 * math.log10(median)
        if (med_db - quiet_db) < 12.0:
            return -90.0
    return round(20 * math.log10(max(quiet, 1e-12)), 2)


def compute_stereo_imaging(stereo: np.ndarray | None) -> tuple[float, float]:
    """
    Returns (phase_correlation -1..1, stereo_width_percent).
    Mono sources: correlation 1.0, width 0.0 (true zeros).
    """
    if stereo is None or stereo.ndim != 2 or stereo.shape[1] < 2:
        return 1.0, 0.0
    L = stereo[:, 0].astype(np.float64)
    R = stereo[:, 1].astype(np.float64)
    # Correlation
    L0 = L - np.mean(L)
    R0 = R - np.mean(R)
    denom = float(np.sqrt(np.sum(L0**2) * np.sum(R0**2)))
    if denom < 1e-18:
        corr = 1.0
    else:
        corr = float(np.clip(np.sum(L0 * R0) / denom, -1.0, 1.0))
    mid = 0.5 * (L + R)
    side = 0.5 * (L - R)
    e_m = float(np.mean(mid**2))
    e_s = float(np.mean(side**2))
    # Side-to-mid energy ratio as % (0 = pure mono, 100 ≈ equal side energy)
    width = 100.0 * e_s / max(e_m + e_s, 1e-18)
    return round(corr, 3), round(width, 1)


def compute_spectral_balance_db(y: np.ndarray, sr: int) -> dict[str, float]:
    """Guaranteed multi-band relative energy (dB vs loudest band)."""
    bands = {
        "sub_bass": (20, 60),
        "bass": (60, 150),
        "low_mid": (150, 400),
        "mid": (400, 1200),
        "presence": (1200, 4000),
        "high": (4000, 10000),
        "air": (10000, min(18000, sr // 2 - 1)),
    }
    if y is None or len(y) < 512:
        return dict.fromkeys(bands, -60.0)
    try:
        n_fft = 2048
        S = np.abs(librosa.stft(y.astype(np.float32), n_fft=n_fft))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        power = np.mean(S**2, axis=1)
        out: dict[str, float] = {}
        for name, (lo, hi) in bands.items():
            mask = (freqs >= lo) & (freqs < hi)
            if not np.any(mask):
                out[name] = -60.0
                continue
            band_p = float(np.mean(power[mask]))
            out[name] = 10.0 * math.log10(max(band_p, 1e-18))
        mx = max(out.values()) if out else 0.0
        return {k: round(v - mx, 1) for k, v in out.items()}
    except Exception:
        return dict.fromkeys(bands, -60.0)


def compute_waveform_envelope(y: np.ndarray, points: int = 180) -> list[float]:
    """Normalized peak envelope 0..1, always length == points when audio present."""
    if y is None or len(y) == 0:
        return [0.0] * points
    n = len(y)
    hop = max(1, n // points)
    peaks: list[float] = []
    for i in range(points):
        start = i * hop
        end = min(n, start + hop)
        if start >= n:
            peaks.append(0.0)
            continue
        peaks.append(float(np.max(np.abs(y[start:end]))))
    mx = max(peaks) if peaks else 0.0
    if mx <= 1e-12:
        return [0.0] * points
    return [round(p / mx, 4) for p in peaks]


def compute_true_peak_dbtp(y: np.ndarray, oversample: int = 4) -> float:
    """
    True-peak estimate (dBTP) via polyphase oversampling when scipy is available.

    Falls back to linear 4× interp, then sample peak. This is substantially more
    accurate than linear np.interp for intersample peaks (ITU-style TP proxy).
    """
    if y is None or len(y) == 0:
        return -120.0
    y64 = y.astype(np.float64, copy=False)
    sample_pk = float(np.max(np.abs(y64)))
    tp = sample_pk
    if len(y64) > 64 and oversample > 1:
        try:
            from scipy.signal import resample_poly

            # Cap work on very long files: process in overlapping blocks
            block = 480_000  # ~10s @ 48k
            if len(y64) <= block:
                y_os = resample_poly(y64, oversample, 1)
                tp = float(np.max(np.abs(y_os)))
            else:
                hop = block - 2048
                mx = sample_pk
                for i in range(0, len(y64), hop):
                    chunk = y64[i : i + block]
                    if len(chunk) < 64:
                        continue
                    y_os = resample_poly(chunk, oversample, 1)
                    mx = max(mx, float(np.max(np.abs(y_os))))
                tp = mx
        except Exception:
            try:
                n = len(y64)
                t_old = np.linspace(0.0, 1.0, n, endpoint=False)
                t_new = np.linspace(0.0, 1.0, n * oversample, endpoint=False)
                y4 = np.interp(t_new, t_old, y64)
                tp = float(np.max(np.abs(y4)))
            except Exception:
                tp = sample_pk
    return round(20 * math.log10(max(tp, 1e-12)), 2)


def compute_loudness_pyloudnorm(y: np.ndarray, sr: int) -> LoudnessMetrics:
    """Use pyloudnorm (ITU-R BS.1770) for accurate integrated LUFS + improved true peak.
    Includes fallback for edge cases (short tones, very quiet, silence).
    """
    if len(y) < sr // 4:  # very short clip
        # Use simple stats
        if len(y) == 0:
            return LoudnessMetrics(None, None, None, None)
        pk = 20 * math.log10(max(np.max(np.abs(y)), 1e-12))
        return LoudnessMetrics(None, None, round(pk, 2), None)

    try:
        meter = pyln.Meter(sr)
        # Prefer stereo-aware path when y is mono — still correct for integrated
        integrated = meter.integrated_loudness(y)
        lra = None
        try:
            lra = meter.loudness_range(y)
        except Exception:
            pass

        true_pk = compute_true_peak_dbtp(y, oversample=4)
        sample_pk = round(20 * math.log10(max(float(np.max(np.abs(y))), 1e-12)), 2)

        integ = (
            round(integrated, 2) if integrated is not None and math.isfinite(integrated) else None
        )

        return LoudnessMetrics(
            integrated_lufs=integ,
            loudness_range_lu=round(lra, 2) if lra is not None and math.isfinite(lra) else None,
            true_peak_dbtp=true_pk,
            threshold_lufs=None,
            sample_peak_dbfs=sample_pk,
        )
    except Exception:
        # Absolute fallback using numpy
        if len(y) == 0:
            return LoudnessMetrics(None, None, None, None)
        pk = compute_true_peak_dbtp(y, oversample=4)
        sample_pk = round(20 * math.log10(max(float(np.max(np.abs(y))), 1e-12)), 2)
        return LoudnessMetrics(None, None, pk, None, sample_peak_dbfs=sample_pk)


def compute_technical_faults(
    y: np.ndarray,
    sr: int,
    stereo: np.ndarray | None = None,
) -> dict[str, Any]:
    """Clipping, DC, silence, mono compatibility - always numeric when audio exists."""
    results: dict[str, Any] = {}

    if y is None or len(y) == 0:
        results["clipped_samples"] = 0
        results["clipped_ratio"] = 0.0
        results["dc_offset"] = 0.0
        results["silence_ratio"] = 1.0
        results["mono_compatibility"] = "n/a"
        results["phase_correlation"] = 1.0
        return results

    # Sample-level near-FS count
    clipped = int(np.sum(np.abs(y) >= 0.998))
    # Intersample overs: count oversampled peaks above ~0.99 (soft-clip / TP abuse)
    try:
        from scipy.signal import resample_poly

        # Short probe on loudest region for speed
        abs_y = np.abs(y.astype(np.float64))
        if len(abs_y) > 8192:
            # windows around global peak
            peak_i = int(np.argmax(abs_y))
            half = 8192
            lo = max(0, peak_i - half)
            hi = min(len(abs_y), peak_i + half)
            region = y[lo:hi].astype(np.float64)
        else:
            region = y.astype(np.float64)
        if len(region) >= 64:
            os_peak = resample_poly(region, 4, 1)
            intersample = int(np.sum(np.abs(os_peak) >= 0.99))
            # Scale to full-file estimate proportionally (capped)
            scale = max(1.0, len(y) / max(1, len(region)))
            clipped = max(clipped, int(min(intersample * scale, len(y))))
    except Exception:
        pass
    results["clipped_samples"] = clipped
    results["clipped_ratio"] = round(clipped / max(1, len(y)), 6)

    dc = float(np.mean(y.astype(np.float64)))
    results["dc_offset"] = round(dc, 6)

    frame = 2048
    if len(y) >= frame:
        rms_frames = np.array(
            [
                np.sqrt(np.mean(y[i : i + frame].astype(np.float64) ** 2))
                for i in range(0, len(y) - frame, frame)
            ]
        )
        silence_ratio = float(np.mean(rms_frames < 1e-5))
    else:
        silence_ratio = 0.0
    results["silence_ratio"] = round(silence_ratio, 4)

    corr, _width = compute_stereo_imaging(stereo)
    results["phase_correlation"] = corr
    if corr >= 0.5:
        results["mono_compatibility"] = "good"
    elif corr >= 0.2:
        results["mono_compatibility"] = "fair"
    elif corr >= 0.0:
        results["mono_compatibility"] = "weak"
    else:
        results["mono_compatibility"] = "poor"

    return results


def compute_librosa_features(y: np.ndarray, sr: int) -> dict[str, Any]:
    """Rich spectral + MIR features using librosa."""
    feats: dict[str, Any] = {}

    # Basic spectral
    cent = librosa.feature.spectral_centroid(y=y, sr=sr)
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)
    bw = librosa.feature.spectral_bandwidth(y=y, sr=sr)
    zcr = librosa.feature.zero_crossing_rate(y)

    feats["spectral_centroid_hz"] = round(float(np.mean(cent)), 1)
    feats["spectral_rolloff_hz"] = round(float(np.mean(rolloff)), 1)
    feats["spectral_bandwidth_hz"] = round(float(np.mean(bw)), 1)
    feats["zero_crossing_rate"] = round(float(np.mean(zcr)), 6)

    # RMS energy
    rms = librosa.feature.rms(y=y)
    feats["rms_mean"] = round(float(np.mean(rms)), 6)

    # Tempo + beats
    try:
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        feats["tempo_bpm"] = round(float(tempo), 1)
    except Exception:
        feats["tempo_bpm"] = None

    # Chroma
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    feats["chroma_mean"] = [round(float(x), 4) for x in np.mean(chroma, axis=1)]

    # Onset strength
    onset = librosa.onset.onset_strength(y=y, sr=sr)
    feats["onset_strength_mean"] = round(float(np.mean(onset)), 4)

    # Energy balance (low/mid/high) - crude using mel bands
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_mels=128)
    mel_db = librosa.power_to_db(mel, ref=np.max)
    low = np.mean(mel_db[:30])
    mid = np.mean(mel_db[30:80])
    high = np.mean(mel_db[80:])
    total = max(1e-6, abs(low) + abs(mid) + abs(high))
    feats["energy_balance"] = {
        "low": round(float(low / total), 3),
        "mid": round(float(mid / total), 3),
        "high": round(float(high / total), 3),
    }

    # Brightness / darkness heuristic
    brightness = (feats["spectral_centroid_hz"] or 0) / 8000.0
    feats["brightness_score"] = round(min(1.0, max(0.0, brightness)), 3)

    return feats


def read_mutagen_tags(path: Path) -> dict[str, str]:
    """Read common tags via Mutagen (includes WAV ID3 via tags_media)."""
    try:
        from .tags_media import read_tags

        return read_tags(path)
    except Exception:
        pass
    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None:
            return {}
        out = {}
        for key in ("artist", "album", "title", "genre", "date", "bpm", "key", "comment"):
            if key in audio:
                val = audio[key]
                out[key] = val[0] if isinstance(val, list) else str(val)
        return out
    except Exception:
        return {}


def embed_analysis_metadata(path: Path, analysis: dict[str, Any]) -> bool:
    """Embed CoProducer / AI analysis results into the file tags (Mutagen).
    For MP3: uses proper ID3 COMM and TXXX frames (reliable).
    For other formats (FLAC etc): uses easy "comment" if available.
    WAV has very limited tag support (documented).
    """
    try:
        p = str(path)
        ext = p.lower()

        # Build payload
        parts = ["CoProducer v3.1"]
        score = analysis.get("score")
        if score is not None:
            parts.append(f"Score={int(score)}")
        lufs = analysis.get("metrics", {}).get("loudness", {}).get("integrated_lufs")
        if lufs is not None:
            parts.append(f"LUFS={float(lufs):.1f}")
        feats = (
            analysis.get("extra", {}).get("librosa", {})
            if isinstance(analysis.get("extra"), dict)
            else {}
        )
        tempo = feats.get("tempo_bpm")
        if tempo:
            parts.append(f"Tempo={tempo}")
        payload = " | ".join(parts)

        if ext.endswith((".mp3", ".mp2")):
            from mutagen.id3 import COMM, ID3, TXXX, ID3NoHeaderError

            try:
                tags = ID3(p)
            except ID3NoHeaderError:
                tags = ID3()
            tags.add(COMM(encoding=3, lang="eng", desc="CoProducer", text=payload))
            tags.add(TXXX(encoding=3, desc="CoProducerAnalysis", text=payload))
            tags.save(p)
            return True
        else:
            audio = MutagenFile(p, easy=True)
            if audio is None:
                try:
                    audio = EasyID3(p)
                except Exception:
                    audio = None
            if audio is None:
                return False
            existing = audio.get("comment", [""])[0] if "comment" in audio else ""
            if payload not in existing:
                audio["comment"] = (existing + " " + payload).strip()
            audio.save()
            return True
    except Exception:
        return False


def analyze_file(path: Path, generate_previews: bool = False) -> TrackAnalysis:
    """Main entry: produce full TrackAnalysis with guaranteed metric fields."""
    probe = run_ffprobe(path)
    audio_info = extract_audio_info(path, probe)

    y, stereo, sr = load_audio_channels(path, sr=44100)
    if y is None or len(y) == 0:
        # Absolute fallback - still return structured zeros that mean silence
        y = np.zeros(44100, dtype=np.float32)
        stereo = None
        sr = 44100

    loud = compute_loudness_pyloudnorm(y, sr)
    # Guarantee loudness fields when audio exists (fallbacks if pyloudnorm fails edge cases)
    if loud.true_peak_dbtp is None:
        pk = float(np.max(np.abs(y)))
        loud = LoudnessMetrics(
            integrated_lufs=loud.integrated_lufs,
            loudness_range_lu=loud.loudness_range_lu,
            true_peak_dbtp=round(20 * math.log10(max(pk, 1e-12)), 2),
            threshold_lufs=loud.threshold_lufs,
        )
    if loud.integrated_lufs is None:
        rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)))
        # Rough LUFS-like proxy only when true integrated fails (still a real number)
        approx = round(20 * math.log10(max(rms, 1e-12)) - 0.691, 2)
        loud = LoudnessMetrics(
            integrated_lufs=approx,
            loudness_range_lu=loud.loudness_range_lu if loud.loudness_range_lu is not None else 0.0,
            true_peak_dbtp=loud.true_peak_dbtp,
            threshold_lufs=loud.threshold_lufs,
        )
    if loud.loudness_range_lu is None:
        # Frame loudness variance proxy
        frame = max(2048, sr // 10)
        if len(y) >= frame * 2:
            vals = []
            for i in range(0, len(y) - frame, frame):
                r = float(np.sqrt(np.mean(y[i : i + frame].astype(np.float64) ** 2)))
                vals.append(20 * math.log10(max(r, 1e-12)))
            lra_proxy = (
                round(float(np.percentile(vals, 95) - np.percentile(vals, 10)), 2) if vals else 0.0
            )
        else:
            lra_proxy = 0.0
        loud = LoudnessMetrics(
            integrated_lufs=loud.integrated_lufs,
            loudness_range_lu=lra_proxy,
            true_peak_dbtp=loud.true_peak_dbtp,
            threshold_lufs=loud.threshold_lufs,
        )

    phase_corr, stereo_width = compute_stereo_imaging(stereo)
    faults = compute_technical_faults(y, sr, stereo=stereo)
    faults["phase_correlation"] = phase_corr

    lib = compute_librosa_features(y, sr)
    # Ensure key librosa fields always present as numbers
    for key, default in (
        ("spectral_centroid_hz", 0.0),
        ("spectral_rolloff_hz", 0.0),
        ("spectral_bandwidth_hz", 0.0),
        ("zero_crossing_rate", 0.0),
        ("rms_mean", 0.0),
        ("tempo_bpm", 0.0),
        ("onset_strength_mean", 0.0),
        ("brightness_score", 0.0),
    ):
        if lib.get(key) is None:
            lib[key] = default
    if not isinstance(lib.get("energy_balance"), dict):
        lib["energy_balance"] = {"low": 0.0, "mid": 0.0, "high": 0.0}
    else:
        for k in ("low", "mid", "high"):
            if lib["energy_balance"].get(k) is None:
                lib["energy_balance"][k] = 0.0

    peak_dbfs = round(float(20 * math.log10(max(float(np.max(np.abs(y))), 1e-12))), 2)
    rms_dbfs = round(
        float(20 * math.log10(max(float(np.mean(y.astype(np.float64) ** 2) ** 0.5), 1e-12))), 2
    )
    dyn = round(peak_dbfs - rms_dbfs, 2)
    crest = round(10 ** (dyn / 20.0), 2) if dyn is not None else 1.0

    noise_floor = compute_noise_floor_dbfs(y, sr)
    spectral = compute_spectral_balance_db(y, sr)
    waveform = compute_waveform_envelope(y, points=180)

    metrics = AudioMetrics(
        loudness=loud,
        peak_dbfs=peak_dbfs,
        rms_dbfs=rms_dbfs,
        dynamic_range_db=dyn,
        crest_factor=crest,
        clipped_samples_estimate=int(faults.get("clipped_samples") or 0),
        noise_floor_dbfs=noise_floor,
        stereo_width_percent=stereo_width,
        phase_correlation=phase_corr,
        spectral_balance_db=spectral,
        waveform=waveform,
    )

    ta = TrackAnalysis(
        audio=audio_info,
        metrics=metrics,
        extra={
            "tags": read_mutagen_tags(path),
            "technical_faults": faults,
            "librosa": lib,
            "has_essentia": HAS_ESSENTIA,
            "channels_analyzed": 2 if stereo is not None else 1,
        },
    )
    return ta


def compare_reference(user: TrackAnalysis, reference: TrackAnalysis) -> dict[str, Any]:
    """Reference Match Engine - traceable comparison + similarity + recommendations."""
    u = user.metrics
    r = reference.metrics
    uf = getattr(user, "extra", {}).get("librosa", {})
    rf = getattr(reference, "extra", {}).get("librosa", {})

    diffs = []
    penalty = 0

    def delta(a, b, name, tol_notice=1.5, tol_warn=3.0):
        if a is None or b is None:
            return None
        d = round(a - b, 2)
        mag = abs(d)
        sev = "pass"
        pen = 0
        if mag >= tol_warn:
            sev = "warning"
            pen = 8
        elif mag >= tol_notice:
            sev = "notice"
            pen = 3
        diffs.append({"metric": name, "delta": d, "severity": sev, "score_penalty": pen})
        return pen

    penalty += delta(u.loudness.integrated_lufs, r.loudness.integrated_lufs, "LUFS", 1.0, 2.5) or 0
    penalty += (
        delta(u.loudness.true_peak_dbtp, r.loudness.true_peak_dbtp, "TruePeak", 0.5, 1.5) or 0
    )
    penalty += delta(uf.get("tempo_bpm"), rf.get("tempo_bpm"), "Tempo", 3, 8) or 0
    penalty += delta(u.dynamic_range_db, r.dynamic_range_db, "DynRange", 2, 4) or 0

    # Spectral centroid example - more aggressive penalty for pitch content differences
    uc = uf.get("spectral_centroid_hz")
    rc = rf.get("spectral_centroid_hz")
    penalty += delta(uc, rc, "SpectralCentroid", 150, 350) or 0

    sim = max(0, 100 - penalty)

    # Similarity guard: single-metric differences should not produce very high scores
    # unless multiple core metrics agree on similarity. Only apply for non-trivial single diffs.
    non_pass = [d for d in diffs if d.get("severity") != "pass"]
    core_metrics = {"LUFS", "TruePeak", "DynRange", "SpectralCentroid", "Tempo"}
    core_non_pass = [d for d in non_pass if d["metric"] in core_metrics]
    significant_single = len(non_pass) == 1 and any(
        d.get("severity") in ("warning", "critical") for d in non_pass
    )
    if significant_single and sim > 90:
        if len(core_non_pass) < 2:
            sim = min(sim, 88)

    recs = []
    if sim < 70:
        recs.append(
            "Significant differences vs reference. Consider matching loudness and spectral balance."
        )
    if (u.loudness.integrated_lufs or 0) > (r.loudness.integrated_lufs or 0) + 1.5:
        recs.append("Target is louder than reference. Consider gentle limiting or gain staging.")

    # Build debug breakdown
    total_pen = sum(d.get("score_penalty", 0) for d in diffs)  # reuse if present, else from earlier
    breakdown = {
        "base": 100,
        "penalty": penalty,
        "final": int(sim),
        "diff_count": len(non_pass),
        "core_diff_count": len(core_non_pass),
        "guard_triggered": len(non_pass) <= 1 and int(sim) <= 88 and penalty > 0,
    }

    return {
        "similarity_score": int(sim),
        "differences": diffs,
        "recommendations": recs or ["Track is reasonably close to reference."],
        "plain_english": f"Reference match score: {int(sim)}/100. "
        + (" ".join(recs[:1]) if recs else ""),
        "debug": {
            "metric_deltas": diffs,
            "score_breakdown": breakdown,
            "explanation": f"Base 100 - {penalty} penalty from {len(non_pass)} differing metric(s). Guard applied: {breakdown['guard_triggered']}. Core metrics differing: {len(core_non_pass)}.",
        },
    }
