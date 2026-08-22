from __future__ import annotations

import array
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from ..core.models import AudioInfo, AudioMetrics, LoudnessMetrics, TrackAnalysis
from .ffmpeg import FFmpeg

NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _last_metric(label: str, text: str) -> float | None:
    values = re.findall(rf"{re.escape(label)}:\s*({NUMBER}|-?inf|nan)", text, re.IGNORECASE)
    parsed = [_float(value) for value in values]
    valid = [value for value in parsed if value is not None]
    return valid[-1] if valid else None


class MetricsAnalyzer:
    def __init__(self, ffmpeg: FFmpeg, settings: dict[str, Any]) -> None:
        self.ffmpeg = ffmpeg
        self.settings = settings

    def analyze(self, path: Path) -> TrackAnalysis:
        path = path.resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        data = self.ffmpeg.probe(path)
        audio = self._audio_info(path, data)
        loudness = self._loudness(path)
        stats = self._astats(path)
        width = self._stereo_width(path, audio.channels)
        correlation = self._phase_correlation(path, audio.channels)
        spectral = self._spectral_balance(path)
        waveform = self._waveform(path)

        peak = stats["peak"]
        rms = stats["rms"]
        dynamic = round(peak - rms, 2) if peak is not None and rms is not None else None
        crest_ratio = round(10 ** (dynamic / 20), 2) if dynamic is not None else None
        clipped = 0
        if peak is not None and peak >= -0.01:
            clipped = max(1, int(round(stats["peak_count"] or 1)))

        metrics = AudioMetrics(
            loudness=loudness,
            peak_dbfs=peak,
            rms_dbfs=rms,
            dynamic_range_db=dynamic,
            crest_factor=crest_ratio,
            clipped_samples_estimate=clipped,
            noise_floor_dbfs=stats["noise_floor"],
            stereo_width_percent=width,
            phase_correlation=correlation,
            spectral_balance_db=spectral,
            waveform=waveform,
        )
        return TrackAnalysis(audio=audio, metrics=metrics)

    @staticmethod
    def _audio_info(path: Path, data: dict[str, Any]) -> AudioInfo:
        format_data = data.get("format") or {}
        streams = data.get("streams") or []
        stream = next((item for item in streams if item.get("codec_type") == "audio"), {})
        bit_depth = _int(stream.get("bits_per_raw_sample")) or _int(stream.get("bits_per_sample"))
        bit_rate = _int(stream.get("bit_rate")) or _int(format_data.get("bit_rate"))
        return AudioInfo(
            file_name=path.name,
            path=str(path),
            size_bytes=path.stat().st_size,
            duration_seconds=round(_float(format_data.get("duration")) or 0.0, 3),
            format_name=str(format_data.get("format_name") or "unknown"),
            codec_name=str(stream.get("codec_name") or "unknown"),
            codec_long_name=str(stream.get("codec_long_name") or "unknown"),
            sample_rate_hz=_int(stream.get("sample_rate")) or 0,
            channels=_int(stream.get("channels")) or 0,
            channel_layout=str(stream.get("channel_layout") or "unknown"),
            bit_rate_bps=bit_rate,
            bit_depth=bit_depth,
        )

    def _loudness(self, path: Path) -> LoudnessMetrics:
        result = self.ffmpeg.run_checked(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                "loudnorm=I=-14:TP=-1:LRA=11:print_format=json",
                "-f",
                "null",
                "-",
            ]
        )
        match = re.search(r'\{\s*"input_i".*?\}', result.stdout + "\n" + result.stderr, re.DOTALL)
        if not match:
            return LoudnessMetrics(None, None, None, None)
        try:
            values = json.loads(match.group(0))
        except json.JSONDecodeError:
            return LoudnessMetrics(None, None, None, None)
        return LoudnessMetrics(
            integrated_lufs=_rounded(values.get("input_i")),
            loudness_range_lu=_rounded(values.get("input_lra")),
            true_peak_dbtp=_rounded(values.get("input_tp")),
            threshold_lufs=_rounded(values.get("input_thresh")),
        )

    def _astats(self, path: Path, prefix: str | None = None) -> dict[str, float | None]:
        audio_filter = "astats=metadata=0:reset=0"
        if prefix:
            audio_filter = f"{prefix},{audio_filter}"
        result = self.ffmpeg.run_checked(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                audio_filter,
                "-f",
                "null",
                "-",
            ]
        )
        text = result.stdout + "\n" + result.stderr
        return {
            "peak": _rounded(_last_metric("Peak level dB", text)),
            "rms": _rounded(_last_metric("RMS level dB", text)),
            "noise_floor": _rounded(_last_metric("Noise floor dB", text)),
            "peak_count": _last_metric("Peak count", text),
        }

    def _stereo_width(self, path: Path, channels: int) -> float | None:
        if channels < 2:
            return 0.0
        result = self.ffmpeg.run_checked(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0-0.5*c1,astats=metadata=0:reset=0",
                "-f",
                "null",
                "-",
            ]
        )
        values = re.findall(rf"RMS level dB:\s*({NUMBER}|-?inf)", result.stderr, re.IGNORECASE)
        parsed = [_float(value) for value in values[:2]]
        if len(parsed) < 2 or parsed[0] is None or parsed[1] is None:
            return None
        ratio = 100.0 * (10 ** ((parsed[1] - parsed[0]) / 20.0))
        return round(max(0.0, min(200.0, ratio)), 1)

    def _phase_correlation(self, path: Path, channels: int) -> float | None:
        if channels < 2:
            return 1.0
        result = self.ffmpeg.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                "aphasemeter=video=0,ametadata=print:key=lavfi.aphasemeter.phase:file=-",
                "-f",
                "null",
                "-",
            ]
        )
        if result.returncode:
            return None
        values = [
            float(value)
            for value in re.findall(
                rf"lavfi\.aphasemeter\.phase=({NUMBER})", result.stdout + result.stderr
            )
        ]
        if not values:
            return None
        return round(max(-1.0, min(1.0, sum(values) / len(values))), 3)

    def _spectral_balance(self, path: Path) -> dict[str, float | None]:
        bands = self.settings["analysis"]["spectral_bands_hz"]
        workers = max(1, min(int(self.settings["analysis"].get("spectral_workers", 3)), len(bands)))

        def analyze_band(item: tuple[str, list[int]]) -> tuple[str, float | None]:
            name, limits = item
            low, high = limits
            center = math.sqrt(low * high)
            width = high - low
            stats = self._astats(path, f"bandpass=f={center:.3f}:w={width}")
            return name, stats["rms"]

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="spectral") as pool:
            return dict(pool.map(analyze_band, bands.items()))

    def _waveform(self, path: Path) -> list[float]:
        target_points = max(40, int(self.settings["analysis"].get("waveform_points", 180)))
        raw = self.ffmpeg.run_bytes(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "200",
                "-f",
                "f32le",
                "-",
            ]
        )
        samples = array.array("f")
        samples.frombytes(raw)
        if not samples:
            return []
        chunk = max(1, math.ceil(len(samples) / target_points))
        envelope = [
            max(abs(value) for value in samples[index : index + chunk])
            for index in range(0, len(samples), chunk)
        ]
        peak = max(envelope) or 1.0
        return [round(min(1.0, value / peak), 4) for value in envelope[:target_points]]


def _rounded(value: Any, digits: int = 2) -> float | None:
    parsed = _float(value)
    return round(parsed, digits) if parsed is not None else None
