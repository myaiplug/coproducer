from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import APP_NAME

DEFAULT_SETTINGS: dict[str, Any] = {
    "brand": APP_NAME,
    "supported_extensions": [
        ".wav",
        ".mp3",
        ".flac",
        ".m4a",
        ".aac",
        ".ogg",
        ".opus",
        ".aiff",
        ".aif",
    ],
    "analysis": {
        "target_lufs": -14.0,
        "true_peak_ceiling_dbtp": -1.0,
        "minimum_sample_rate_hz": 44100,
        "spectral_workers": 3,
        "waveform_points": 180,
        "spectral_bands_hz": {
            "sub_bass": [20, 60],
            "bass": [60, 150],
            "low_mid": [150, 400],
            "mid": [400, 1200],
            "presence": [1200, 4000],
            "high": [4000, 10000],
            "air": [10000, 18000],
        },
    },
    "previews": {
        "duration_seconds": 30,
        "codec_profiles": [
            {
                "name": "MP3 320 kbps",
                "extension": ".mp3",
                "codec": "libmp3lame",
                "args": ["-b:a", "320k"],
            },
            {"name": "AAC 256 kbps", "extension": ".m4a", "codec": "aac", "args": ["-b:a", "256k"]},
            {
                "name": "Opus 160 kbps",
                "extension": ".opus",
                "codec": "libopus",
                "args": ["-b:a", "160k"],
            },
        ],
    },
    "streaming_profiles": {
        "Spotify": {"target_lufs": -14.0, "true_peak_dbtp": -1.0},
        "Apple Music": {"target_lufs": -16.0, "true_peak_dbtp": -1.0},
        "YouTube": {"target_lufs": -14.0, "true_peak_dbtp": -1.0},
        "Amazon Music": {"target_lufs": -14.0, "true_peak_dbtp": -2.0},
        "TIDAL": {"target_lufs": -14.0, "true_peak_dbtp": -1.0},
    },
    "licensing": {
        "billing_url": "http://localhost:8787",
        "gumroad_product_id": "",
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass(frozen=True, slots=True)
class ProjectPaths:
    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "config" / "settings.json"

    @property
    def song_input(self) -> Path:
        return self.root / "input" / "song"

    @property
    def reference_input(self) -> Path:
        return self.root / "input" / "reference"

    @property
    def batch_input(self) -> Path:
        return self.root / "input" / "batch"

    @property
    def album_input(self) -> Path:
        return self.root / "input" / "album"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def history_file(self) -> Path:
        return self.reports / "history" / "index.jsonl"

    def ensure(self) -> None:
        folders = [
            self.song_input,
            self.reference_input,
            self.batch_input,
            self.album_input,
            self.reports / "html",
            self.reports / "txt",
            self.reports / "json",
            self.reports / "csv",
            self.reports / "history",
            self.exports / "repairs",
            self.exports / "previews" / "codecs",
            self.exports / "previews" / "streaming",
            self.logs,
        ]
        for folder in folders:
            folder.mkdir(parents=True, exist_ok=True)


def load_settings(paths: ProjectPaths) -> dict[str, Any]:
    settings = DEFAULT_SETTINGS
    if paths.config_file.exists():
        try:
            loaded = json.loads(paths.config_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid configuration file {paths.config_file}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError("Configuration root must be a JSON object.")
        settings = _merge(DEFAULT_SETTINGS, loaded)

    extensions = settings.get("supported_extensions")
    if not isinstance(extensions, list) or not extensions:
        raise ValueError("supported_extensions must be a non-empty list.")
    bands = settings.get("analysis", {}).get("spectral_bands_hz")
    if not isinstance(bands, dict) or not bands:
        raise ValueError("analysis.spectral_bands_hz must be a non-empty object.")
    for name, limits in bands.items():
        if (
            not isinstance(limits, list)
            or len(limits) != 2
            or limits[0] <= 0
            or limits[1] <= limits[0]
        ):
            raise ValueError(f"Invalid spectral band {name}: {limits}")
    return settings
