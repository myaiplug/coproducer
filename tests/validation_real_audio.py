"""
Final real-audio validation for CoProducer Reference Match.
Run from project root with the validated venv.
"""

import subprocess
import tempfile
import json
from pathlib import Path
import sys

sys.path.insert(0, ".")
from app.nodaw.audio.analyzer import analyze_file, compare_reference

BASE = Path("tests/real_validation_audio")

def create_variant(src: Path, dst: Path, effect: str):
    """Create a processed variant using ffmpeg."""
    cmd = ["ffmpeg", "-y", "-i", str(src)]
    if effect == "quiet":
        cmd += ["-af", "volume=-8dB"]
    elif effect == "loud":
        cmd += ["-af", "volume=+4dB"]
    elif effect == "clipped":
        cmd += ["-af", "alimiter=limit=0.6:attack=5:release=50"]
    cmd += ["-c:a", "pcm_s16le" if dst.suffix == ".wav" else "libmp3lame", str(dst)]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def run_pair(name: str, a: Path, b: Path):
    try:
        ta = analyze_file(a)
        tb = analyze_file(b)
        res = compare_reference(ta, tb)
        score = res["similarity_score"]
        print(f"{name:45} | {score:3d} | {res['plain_english'][:70]}")
        return {"pair": name, "score": score, "details": res}
    except Exception as e:
        print(f"{name:45} | ERR | {e}")
        return {"pair": name, "score": None, "error": str(e)}

def main():
    results = []
    print("=" * 90)
    print("CoProducer Real-Audio Reference Match Validation")
    print("=" * 90)

    # Ensure variants exist
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        full1 = BASE / "beatgohard_full.mp3"
        full2 = BASE / "sadababy_full.mp3"

        # 1. same file vs itself
        results.append(run_pair("same file vs itself (beatgohard)", full1, full1))

        # 2. same song different master (sadababy full vs ref)
        ref2 = BASE / "sadababy_ref.wav"
        results.append(run_pair("same song / different master (sadababy)", full2, ref2))

        # 3. vocal-heavy vs instrumental
        inst = BASE / "beatgohard_inst.wav"
        results.append(run_pair("vocal-heavy vs instrumental (beatgohard)", full1, inst))

        # 4-5. create quiet/loud and clipped variants of sadababy
        quiet = tmp / "sadababy_quiet.wav"
        loud = tmp / "sadababy_loud.wav"
        clipped = tmp / "sadababy_clipped.wav"
        create_variant(full2, quiet, "quiet")
        create_variant(full2, loud, "loud")
        create_variant(full2, clipped, "clipped")

        results.append(run_pair("quiet master vs loud master", quiet, loud))
        results.append(run_pair("clipped master vs clean reference", clipped, full2))

        # 6. same genre different song (use bam and beatgohard - both rap/hiphop-ish)
        bam = BASE / "bam_full.mp3"
        results.append(run_pair("same genre / different song (bam vs beatgohard)", bam, full1))

        # 7. different "genre" proxy - use a stem heavy vs another full (or accept limitation)
        # For demo use vocal vs full of different track
        results.append(run_pair("different content (sadababy full vs beatgohard inst)", full2, inst))

    print("=" * 90)
    print("Expected ranges (approximate for release):")
    print("  identical / same file          : 98-100")
    print("  same song / alt master         : 75-95")
    print("  same genre / different song    : 45-80")
    print("  different genre / content      : < 60")
    print("=" * 90)

    # Write summary
    summary_path = Path("tests/real_validation_results.json")
    summary_path.write_text(json.dumps(results, indent=2))
    print(f"\nDetailed results written to {summary_path}")

if __name__ == "__main__":
    main()