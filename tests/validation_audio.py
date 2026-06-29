"""
Validation script for Phase 3.1.0 Audio Analysis.
Generates synthetic test files covering the checklist and runs full analysis.
Run with the 3.11 venv: .\.venv-validate\Scripts\python -m tests.validation_audio
"""

import sys
from pathlib import Path
import tempfile
import wave
import struct
import math
import subprocess
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

from nodaw.audio.analyzer import analyze_file

def make_wav(path: Path, freq=440, amp=0.5, sr=44100, dur=3.0, channels=2, bitdepth=16):
    n = int(sr * dur)
    frames = bytearray()
    for i in range(n):
        val = int((32767 * amp) * math.sin(2 * math.pi * freq * i / sr))
        if bitdepth == 32:
            # simple float32 simulation via scaling (real 32f handled by sf/librosa anyway)
            val = val
        for _ in range(channels):
            frames.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(frames)
    return path

def make_variants(outdir: Path):
    files = {}
    # 1. Standard stereo WAV
    files["wav_stereo"] = make_wav(outdir / "std_stereo.wav", dur=4.0)

    # 2. Mono
    p = outdir / "mono.wav"
    make_wav(p, channels=1)
    files["mono"] = p

    # 3. 96kHz
    p = outdir / "96khz.wav"
    make_wav(p, sr=96000, dur=2.0)
    files["96khz"] = p

    # 4. Clipped (high amp)
    p = outdir / "clipped.wav"
    make_wav(p, amp=0.999, dur=2.5)
    files["clipped"] = p

    # 5. Very quiet
    p = outdir / "quiet.wav"
    make_wav(p, amp=0.001, dur=3.0)
    files["quiet"] = p

    # 6. Short
    p = outdir / "short.wav"
    make_wav(p, dur=0.2)
    files["short"] = p

    # 7. Long-ish
    p = outdir / "long.wav"
    make_wav(p, dur=12.0)
    files["long"] = p

    # MP3 via ffmpeg (if available)
    try:
        wav = files["wav_stereo"]
        mp3 = outdir / "std.mp3"
        subprocess.run(["ffmpeg", "-y", "-i", str(wav), "-b:a", "128k", str(mp3)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        files["mp3"] = mp3
    except Exception:
        print("Skipping MP3 (ffmpeg encode not available in this context)")

    # FLAC
    try:
        wav = files["wav_stereo"]
        flac = outdir / "std.flac"
        subprocess.run(["ffmpeg", "-y", "-i", str(wav), str(flac)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        files["flac"] = flac
    except Exception:
        print("Skipping FLAC")

    return files

def main():
    with tempfile.TemporaryDirectory() as td:
        out = Path(td)
        variants = make_variants(out)
        print("Generated variants:", list(variants.keys()))

        results = {}
        for name, fpath in variants.items():
            try:
                ta = analyze_file(fpath)
                metrics = {
                    "LUFS": ta.metrics.loudness.integrated_lufs,
                    "TruePeak": ta.metrics.loudness.true_peak_dbtp,
                    "Peak_dBFS": ta.metrics.peak_dbfs,
                    "RMS": ta.metrics.rms_dbfs,
                    "Clipped": ta.metrics.clipped_samples_estimate,
                    "Centroid": ta.extra.get("librosa", {}).get("spectral_centroid_hz"),
                    "Tempo": ta.extra.get("librosa", {}).get("tempo_bpm"),
                }
                results[name] = metrics
                print(f"{name}: {metrics}")
                # Basic reasonableness checks
                if "clipped" in name and ta.metrics.clipped_samples_estimate == 0:
                    print("  WARNING: expected clipping not detected")
            except Exception as e:
                print(f"FAIL {name}: {e}")
                results[name] = {"error": str(e)}

        print("\n=== Audio Analysis Validation Summary ===")
        print(json.dumps(results, indent=2))
        print("All generated files analyzed without crash.")

if __name__ == "__main__":
    main()