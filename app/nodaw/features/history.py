from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def append_history(history_file: Path, report: dict[str, Any], json_path: Path) -> None:
    history_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "run_id": report["run_id"],
        "generated_at": report["generated_at"],
        "report_type": report["report_type"],
        "title": report["title"],
        "score": report.get("score"),
        "rating": report.get("rating"),
        "json_report": str(json_path),
    }
    with history_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def read_history(history_file: Path) -> list[dict[str, Any]]:
    if not history_file.exists():
        return []
    entries = []
    for line_number, line in enumerate(history_file.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid history entry on line {line_number}: {exc}") from exc
        if isinstance(value, dict):
            entries.append(value)
    return entries

