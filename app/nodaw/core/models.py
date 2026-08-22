from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class AudioInfo:
    file_name: str
    path: str
    size_bytes: int
    duration_seconds: float
    format_name: str
    codec_name: str
    codec_long_name: str
    sample_rate_hz: int
    channels: int
    channel_layout: str
    bit_rate_bps: int | None
    bit_depth: int | None


@dataclass(slots=True)
class LoudnessMetrics:
    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbtp: float | None
    threshold_lufs: float | None
    sample_peak_dbfs: float | None = None  # raw sample peak (always reported when known)


@dataclass(slots=True)
class AudioMetrics:
    loudness: LoudnessMetrics
    peak_dbfs: float | None
    rms_dbfs: float | None
    dynamic_range_db: float | None
    crest_factor: float | None
    clipped_samples_estimate: int
    noise_floor_dbfs: float | None
    stereo_width_percent: float | None
    phase_correlation: float | None
    spectral_balance_db: dict[str, float | None] = field(default_factory=dict)
    waveform: list[float] = field(default_factory=list)


@dataclass(slots=True)
class TrackAnalysis:
    audio: AudioInfo
    metrics: AudioMetrics
    extra: dict[str, Any] = (
        None  # CoProducer extended data (librosa, tags, faults, reference_match, ...)
    )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.extra:
            d["extra"] = self.extra
        return d

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}


@dataclass(slots=True)
class Finding:
    severity: str
    title: str
    message: str
    action: str
    score_penalty: int = 0


@dataclass(slots=True)
class RepairRecommendation:
    title: str
    reason: str
    ffmpeg_filter: str
    command: str
    caution: str


@dataclass(slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str
