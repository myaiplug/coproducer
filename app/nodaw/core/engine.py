from __future__ import annotations

import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import APP_NAME, __version__
from ..audio.analyzer import analyze_file, compare_reference  # CoProducer upgrade
from ..audio.ffmpeg import FFmpeg
from ..audio.metrics import MetricsAnalyzer
from ..config import ProjectPaths, load_settings
from ..features.codecs import analyze_codecs
from ..features.collections import album_consistency, track_row
from ..features.diagnostics import diagnostic_checks
from ..features.history import append_history, read_history
from ..features.reference import comparison_rows
from ..features.repairs import (
    build_reference_repairs,
    build_repairs,
    write_repair_launcher,
)
from ..features.streaming import analyze_streaming
from ..reporting.renderers import ReportWriter
from ..utils.files import audio_files, run_id, safe_name, select_audio, utc_now
from .models import TrackAnalysis
from .scoring import (
    codec_recommendations,
    evaluate_track,
    floor_score_after_repair,
    rating,
    streaming_compatibility,
)


class WorkflowRunner:
    def __init__(self, root: Path, logger: logging.Logger, generate_previews: bool = True) -> None:
        self.paths = ProjectPaths(root.resolve())
        self.paths.ensure()
        self.settings = load_settings(self.paths)
        self.logger = logger
        self.generate_previews = generate_previews
        self.ffmpeg = FFmpeg()
        self.metrics = MetricsAnalyzer(self.ffmpeg, self.settings)
        self.writer = ReportWriter(self.paths)
        self._cache: dict[tuple[str, int, int], TrackAnalysis] = {}

    @property
    def extensions(self) -> list[str]:
        return list(self.settings["supported_extensions"])

    def analyze(self, path: Path) -> TrackAnalysis:
        resolved = path.resolve()
        stat = resolved.stat()
        key = (str(resolved).casefold(), stat.st_size, stat.st_mtime_ns)
        if key not in self._cache:
            self.ffmpeg.require()
            self.logger.info("Analyzing %s", resolved.name)
            try:
                # Prefer CoProducer upgraded analyzer (pyloudnorm + librosa + mutagen)
                ta = analyze_file(resolved)
                self._cache[key] = ta
            except Exception as exc:
                self.logger.warning(
                    "Advanced analyzer failed (%s), falling back to legacy metrics", exc
                )
                self._cache[key] = self.metrics.analyze(resolved)
        return self._cache[key]

    def source(self, explicit: Path | None, default_folder: Path) -> Path:
        if explicit:
            resolved = explicit.resolve()
            if not resolved.is_file():
                raise FileNotFoundError(resolved)
            if resolved.suffix.casefold() not in {item.casefold() for item in self.extensions}:
                raise ValueError(f"Unsupported audio extension: {resolved.suffix}")
            return resolved
        return select_audio(default_folder, self.extensions)

    def base_report(self, report_type: str, title: str) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "product": APP_NAME,
            "version": __version__,
            "report_type": report_type,
            "run_id": run_id(),
            "generated_at": utc_now(),
            "title": title,
        }

    def write_report(
        self, report: dict[str, Any], base_name: str, record: bool = True
    ) -> dict[str, Path]:
        destinations = self.writer.write(report, base_name)
        if record:
            append_history(self.paths.history_file, report, destinations["json"])
        self.logger.info("Wrote %s", destinations["html"])
        return destinations

    def single(
        self,
        input_path: Path | None = None,
        *,
        floor_score: int | None = None,
        applied_repair_filters: str | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        source = self.source(input_path, self.paths.song_input)
        track = self.analyze(source)
        score, report_rating, summary, findings = evaluate_track(track, self.settings)
        raw_score = score
        if floor_score is not None:
            score, findings = floor_score_after_repair(
                floor_score,
                score,
                findings=findings,
                applied_filters=applied_repair_filters,
            )
            report_rating = rating(score)
            summary = (
                f"{source.name} scored {score}/100"
                + (f" (raw re-score {raw_score}, floored to pre-repair readiness)."
                   if score != raw_score else ".")
                + " Technical repair readiness is non-decreasing."
            )
        repairs = build_repairs(track, self.settings, self.paths.exports / "repairs")
        report = self.base_report("single", f"Audio quality report - {source.name}")
        report.update(
            {
                "score": score,
                "raw_score": raw_score,
                "score_floor_applied": floor_score is not None and score != raw_score,
                "pre_repair_score_floor": floor_score,
                "rating": report_rating,
                "summary": summary,
                "track": track.to_dict(),
                "findings": [asdict(item) for item in findings],
                "codec_analysis": {"source": codec_recommendations(track), "previews": []},
                "streaming_analysis": {"platforms": streaming_compatibility(track, self.settings)},
                "repairs": [asdict(item) for item in repairs],
            }
        )
        if applied_repair_filters:
            report["applied_repair_filters"] = applied_repair_filters
        if persist:
            self.write_report(report, f"{safe_name(source.stem)}_audio_quality")
        return report

    def reference(
        self, input_path: Path | None = None, reference_path: Path | None = None
    ) -> dict[str, Any]:
        source = self.source(input_path, self.paths.song_input)
        reference_source = self.source(reference_path, self.paths.reference_input)
        user = self.analyze(source)
        reference = self.analyze(reference_source)
        differences = comparison_rows(user, reference)
        penalty = sum(int(item["score_penalty"]) for item in differences)
        score = max(0, 100 - penalty)
        warnings = sum(1 for item in differences if item["severity"] in {"warning", "critical"})
        repairs = build_reference_repairs(user, reference, self.paths.exports / "repairs")
        launcher = (
            self.paths.exports / "repairs" / f"APPLY_{safe_name(source.stem)}_REFERENCE_MATCH.bat"
        )
        write_repair_launcher(repairs, launcher)
        report = self.base_report("reference", f"Reference comparison - {source.name}")

        # CoProducer Reference Match Engine (enhanced)
        try:
            ref_match = compare_reference(user, reference)
        except Exception:
            ref_match = {"similarity_score": score, "plain_english": "Basic comparison performed."}

        report.update(
            {
                "score": score,
                "rating": rating(score),
                "summary": f"{source.name} was compared with {reference_source.name}; {warnings} material difference(s) require review. Similarity: {ref_match.get('similarity_score')}/100.",
                "track": user.to_dict(),
                "reference_track": reference.to_dict(),
                "differences": differences,
                "findings": [],
                "repairs": [asdict(item) for item in repairs],
                "repair_launcher": str(launcher),
                "reference_match": ref_match,  # new traceable match data + recs
            }
        )
        base = f"{safe_name(source.stem)}_VS_{safe_name(reference_source.stem)}_reference"
        self.write_report(report, base)
        return report

    def batch(self, folder: Path | None = None) -> dict[str, Any]:
        source_folder = (folder or self.paths.batch_input).resolve()
        files = audio_files(source_folder, self.extensions, recursive=True)
        if not files:
            raise FileNotFoundError(f"No supported audio files found in {source_folder}")
        rows = [track_row(path, self.analyze(path), self.settings) for path in files]
        average = round(sum(row["score"] for row in rows) / len(rows), 1)
        report = self.base_report("batch", f"Batch analysis - {source_folder.name}")
        report.update(
            {
                "score": int(round(average)),
                "rating": rating(int(round(average))),
                "summary": f"Analyzed {len(rows)} file(s). Average health score: {average}/100.",
                "tracks": rows,
            }
        )
        base = f"{safe_name(source_folder.name)}_batch_analysis"
        self.writer.write_csv(rows, base)
        self.write_report(report, base)
        return report

    def album(self, folder: Path | None = None) -> dict[str, Any]:
        source_folder = (folder or self.paths.album_input).resolve()
        files = audio_files(source_folder, self.extensions, recursive=True)
        if len(files) < 2:
            raise ValueError(f"Album analysis requires at least two audio files in {source_folder}")
        analyzed = [(path, self.analyze(path)) for path in files]
        rows, medians, score = album_consistency(analyzed, self.settings)
        report = self.base_report("album", f"Album consistency - {source_folder.name}")
        report.update(
            {
                "score": score,
                "rating": rating(score),
                "summary": f"Compared {len(rows)} tracks against album medians for loudness, peak, and dynamics.",
                "album_medians": medians,
                "tracks": rows,
            }
        )
        base = f"{safe_name(source_folder.name)}_album_consistency"
        self.writer.write_csv(rows, base)
        self.write_report(report, base)
        return report

    def codecs(self, input_path: Path | None = None) -> dict[str, Any]:
        source = self.source(input_path, self.paths.song_input)
        track = self.analyze(source)
        analysis = analyze_codecs(
            track,
            source,
            self.paths.exports / "previews" / "codecs",
            self.ffmpeg,
            self.settings,
            self.generate_previews,
        )
        score, report_rating, summary, findings = evaluate_track(track, self.settings)
        report = self.base_report("codecs", f"Codec analysis - {source.name}")
        report.update(
            {
                "score": score,
                "rating": report_rating,
                "summary": summary,
                "track": track.to_dict(),
                "findings": [asdict(item) for item in findings],
                "codec_analysis": analysis,
            }
        )
        self.write_report(report, f"{safe_name(source.stem)}_codec_analysis")
        return report

    def streaming(self, input_path: Path | None = None) -> dict[str, Any]:
        source = self.source(input_path, self.paths.song_input)
        track = self.analyze(source)
        analysis = analyze_streaming(
            track,
            source,
            self.paths.exports / "previews" / "streaming",
            self.ffmpeg,
            self.settings,
            self.generate_previews,
        )
        score, report_rating, summary, findings = evaluate_track(track, self.settings)
        report = self.base_report("streaming", f"Streaming readiness - {source.name}")
        report.update(
            {
                "score": score,
                "rating": report_rating,
                "summary": summary,
                "track": track.to_dict(),
                "findings": [asdict(item) for item in findings],
                "streaming_analysis": analysis,
            }
        )
        self.write_report(report, f"{safe_name(source.stem)}_streaming_readiness")
        return report

    def fixes(self, input_path: Path | None = None) -> dict[str, Any]:
        source = self.source(input_path, self.paths.song_input)
        track = self.analyze(source)
        repairs = build_repairs(track, self.settings, self.paths.exports / "repairs")
        launcher = self.paths.exports / "repairs" / f"APPLY_{safe_name(source.stem)}_REPAIR.bat"
        write_repair_launcher(repairs, launcher)
        report = self.base_report("fixes", f"Repair recommendations - {source.name}")
        report.update(
            {
                "summary": f"Generated {len(repairs)} conservative repair plan(s) and an executable Windows repair script.",
                "track": track.to_dict(),
                "repairs": [asdict(item) for item in repairs],
                "repair_launcher": str(launcher),
            }
        )
        self.write_report(report, f"{safe_name(source.stem)}_repairs")
        return report

    def history(self) -> dict[str, Any]:
        entries = read_history(self.paths.history_file)
        scored = [entry["score"] for entry in entries if isinstance(entry.get("score"), int)]
        report = self.base_report("history", "Project analysis history")
        report.update(
            {
                "summary": f"History contains {len(entries)} recorded analysis run(s).",
                "score": round(sum(scored) / len(scored)) if scored else None,
                "rating": "Average recorded score" if scored else "No scored history",
                "entries": list(reversed(entries)),
            }
        )
        self.write_report(report, "project_history", record=False)
        return report

    def export(self) -> dict[str, Any]:
        report = self.base_report("export", "Report export bundle")
        destination = self.paths.exports / f"NoDAW_reports_{report['run_id']}.zip"
        report.update(
            {
                "summary": "Packaged all current HTML, TXT, JSON, CSV, and history reports.",
                "operations": [
                    {
                        "operation": "export report bundle",
                        "status": "prepared",
                        "path": str(destination),
                    }
                ],
            }
        )
        self.write_report(report, f"report_export_{report['run_id']}", record=False)
        archive, count = self.writer.export_bundle(destination)
        report["operations"][0].update(status="completed", files=count, path=str(archive))
        self.write_report(report, f"report_export_{report['run_id']}", record=True)
        return report

    def doctor(self) -> dict[str, Any]:
        checks = diagnostic_checks(self.paths, self.settings, self.ffmpeg)
        passed = sum(1 for check in checks if check["status"] == "pass")
        score = round(100 * passed / len(checks))
        report = self.base_report("doctor", "Dependency and installation diagnostics")
        report.update(
            {
                "score": score,
                "rating": "All checks passed" if passed == len(checks) else "Action required",
                "summary": f"{passed} of {len(checks)} dependency and installation checks passed.",
                "operations": checks,
            }
        )
        self.write_report(report, "system_diagnostics")
        return report

    def complete(
        self,
        input_path: Path | None = None,
        reference_path: Path | None = None,
        folder: Path | None = None,
    ) -> dict[str, Any]:
        operations: list[dict[str, Any]] = []

        def execute(name: str, callback: Any, required: bool = False) -> None:
            try:
                result = callback()
                operations.append(
                    {"operation": name, "status": "completed", "report_type": result["report_type"]}
                )
            except (FileNotFoundError, ValueError) as exc:
                if required:
                    raise
                operations.append({"operation": name, "status": "skipped", "reason": str(exc)})

        execute("single-file analysis", lambda: self.single(input_path), required=True)
        execute("reference comparison", lambda: self.reference(input_path, reference_path))
        execute("batch analysis", lambda: self.batch(folder))
        execute("album consistency", lambda: self.album(folder))
        execute("codec analysis and previews", lambda: self.codecs(input_path), required=True)
        execute(
            "streaming readiness and previews", lambda: self.streaming(input_path), required=True
        )
        execute("repair recommendations", lambda: self.fixes(input_path), required=True)
        execute("history dashboard", self.history, required=True)
        report = self.base_report("complete", "Complete analysis")
        completed = sum(1 for item in operations if item["status"] == "completed")
        report.update(
            {
                "summary": f"Completed {completed} of {len(operations)} available analysis operations.",
                "operations": operations,
            }
        )
        base = f"complete_analysis_{report['run_id']}"
        self.write_report(report, base)
        export_report = self.export()
        report["operations"].append(
            {
                "operation": "report export",
                "status": "completed",
                "path": export_report["operations"][0]["path"],
            }
        )
        self.write_report(report, base, record=False)
        return report
