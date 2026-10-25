"""BS.1770-4 / EBU R128 true-peak and stereo level-path tests.

Signals are generated in a temp dir. Tolerance is +/-0.2 dB as agreed for the
true-peak change. Every sine gets 50 ms raised-cosine fades so the ideal
reconstruction has no Gibbs overshoot from a hard start/stop.
"""

from __future__ import annotations

import math
import shutil
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

try:
    import soundfile as sf
except ImportError:  # pragma: no cover
    sf = None

from nodaw.audio import r128  # noqa: E402

TOL = 0.2
SQRT2 = math.sqrt(2.0)


def _db(x: float) -> float:
    return 20.0 * math.log10(x)


def sine(sr: int, freq: float, amp: float, phase: float = 0.0, seconds: float = 3.0) -> np.ndarray:
    t = np.arange(int(sr * seconds)) / sr
    x = amp * np.sin(2 * np.pi * freq * t + phase)
    n = int(0.05 * sr)
    w = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)
    x[:n] *= w
    x[-n:] *= w[::-1]
    return x


def fs4_sine(sr: int, sample_peak: float) -> np.ndarray:
    """fs/4 sine at 45 deg: samples hit +/-sample_peak, the waveform peaks 3.01 dB higher."""
    return sine(sr, sr / 4.0, sample_peak * SQRT2, math.pi / 4)


class NativeTruePeakTests(unittest.TestCase):
    def test_fs4_full_scale_true_peak_exceeds_sample_peak(self) -> None:
        x = fs4_sine(44100, 1.0)
        stereo = np.column_stack([x, x])
        m = r128.measure_native(stereo, 44100, loudness=False)
        self.assertAlmostEqual(m.sample_peak_dbfs, 0.0, delta=TOL)
        self.assertAlmostEqual(m.true_peak_dbtp, 3.01, delta=TOL)
        self.assertGreater(m.true_peak_dbtp - m.sample_peak_dbfs, 2.5)
        self.assertEqual(len(m.true_peak_dbtp_per_channel), 2)

    def test_fs4_at_48k(self) -> None:
        x = fs4_sine(48000, 0.5)
        m = r128.measure_native(x, 48000, loudness=False)
        self.assertAlmostEqual(m.sample_peak_dbfs, -6.02, delta=TOL)
        self.assertAlmostEqual(m.true_peak_dbtp, -3.01, delta=TOL)

    def test_minus6_dbfs_sine_true_peak_equals_sample_peak(self) -> None:
        x = sine(48000, 997.0, 0.5)
        m = r128.measure_native(np.column_stack([x, x]), 48000)
        self.assertAlmostEqual(m.sample_peak_dbfs, _db(0.5), delta=TOL)
        self.assertAlmostEqual(m.true_peak_dbtp, _db(0.5), delta=TOL)
        self.assertAlmostEqual(m.true_peak_dbtp, m.sample_peak_dbfs, delta=TOL)
        # BS.1770 sums channel power: dual-mono -6 dBFS 997 Hz sine = -6.0 LUFS (not -9).
        self.assertAlmostEqual(m.integrated_lufs, -6.0, delta=TOL)

    def test_stereo_different_levels_per_channel(self) -> None:
        sr = 48000
        left = sine(sr, 1000.0, 10 ** (-3 / 20))
        right = sine(sr, 1000.0, 10 ** (-12 / 20))
        m = r128.measure_native(np.column_stack([left, right]), sr)
        l_tp, r_tp = m.true_peak_dbtp_per_channel
        l_sp, r_sp = m.sample_peak_dbfs_per_channel
        self.assertAlmostEqual(l_tp, -3.0, delta=TOL)
        self.assertAlmostEqual(r_tp, -12.0, delta=TOL)
        self.assertAlmostEqual(l_sp, -3.0, delta=TOL)
        self.assertAlmostEqual(r_sp, -12.0, delta=TOL)
        self.assertAlmostEqual(m.true_peak_dbtp, -3.0, delta=TOL)
        # -6.01 LUFS (L) power-summed with -15.01 LUFS (R)
        expected = 10 * math.log10(10 ** (-6.01 / 10) + 10 ** (-15.01 / 10))
        self.assertAlmostEqual(m.integrated_lufs, expected, delta=TOL)

    def test_anti_phase_does_not_cancel(self) -> None:
        x = fs4_sine(44100, 10 ** (-1 / 20))
        m = r128.measure_native(np.column_stack([x, -x]), 44100)
        self.assertAlmostEqual(m.sample_peak_dbfs, -1.0, delta=TOL)
        self.assertAlmostEqual(m.true_peak_dbtp, 2.01, delta=TOL)
        self.assertIsNotNone(m.integrated_lufs)

    def test_silence(self) -> None:
        m = r128.measure_native(np.zeros((48000, 2)), 48000)
        self.assertLessEqual(m.true_peak_dbtp, r128.SILENCE_DB)
        self.assertIsNone(m.integrated_lufs)


class Ebur128ParserTests(unittest.TestCase):
    SAMPLE = """
[Parsed_ebur128_0 @ 0x7f9d14002f40] Summary:

  Integrated loudness:
    I:          -5.5 LUFS
    Threshold: -15.5 LUFS

  Loudness range:
    LRA:        20.0 LU
    Threshold: -25.6 LUFS
    LRA low:   -25.6 LUFS
    LRA high:   -5.6 LUFS

  Sample peak:
    Peak:       -3.0 dBFS

  True peak:
    Peak:        0.4 dBFS
"""

    def test_parse_summary(self) -> None:
        m = r128.parse_ebur128_output(self.SAMPLE)
        assert m is not None
        self.assertEqual(m.integrated_lufs, -5.5)
        self.assertEqual(m.threshold_lufs, -15.5)
        self.assertEqual(m.loudness_range_lu, 20.0)
        self.assertEqual(m.sample_peak_dbfs, -3.0)
        self.assertEqual(m.true_peak_dbtp, 0.4)

    def test_parse_silence_and_garbage(self) -> None:
        silent = self.SAMPLE.replace("-5.5 LUFS", "-70.0 LUFS").replace("0.4 dBFS", "-inf dBFS")
        m = r128.parse_ebur128_output(silent)
        assert m is not None
        self.assertIsNone(m.integrated_lufs)
        self.assertLessEqual(m.true_peak_dbtp, r128.SILENCE_DB)
        self.assertIsNone(r128.parse_ebur128_output("no summary here"))


@unittest.skipIf(sf is None, "soundfile not installed")
class AnalyzeFileTests(unittest.TestCase):
    """End-to-end through analyze_file (stereo native path + ffmpeg cross-check)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="cp_tp_"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        # Pure test tones trip librosa's MIR warnings (tuning/chroma); not under test here.
        self._warn = warnings.catch_warnings()
        self._warn.__enter__()
        warnings.simplefilter("ignore", UserWarning)

    def tearDown(self) -> None:
        self._warn.__exit__(None, None, None)

    def _write(self, name: str, data: np.ndarray, sr: int) -> Path:
        path = self.tmp / name
        sf.write(str(path), data, sr, subtype="FLOAT")
        return path

    def test_stereo_file_per_channel_and_native_rate(self) -> None:
        from nodaw.audio.analyzer import analyze_file

        sr = 48000
        left = sine(sr, 1000.0, 10 ** (-3 / 20))
        right = sine(sr, 1000.0, 10 ** (-12 / 20))
        path = self._write("lr.wav", np.column_stack([left, right]), sr)
        ta = analyze_file(path)
        loud = ta.metrics.loudness
        self.assertAlmostEqual(loud.true_peak_dbtp, -3.0, delta=TOL)
        self.assertAlmostEqual(loud.true_peak_dbtp_per_channel[0], -3.0, delta=TOL)
        self.assertAlmostEqual(loud.true_peak_dbtp_per_channel[1], -12.0, delta=TOL)
        self.assertAlmostEqual(loud.sample_peak_dbfs, -3.0, delta=TOL)
        self.assertAlmostEqual(ta.metrics.peak_dbfs, -3.0, delta=TOL)
        self.assertAlmostEqual(loud.integrated_lufs, -5.5, delta=TOL)
        tp = ta.extra["true_peak"]
        self.assertEqual(tp["oversample"], 4)
        if shutil.which("ffmpeg"):
            self.assertAlmostEqual(tp["ffmpeg_ebur128_true_peak_dbtp"], -3.0, delta=TOL)

    def test_fs4_file_sample_peak_not_resampled(self) -> None:
        from nodaw.audio.analyzer import analyze_file

        x = fs4_sine(48000, 0.5)
        path = self._write("fs4_48k.wav", np.column_stack([x, x]), 48000)
        loud = analyze_file(path).metrics.loudness
        self.assertAlmostEqual(loud.sample_peak_dbfs, -6.02, delta=TOL)
        self.assertAlmostEqual(loud.true_peak_dbtp, -3.01, delta=TOL)


@unittest.skipIf(shutil.which("ffmpeg") is None or sf is None, "ffmpeg not on PATH")
class FfmpegCrossCheckTests(unittest.TestCase):
    def test_native_matches_ffmpeg_ebur128(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            x = fs4_sine(44100, 1.0)
            path = Path(tmp) / "fs4.wav"
            sf.write(str(path), np.column_stack([x, x]), 44100, subtype="FLOAT")
            ff = r128.measure_ebur128_ffmpeg(path)
            assert ff is not None
            native = r128.measure_native(np.column_stack([x, x]), 44100, loudness=False)
            self.assertAlmostEqual(ff.true_peak_dbtp, 3.01, delta=TOL)
            self.assertAlmostEqual(native.true_peak_dbtp, ff.true_peak_dbtp, delta=TOL)
            self.assertAlmostEqual(ff.sample_peak_dbfs, 0.0, delta=TOL)


if __name__ == "__main__":
    unittest.main()
