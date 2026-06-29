# -*- coding: utf-8 -*-
"""Engine/repair/engine.py — Adaptive score-chasing repair orchestrator."""

from __future__ import annotations

import tempfile, time, shutil
from pathlib import Path

from ..utils.audio_utils import clamp
from ..utils.file_utils import collect_files, save_json
from ..analyzer.quality_analyzer_lux import analyze
from ..backends import ffmpeg_backend, auto_backend
from . import artifact, noise, enhancer, hybrid, presets


def process_file(input_file: Path, output_dir: Path, args) -> dict:
    start = time.time()
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        source_wav = td / "source_32f.wav"
        if input_file.suffix.lower() != ".wav":
            ffmpeg_backend.convert_to_wav32(input_file, source_wav, args.get("dry_run", False))
        else:
            source_wav = input_file

        before = analyze(source_wav)
        preset_name = args.get("preset", "auto")
        if preset_name == "auto":
            preset_name = presets.choose_from_analysis(before)
        preset_cfg = presets.get(preset_name)

        mode = args.get("mode", preset_cfg["mode"])
        backend = args.get("backend", preset_cfg["backend"])
        strength = clamp(args.get("strength", preset_cfg["strength"]), 0.05, 1.0)
        target_score = args.get("target_score", 90)
        max_passes = args.get("max_passes", 4)

        candidates = []
        strengths = [strength, clamp(strength * 0.75, 0.05, 1), clamp(strength * 1.15, 0.05, 1)]
        strengths = strengths[:max(1, min(max_passes, len(strengths)))]

        best_score = -1
        best_path = None
        best_analysis = None

        for idx, s in enumerate(strengths, 1):
            candidate = td / f"candidate_{idx}.wav"
            print(f"[Engine] Candidate {idx}/{len(strengths)} strength={s:.2f} mode={mode} backend={backend}")

            cmds = []
            if backend == "hybrid":
                result = hybrid.hybrid_repair(source_wav, candidate, s, args.get("dry_run", False))
                cmds = result.get("steps", [])
            elif mode == "artifact":
                cmds = artifact.repair(source_wav, candidate, backend, s, args.get("dry_run", False))
            elif mode == "noise":
                cmds = noise.repair(source_wav, candidate, backend, s, args.get("dry_run", False))
            elif mode == "enhance":
                cmds = enhancer.repair(source_wav, candidate, backend, s, args.get("dry_run", False))
            else:  # full
                st1 = td / f"c{idx}_artifact.wav"
                cmds += artifact.repair(source_wav, st1, backend, s, args.get("dry_run", False))
                st2 = td / f"c{idx}_noise.wav"
                cmds += noise.repair(st1 if st1.exists() else source_wav, st2, backend, s * 0.7, args.get("dry_run", False))
                cmds += enhancer.repair(st2 if st2.exists() else source_wav, candidate, backend, s * 0.5, args.get("dry_run", False))

            after = analyze(candidate) if candidate.exists() and not args.get("dry_run") else before
            score = after.get("score", 0)
            candidates.append({"candidate": idx, "strength": s, "score": score, "commands": cmds})
            print(f"[Engine] Candidate score: {score}/100")

            if score > best_score:
                best_score = score
                best_path = candidate
                best_analysis = after
            if score >= target_score:
                break

        final_ext = args.get("export", "wav")
        final_name = f"{input_file.stem}_REPAIRED_SCORE_{best_score}"
        if args.get("add_suffix"):
            final_name += f"_{args['add_suffix']}"
        final_path = output_dir / f"{final_name}.{final_ext}"
        if best_path and best_path.exists():
            ffmpeg_backend.export_audio(best_path, final_path, final_ext, args.get("dry_run", False))

    after_final = analyze(final_path) if final_path.exists() and not args.get("dry_run") else best_analysis
    final_score = after_final.get("score", 0) if after_final else best_score
    elapsed = round(time.time() - start, 3)

    report = {
        "tool": "NoDAW Engine v3.2",
        "input": str(input_file),
        "output": str(final_path) if final_path.exists() else "",
        "mode": mode,
        "preset": preset_name,
        "backend": backend,
        "before_score": before.get("score", 0),
        "after_score": final_score,
        "improvement": final_score - before.get("score", 0),
        "candidates": candidates,
        "elapsed_sec": elapsed,
    }

    from ..reports.report_generator import generate_html_report_path
    html_path = output_dir / f"{input_file.stem}_repair_report.html"
    report["html_report"] = generate_html_report_path(html_path, after_final or before, f"Repair Report - {input_file.stem}")

    save_json(output_dir / f"{input_file.stem}_repair_report.json", report)
    print(f"[Engine] Final: {final_score}/100 | +{report['improvement']} | {elapsed}s")
    return report
