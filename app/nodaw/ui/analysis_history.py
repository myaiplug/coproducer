"""
Persistent analysis history + favorites for CoProducer.

Stored under reports/analysis_history.json (full enough to restore dashboard).
"""

from __future__ import annotations

import json
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class AnalysisHistory:
    """Append-only-ish history with favorites and delete."""

    MAX_ITEMS = 80

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            self.items = []
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self.items = [x for x in raw if isinstance(x, dict)]
            elif isinstance(raw, dict):
                self.items = list(raw.get("items") or [])
            else:
                self.items = []
        except Exception:
            self.items = []

    def _save(self) -> None:
        try:
            self.path.write_text(
                json.dumps({"version": 1, "items": self.items}, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception:
            pass

    def add_from_report(self, report: dict[str, Any], *, audio_path: str | None = None) -> str:
        """Log a completed analysis; returns entry id."""
        track = report.get("track") if isinstance(report.get("track"), dict) else {}
        audio = track.get("audio") if isinstance(track.get("audio"), dict) else {}
        path = audio_path or audio.get("path") or audio.get("file_path")
        title = Path(str(path)).stem if path else (audio.get("file_name") or "Mix")
        entry_id = str(uuid.uuid4())[:12]
        # Store a compact but restorable snapshot
        data = {
            "score": report.get("score"),
            "rating": report.get("rating"),
            "summary": report.get("summary"),
            "report_type": report.get("report_type") or "single",
            "run_id": report.get("run_id"),
            "findings": report.get("findings"),
            "repairs": report.get("repairs"),
            "track": report.get("track"),
            "tracks": report.get("tracks"),
            "path": path,
            "reference_match": report.get("reference_match"),
            "operations": report.get("operations"),
            # keep extras used by dashboard when present
            "codec_analysis": report.get("codec_analysis"),
            "streaming_analysis": report.get("streaming_analysis"),
        }
        item = {
            "id": entry_id,
            "title": str(title)[:48],
            "score": report.get("score"),
            "date": _utc_now(),
            "path": path,
            "favorite": False,
            "data": data,
        }
        # de-dupe same path at top
        if path:
            self.items = [
                x
                for x in self.items
                if str(x.get("path") or "") != str(path) or x.get("favorite")
            ]
        self.items.insert(0, item)
        self.items = self.items[: self.MAX_ITEMS]
        self._save()
        return entry_id

    def get(self, entry_id: str) -> dict[str, Any] | None:
        for it in self.items:
            if it.get("id") == entry_id:
                return it
        return None

    def delete(self, entry_id: str) -> bool:
        before = len(self.items)
        self.items = [x for x in self.items if x.get("id") != entry_id]
        if len(self.items) != before:
            self._save()
            return True
        return False

    def set_favorite(self, entry_id: str, fav: bool) -> bool:
        for it in self.items:
            if it.get("id") == entry_id:
                it["favorite"] = bool(fav)
                self._save()
                return True
        return False

    def toggle_favorite(self, entry_id: str) -> bool:
        for it in self.items:
            if it.get("id") == entry_id:
                it["favorite"] = not bool(it.get("favorite"))
                self._save()
                return bool(it["favorite"])
        return False

    def favorites(self) -> list[dict[str, Any]]:
        return [x for x in self.items if x.get("favorite")]

    def all_items(self) -> list[dict[str, Any]]:
        return list(self.items)

    def report_from_item(self, item: dict[str, Any]) -> dict[str, Any]:
        """Rebuild a report-like dict for dashboard restore."""
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        report = deepcopy(data) if data else {}
        report.setdefault("score", item.get("score"))
        report.setdefault("report_type", "single")
        # Ensure track.audio.path
        path = item.get("path") or data.get("path")
        if path:
            track = report.get("track") if isinstance(report.get("track"), dict) else {}
            audio = track.get("audio") if isinstance(track.get("audio"), dict) else {}
            audio = dict(audio)
            audio["path"] = path
            audio.setdefault("file_name", Path(str(path)).name)
            track = dict(track)
            track["audio"] = audio
            report["track"] = track
            report["path"] = path
        return report
