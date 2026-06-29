from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from nodaw.config import ProjectPaths, load_settings
from nodaw.core.models import AudioInfo, AudioMetrics, LoudnessMetrics, TrackAnalysis
from nodaw.core.scoring import evaluate_track
from nodaw.reporting.renderers import render_html


class UnitTests(unittest.TestCase):
    def make_track(self) -> TrackAnalysis:
        return TrackAnalysis(
            audio=AudioInfo(
                file_name="synthetic.wav",
                path="synthetic.wav",
                size_bytes=100,
                duration_seconds=10,
                format_name="wav",
                codec_name="pcm_s16le",
                codec_long_name="PCM signed 16-bit little-endian",
                sample_rate_hz=44100,
                channels=2,
                channel_layout="stereo",
                bit_rate_bps=1411200,
                bit_depth=16,
            ),
            metrics=AudioMetrics(
                loudness=LoudnessMetrics(-14.0, 6.0, -1.2, -24.0),
                peak_dbfs=-1.1,
                rms_dbfs=-12.0,
                dynamic_range_db=10.9,
                crest_factor=3.5,
                clipped_samples_estimate=0,
                noise_floor_dbfs=-70.0,
                stereo_width_percent=70.0,
                phase_correlation=0.8,
                spectral_balance_db={"bass": -20.0, "mid": -18.0},
                waveform=[0.1, 0.6, 1.0, 0.4],
            ),
        )

    def test_clean_track_scores_100(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(Path(directory))
            paths.ensure()
            settings = load_settings(paths)
            score, _, _, findings = evaluate_track(self.make_track(), settings)
        self.assertEqual(score, 100)
        self.assertEqual(findings[0].severity, "pass")

    def test_critical_metrics_reduce_score(self) -> None:
        track = self.make_track()
        track.metrics.loudness.integrated_lufs = -5
        track.metrics.loudness.true_peak_dbtp = 0.5
        track.metrics.clipped_samples_estimate = 20
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(Path(directory))
            paths.ensure()
            score, _, _, findings = evaluate_track(track, load_settings(paths))
        self.assertLess(score, 60)
        self.assertTrue(any(item.severity == "critical" for item in findings))

    def test_invalid_config_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            paths = ProjectPaths(Path(directory))
            paths.ensure()
            paths.config_file.parent.mkdir(parents=True, exist_ok=True)
            paths.config_file.write_text(json.dumps({"supported_extensions": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_settings(paths)

    def test_html_contains_professional_charts(self) -> None:
        track = self.make_track()
        report = {
            "title": "Synthetic report",
            "run_id": "test",
            "generated_at": "2026-06-29T00:00:00Z",
            "summary": "Synthetic report for renderer verification.",
            "score": 100,
            "rating": "Release ready",
            "track": track.to_dict(),
            "findings": [],
        }
        html = render_html(report)
        self.assertIn("Waveform envelope", html)
        self.assertIn("Frequency balance", html)
        self.assertIn("Phase Correlation", html)
        self.assertIn("<svg", html)


if __name__ == "__main__":
    unittest.main()

