from __future__ import annotations

import csv
import json
import zipfile
from html import escape
from pathlib import Path
from typing import Any, Iterable

from .. import __version__
from ..config import ProjectPaths
from ..utils.files import safe_name, utc_now


def display(value: Any, suffix: str = "") -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, float):
        return f"{value:.2f}{suffix}"
    return f"{value}{suffix}"


def score_class(score: int | None) -> str:
    if score is None:
        return "neutral"
    if score >= 90:
        return "excellent"
    if score >= 75:
        return "good"
    if score >= 60:
        return "caution"
    return "critical"


def waveform_svg(values: list[float]) -> str:
    if not values:
        return "<p class='muted'>Waveform unavailable.</p>"
    width, height, middle = 900, 180, 90
    step = width / max(1, len(values) - 1)
    top = [(index * step, middle - value * 76) for index, value in enumerate(values)]
    bottom = [(index * step, middle + value * 76) for index, value in reversed(list(enumerate(values)))]
    points = " ".join(f"{x:.1f},{y:.1f}" for x, y in top + bottom)
    return (
        f"<svg class='chart waveform' viewBox='0 0 {width} {height}' role='img' "
        "aria-label='Normalized waveform envelope'>"
        f"<line x1='0' y1='{middle}' x2='{width}' y2='{middle}' class='axis'/>"
        f"<polygon points='{points}'/></svg>"
    )


def spectral_chart(values: dict[str, float | None]) -> str:
    valid = [value for value in values.values() if value is not None]
    if not valid:
        return "<p class='muted'>Frequency balance unavailable.</p>"
    floor = min(valid) - 3
    ceiling = max(valid)
    span = max(1.0, ceiling - floor)
    bars = []
    for name, value in values.items():
        height = 0 if value is None else max(3, min(100, ((value - floor) / span) * 100))
        bars.append(
            "<div class='spectral-bar'>"
            f"<span class='bar-value'>{escape(display(value, ' dB'))}</span>"
            f"<i style='height:{height:.1f}%'></i>"
            f"<span>{escape(name.replace('_', ' ').title())}</span>"
            "</div>"
        )
    return "<div class='spectral-chart'>" + "".join(bars) + "</div>"


def track_section(track: dict[str, Any]) -> str:
    audio = track["audio"]
    metrics = track["metrics"]
    loudness = metrics["loudness"]
    cards = [
        ("Integrated LUFS", display(loudness.get("integrated_lufs"), " LUFS")),
        ("True Peak", display(loudness.get("true_peak_dbtp"), " dBTP")),
        ("Dynamic Range", display(metrics.get("dynamic_range_db"), " dB")),
        ("Stereo Width", display(metrics.get("stereo_width_percent"), "%")),
        ("Phase Correlation", display(metrics.get("phase_correlation"))),
        ("Noise Floor", display(metrics.get("noise_floor_dbfs"), " dBFS")),
        ("Clipping Estimate", display(metrics.get("clipped_samples_estimate"))),
        ("Sample Rate", display(audio.get("sample_rate_hz"), " Hz")),
    ]
    card_html = "".join(
        f"<article class='metric'><span>{escape(label)}</span><strong>{escape(value)}</strong></article>"
        for label, value in cards
    )
    technical = [
        ("File", audio.get("file_name")),
        ("Format", audio.get("format_name")),
        ("Codec", audio.get("codec_name")),
        ("Channels", audio.get("channels")),
        ("Bit rate", display(audio.get("bit_rate_bps"), " bps")),
        ("Bit depth", display(audio.get("bit_depth"), "-bit")),
        ("Duration", display(audio.get("duration_seconds"), " seconds")),
        ("RMS", display(metrics.get("rms_dbfs"), " dBFS")),
        ("Crest factor", display(metrics.get("crest_factor"), "x")),
        ("Loudness range", display(loudness.get("loudness_range_lu"), " LU")),
    ]
    rows = "".join(
        f"<tr><th>{escape(str(label))}</th><td>{escape(str(value))}</td></tr>"
        for label, value in technical
    )
    return (
        f"<section class='grid'>{card_html}</section>"
        "<section class='panel'><h2>Waveform envelope</h2>"
        f"{waveform_svg(metrics.get('waveform') or [])}</section>"
        "<section class='panel'><h2>Frequency balance</h2>"
        f"{spectral_chart(metrics.get('spectral_balance_db') or {})}</section>"
        f"<section class='panel'><h2>Technical details</h2><table><tbody>{rows}</tbody></table></section>"
    )


def findings_section(findings: list[dict[str, Any]]) -> str:
    if not findings:
        return ""
    cards = []
    for finding in findings:
        severity = str(finding.get("severity", "notice"))
        cards.append(
            f"<article class='finding {escape(severity)}'>"
            f"<div><span class='badge'>{escape(severity.upper())}</span>"
            f"<h3>{escape(str(finding.get('title', 'Finding')))}</h3></div>"
            f"<p>{escape(str(finding.get('message', '')))}</p>"
            f"<p class='action'><strong>Action:</strong> {escape(str(finding.get('action', '')))}</p>"
            "</article>"
        )
    return "<section class='panel'><h2>Engineering findings</h2><div class='findings'>" + "".join(cards) + "</div></section>"


def table_section(title: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns and not isinstance(row[key], (dict, list)):
                columns.append(key)
    head = "".join(f"<th>{escape(key.replace('_', ' ').title())}</th>" for key in columns)
    body = "".join(
        "<tr>" + "".join(f"<td>{escape(display(row.get(column)))}</td>" for column in columns) + "</tr>"
        for row in rows
    )
    return f"<section class='panel'><h2>{escape(title)}</h2><div class='table-scroll'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div></section>"


def repairs_section(repairs: list[dict[str, Any]]) -> str:
    if not repairs:
        return ""
    content = []
    for repair in repairs:
        content.append(
            "<article class='repair'>"
            f"<h3>{escape(str(repair.get('title')))}</h3>"
            f"<p>{escape(str(repair.get('reason')))}</p>"
            f"<code>{escape(str(repair.get('command')))}</code>"
            f"<p class='muted'>{escape(str(repair.get('caution')))}</p>"
            "</article>"
        )
    return "<section class='panel'><h2>Repair recommendations</h2>" + "".join(content) + "</section>"


def render_html(report: dict[str, Any]) -> str:
    score = report.get("score")
    title = str(report.get("title", "Analysis report"))
    body = []
    if isinstance(report.get("track"), dict):
        body.append(track_section(report["track"]))
    if isinstance(report.get("reference_track"), dict):
        body.append("<section class='panel'><h2>Reference track</h2></section>")
        body.append(track_section(report["reference_track"]))
    body.append(findings_section(report.get("findings") or []))
    body.append(table_section("Reference differences", report.get("differences") or []))
    ref_match = report.get("reference_match") or {}
    if ref_match.get("debug"):
        db = ref_match["debug"]
        debug_rows = []
        if isinstance(db.get("score_breakdown"), dict):
            debug_rows.append(db["score_breakdown"])
        body.append(table_section("Reference Match Debug (deltas, severity, contributions)", debug_rows))
        if db.get("explanation"):
            body.append(f"<section class='panel'><h2>Score Explanation</h2><p>{escape(db['explanation'])}</p></section>")
    body.append(table_section("Track results", report.get("tracks") or []))
    codec = report.get("codec_analysis") or {}
    body.append(table_section("Codec previews", codec.get("previews") or []))
    streaming = report.get("streaming_analysis") or {}
    body.append(table_section("Streaming compatibility", streaming.get("platforms") or []))
    body.append(table_section("Project history", report.get("entries") or []))
    body.append(table_section("Completed operations", report.get("operations") or []))
    body.append(repairs_section(report.get("repairs") or []))
    score_markup = (
        f"<div class='score {score_class(score)}'><strong>{score}</strong><span>{escape(str(report.get('rating', 'Scored report')))}</span></div>"
        if score is not None else
        "<div class='score neutral'><strong>-</strong><span>Informational report</span></div>"
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} | NoDAW PRO</title>
<link rel="stylesheet" href="../../assets/report.css"></head>
<body><main class="wrap">
<header class="hero"><div><p class="eyebrow">NoDAW Audio Quality Analyzer PRO v{__version__}</p>
<h1>{escape(title)}</h1><p class="summary">{escape(str(report.get('summary', '')))}</p></div>{score_markup}</header>
{''.join(body)}
<footer>Run {escape(str(report.get('run_id', '')))} | Generated {escape(str(report.get('generated_at', '')))} | Measurements require listening verification.</footer>
</main></body></html>"""


def render_text(report: dict[str, Any]) -> str:
    lines = [
        f"NoDAW Audio Quality Analyzer PRO v{__version__}",
        report.get("title", "Analysis report"),
        "=" * 72,
        f"Generated: {report.get('generated_at')}",
        f"Run ID: {report.get('run_id')}",
        f"Score: {report.get('score', 'N/A')}",
        f"Rating: {report.get('rating', 'Informational')}",
        "",
        str(report.get("summary", "")),
    ]
    track = report.get("track")
    if isinstance(track, dict):
        audio, metrics = track["audio"], track["metrics"]
        loudness = metrics["loudness"]
        lines.extend([
            "", "MEASUREMENTS",
            f"File: {audio.get('file_name')}",
            f"Codec: {audio.get('codec_name')}",
            f"Sample rate: {display(audio.get('sample_rate_hz'), ' Hz')}",
            f"Integrated LUFS: {display(loudness.get('integrated_lufs'), ' LUFS')}",
            f"True peak: {display(loudness.get('true_peak_dbtp'), ' dBTP')}",
            f"Dynamic range: {display(metrics.get('dynamic_range_db'), ' dB')}",
            f"Stereo width: {display(metrics.get('stereo_width_percent'), '%')}",
            f"Phase correlation: {display(metrics.get('phase_correlation'))}",
            f"Noise floor: {display(metrics.get('noise_floor_dbfs'), ' dBFS')}",
            f"Clipping estimate: {display(metrics.get('clipped_samples_estimate'))}",
        ])
    findings = report.get("findings") or []
    if findings:
        lines.extend(["", "FINDINGS"])
        for finding in findings:
            lines.append(f"[{finding['severity'].upper()}] {finding['title']}: {finding['message']}")
            lines.append(f"Action: {finding['action']}")
    repairs = report.get("repairs") or []
    if repairs:
        lines.extend(["", "REPAIR COMMANDS"])
        for repair in repairs:
            lines.extend([repair["title"], repair["command"], f"Caution: {repair['caution']}"])
    for section, key in [
        ("REFERENCE DIFFERENCES", "differences"),
        ("TRACK RESULTS", "tracks"),
        ("PROJECT HISTORY", "entries"),
        ("COMPLETED OPERATIONS", "operations"),
    ]:
        rows = report.get(key) or []
        if rows:
            lines.extend(["", section])
            lines.extend(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows)

    ref_match = report.get("reference_match") or {}
    if ref_match.get("debug"):
        lines.extend(["", "REFERENCE MATCH DEBUG"])
        lines.append(json.dumps(ref_match["debug"], ensure_ascii=False, sort_keys=True))
    return "\n".join(str(line) for line in lines) + "\n"


class ReportWriter:
    def __init__(self, paths: ProjectPaths) -> None:
        self.paths = paths

    def write(self, report: dict[str, Any], base_name: str) -> dict[str, Path]:
        base = safe_name(base_name)
        destinations = {
            "json": self.paths.reports / "json" / f"{base}.json",
            "txt": self.paths.reports / "txt" / f"{base}.txt",
            "html": self.paths.reports / "html" / f"{base}.html",
        }
        destinations["json"].write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        destinations["txt"].write_text(render_text(report), encoding="utf-8")
        destinations["html"].write_text(render_html(report), encoding="utf-8")
        return destinations

    def write_csv(self, rows: Iterable[dict[str, Any]], base_name: str) -> Path:
        rows = list(rows)
        destination = self.paths.reports / "csv" / f"{safe_name(base_name)}.csv"
        if not rows:
            destination.write_text("", encoding="utf-8")
            return destination
        columns: list[str] = []
        for row in rows:
            for key, value in row.items():
                if key not in columns and not isinstance(value, (list, dict)):
                    columns.append(key)
        with destination.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return destination

    def export_bundle(self, destination: Path) -> tuple[Path, int]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        included = 0
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for folder_name in ("html", "txt", "json", "csv", "history"):
                folder = self.paths.reports / folder_name
                for path in sorted(folder.glob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(self.paths.root))
                        included += 1
            manifest = {
                "product": "NoDAW Audio Quality Analyzer PRO",
                "version": __version__,
                "created_at": utc_now(),
                "files": included,
            }
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        return destination, included

