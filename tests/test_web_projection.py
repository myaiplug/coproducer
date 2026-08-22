from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from nodaw.features.web_projection import (  # noqa: E402
    PROMO,
    public_analyze_payload,
    project_from_report,
    vs_target_rows,
)

SETTINGS = {
    "analysis": {
        "target_lufs": -14.0,
        "true_peak_ceiling_dbtp": -1.0,
        "minimum_sample_rate_hz": 44100,
    }
}


def hot_report() -> dict:
    return {
        "score": 70,
        "rating": "Usable after technical corrections",
        "summary": "hot",
        "findings": [{"severity": "warning", "title": "Unsafe true peak", "message": "x", "action": "y", "score_penalty": 14}],
        "streaming_analysis": {"platforms": []},
        "track": {
            "audio": {
                "file_name": "hot.wav",
                "path": "D:\\secret\\hot.wav",
                "size_bytes": 1000,
                "duration_seconds": 10.0,
                "format_name": "wav",
                "codec_name": "pcm_s16le",
                "codec_long_name": "PCM",
                "sample_rate_hz": 44100,
                "channels": 2,
                "channel_layout": "stereo",
                "bit_rate_bps": 1411200,
                "bit_depth": 16,
            },
            "metrics": {
                "loudness": {
                    "integrated_lufs": -8.0,
                    "loudness_range_lu": 5.0,
                    "true_peak_dbtp": 0.4,
                    "threshold_lufs": None,
                    "sample_peak_dbfs": -0.1,
                },
                "peak_dbfs": -0.1,
                "rms_dbfs": -7.0,
                "dynamic_range_db": 8.0,
                "crest_factor": 3.0,
                "clipped_samples_estimate": 40,
                "noise_floor_dbfs": -55.0,
                "stereo_width_percent": 70.0,
                "phase_correlation": 0.85,
                "spectral_balance_db": {"sub_bass": -6.0, "bass": 0.0},
                "waveform": [0.2, 0.9],
            },
            "extra": {},
        },
    }


def clean_report() -> dict:
    r = hot_report()
    r["score"] = 97
    m = r["track"]["metrics"]
    m["loudness"]["integrated_lufs"] = -14.0
    m["loudness"]["true_peak_dbtp"] = -1.2
    m["loudness"]["sample_peak_dbfs"] = -1.2
    m["peak_dbfs"] = -1.2
    m["rms_dbfs"] = -12.0
    m["clipped_samples_estimate"] = 0
    # Outside detect_repair_plan highpass window (-70..-42); empty plan must be genuine
    m["noise_floor_dbfs"] = -80.0
    return r


class ProjectionTests(unittest.TestCase):
    def test_loudnorm_projects_lufs_and_true_peak(self) -> None:
        out = project_from_report(hot_report(), SETTINGS)
        self.assertTrue(out["needed"])
        self.assertEqual(out["metrics"]["loudness"]["integrated_lufs"], -14.0)
        self.assertEqual(out["metrics"]["loudness"]["true_peak_dbtp"], -1.0)
        self.assertEqual(out["metrics"]["clipped_samples_estimate"], 0)
        ids = {a["id"] for a in out["plan"]["actions"]}
        self.assertIn("loudnorm", ids)
        self.assertGreaterEqual(out["score"], 70)

    def test_empty_plan_copies_measured(self) -> None:
        out = project_from_report(clean_report(), SETTINGS)
        self.assertFalse(out["needed"])
        self.assertEqual(out["metrics"]["loudness"]["integrated_lufs"], -14.0)
        self.assertEqual(out["score"], 97)

    def test_phase_unchanged(self) -> None:
        r = hot_report()
        r["track"]["metrics"]["phase_correlation"] = -0.2
        out = project_from_report(r, SETTINGS)
        self.assertEqual(out["metrics"]["phase_correlation"], -0.2)
        self.assertTrue(any("phase" in c.lower() for c in out["plan"]["cautions"]))

    def test_vs_target_and_path_strip(self) -> None:
        payload = public_analyze_payload(hot_report(), SETTINGS)
        self.assertIsNone(payload["track"]["audio"].get("path"))
        self.assertEqual(payload["promo"], PROMO)
        lufs = next(row for row in payload["vs_target"] if row["metric"] == "Integrated LUFS")
        self.assertEqual(lufs["target"], -14.0)
        self.assertEqual(lufs["projected"], -14.0)
        self.assertEqual(lufs["status_projected"], "pass")
        self.assertEqual(lufs["status_yours"], "off")


if __name__ == "__main__":
    unittest.main()
