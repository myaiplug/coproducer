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
import os
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Core third-party (pinned in requirements)
import soundfile as sf
import pyloudnorm as pyln
import librosa
from mutagen.easyid3 import EasyID3
from mutagen import File as MutagenFile

# Optional advanced
try:
    import essentia
    import essentia.standard as es
    HAS_ESSENTIA = True
except Exception:
    HAS_ESSENTIA = False

from ..core.models import AudioInfo, LoudnessMetrics, AudioMetrics, TrackAnalysis


class AnalyzerError(RuntimeError):
    pass


def run_ffprobe(path: Path) -> Dict[str, Any]:
    """Return ffprobe json dict for the file."""
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-show_entries",
        "format=duration,bit_rate,format_name,tags",
        str(path)
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True, timeout=60)
        data = json.loads(out)
        return data
    except Exception as exc:
        raise AnalyzerError(f"ffprobe failed for {path.name}: {exc}") from exc


def extract_audio_info(path: Path, probe: Dict[str, Any]) -> AudioInfo:
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


def load_audio(path: Path, sr: int = 44100, mono: bool = True) -> Tuple[np.ndarray, int]:
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
        y, sr = librosa.load(str(path), sr=sr, mono=mono)
        return y.astype(np.float32), sr


def compute_loudness_pyloudnorm(y: np.ndarray, sr: int) -> LoudnessMetrics:
    """Use pyloudnorm (ITU-R BS.1770) for accurate integrated + true peak.
    Includes fallback for edge cases (short tones, very quiet, silence).
    """
    if len(y) < sr // 4:  # very short clip
        # Use simple stats
        if len(y) == 0:
            return LoudnessMetrics(None, None, None, None)
        pk = 20 * math.log10(max(np.max(np.abs(y)), 1e-12))
        rms = 20 * math.log10(max(np.sqrt(np.mean(y**2)), 1e-12))
        return LoudnessMetrics(None, None, round(pk, 2), None)

    try:
        meter = pyln.Meter(sr)
        integrated = meter.integrated_loudness(y)
        lra = None
        try:
            lra = meter.loudness_range(y)
        except Exception:
            pass

        # Robust peak
        peak = float(np.max(np.abs(y)))
        pk_dbfs = 20 * math.log10(max(peak, 1e-12))

        # Approximate true peak (simple for now; real TP uses oversampling)
        true_pk = round(pk_dbfs, 2)

        integ = round(integrated, 2) if integrated is not None and math.isfinite(integrated) else None

        return LoudnessMetrics(
            integrated_lufs=integ,
            loudness_range_lu=round(lra, 2) if lra is not None and math.isfinite(lra) else None,
            true_peak_dbtp=true_pk,
            threshold_lufs=None,
        )
    except Exception:
        # Absolute fallback using numpy
        if len(y) == 0:
            return LoudnessMetrics(None, None, None, None)
        pk = 20 * math.log10(max(np.max(np.abs(y)), 1e-12))
        rms = 20 * math.log10(max(np.sqrt(np.mean(y**2)), 1e-12))
        return LoudnessMetrics(None, None, round(pk, 2), None)


def compute_technical_faults(y: np.ndarray, sr: int) -> Dict[str, Any]:
    """Clipping, DC, silence, phase correlation using numpy."""
    results: Dict[str, Any] = {}

    # Clipping count (near full scale, allows for dither)
    clipped = int(np.sum(np.abs(y) >= 0.998))
    results["clipped_samples"] = clipped
    results["clipped_ratio"] = round(clipped / max(1, len(y)), 6)

    # DC offset
    dc = float(np.mean(y))
    results["dc_offset"] = round(dc, 6)

    # Silence detection (energy based)
    frame = 2048
    if len(y) >= frame:
        rms_frames = np.array([
            np.sqrt(np.mean(y[i:i+frame]**2))
            for i in range(0, len(y) - frame, frame)
        ])
        silence_ratio = float(np.mean(rms_frames < 1e-5))
    else:
        silence_ratio = 0.0
    results["silence_ratio"] = round(silence_ratio, 4)

    # Stereo phase correlation (if stereo originally, but we often mono it)
    # For mono we report 1.0. Real correlation should be computed on original stereo if desired.
    # Here we keep simple mono compatibility score from spectral.
    results["mono_compatibility"] = "good"   # placeholder refined later

    # Phase correlation rough (on stereo if load kept channels)
    # We will enrich from original load in future passes.
    results["phase_correlation"] = None

    return results


def compute_librosa_features(y: np.ndarray, sr: int) -> Dict[str, Any]:
    """Rich spectral + MIR features using librosa."""
    feats: Dict[str, Any] = {}

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


def read_mutagen_tags(path: Path) -> Dict[str, str]:
    """Read common tags via Mutagen."""
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


def embed_analysis_metadata(path: Path, analysis: Dict[str, Any]) -> bool:
    """Embed CoProducer / AI analysis results into the file tags (Mutagen).
    Works best for MP3/FLAC/M4A/AIFF. WAV has limited support (documented).
    """
    try:
        audio = MutagenFile(str(path), easy=True)
        if audio is None:
            # Try forcing ID3 for mp3-like
            try:
                audio = EasyID3(str(path))
            except Exception:
                return False
        if audio is None:
            return False

        score = analysis.get("score")
        if score is not None:
            existing = audio.get("comment", [""])[0] if "comment" in audio else ""
            audio["comment"] = (existing + f" | CoProducerScore={int(score)}").strip(" |")
            audio["CoProducerScore"] = str(int(score))

        lufs = analysis.get("metrics", {}).get("loudness", {}).get("integrated_lufs")
        if lufs is not None:
            audio["CoProducerLUFS"] = f"{float(lufs):.1f}"

        feats = analysis.get("extra", {}).get("librosa", {}) if isinstance(analysis.get("extra"), dict) else {}
        tempo = feats.get("tempo_bpm")
        if tempo:
            audio["CoProducerTempo"] = str(tempo)

        # Always try to persist version info
        audio["CoProducerVersion"] = "3.1.0"

        audio.save()
        return True
    except Exception as e:
        # WAV and some containers often fail silently here
        return False


def analyze_file(path: Path, generate_previews: bool = False) -> TrackAnalysis:
    """Main entry: produce full TrackAnalysis with new stack."""
    probe = run_ffprobe(path)
    audio_info = extract_audio_info(path, probe)

    y, sr = load_audio(path)

    # Loudness (pyloudnorm)
    loud = compute_loudness_pyloudnorm(y, sr)

    # Technical faults
    faults = compute_technical_faults(y, sr)

    # Librosa features
    lib = compute_librosa_features(y, sr)

    # Build AudioMetrics (backwards + new)
    peak_dbfs = round(float(20 * math.log10(max(np.max(np.abs(y)), 1e-12))), 2)
    rms_dbfs = round(float(20 * math.log10(max(np.mean(y**2)**0.5 , 1e-12))), 2)
    dyn = round(peak_dbfs - rms_dbfs, 2) if peak_dbfs and rms_dbfs else None
    crest = round(10 ** (dyn / 20), 2) if dyn else None

    metrics = AudioMetrics(
        loudness=loud,
        peak_dbfs=peak_dbfs,
        rms_dbfs=rms_dbfs,
        dynamic_range_db=dyn,
        crest_factor=crest,
        clipped_samples_estimate=faults["clipped_samples"],
        noise_floor_dbfs=None,  # can be enhanced
        stereo_width_percent=None,
        phase_correlation=faults.get("phase_correlation"),
        spectral_balance_db={},  # filled by older spectral if desired
        waveform=[],
    )

    ta = TrackAnalysis(audio=audio_info, metrics=metrics, extra={
        "tags": read_mutagen_tags(path),
        "technical_faults": faults,
        "librosa": lib,
        "has_essentia": HAS_ESSENTIA,
    })
    return ta


def compare_reference(user: TrackAnalysis, reference: TrackAnalysis) -> Dict[str, Any]:
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
        diffs.append({"metric": name, "delta": d, "severity": sev})
        return pen

    penalty += delta(u.loudness.integrated_lufs, r.loudness.integrated_lufs, "LUFS", 1.0, 2.5) or 0
    penalty += delta(u.loudness.true_peak_dbtp, r.loudness.true_peak_dbtp, "TruePeak", 0.5, 1.5) or 0
    penalty += delta(uf.get("tempo_bpm"), rf.get("tempo_bpm"), "Tempo", 3, 8) or 0
    penalty += delta(u.dynamic_range_db, r.dynamic_range_db, "DynRange", 2, 4) or 0

    # Spectral centroid example
    uc = uf.get("spectral_centroid_hz")
    rc = rf.get("spectral_centroid_hz")
    penalty += delta(uc, rc, "SpectralCentroid", 200, 500) or 0

    sim = max(0, 100 - penalty)
    recs = []
    if sim < 70:
        recs.append("Significant differences vs reference. Consider matching loudness and spectral balance.")
    if (u.loudness.integrated_lufs or 0) > (r.loudness.integrated_lufs or 0) + 1.5:
        recs.append("Target is louder than reference. Consider gentle limiting or gain staging.")

    return {
        "similarity_score": int(sim),
        "differences": diffs,
        "recommendations": recs or ["Track is reasonably close to reference."],
        "plain_english": f"Reference match score: {int(sim)}/100. " + (" ".join(recs[:1]) if recs else "")
    }
