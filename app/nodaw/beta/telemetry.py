"""
Local usage telemetry for beta testers.

Stores events in SQLite under logs/beta_telemetry.sqlite.
Owner can export stats per email to prove real usage.
Never blocks the app if DB fails.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class TelemetryStore:
    def __init__(self, root: Path, email: str | None = None):
        self.root = Path(root).resolve()
        self.logs = self.root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.db_path = self.logs / "beta_telemetry.sqlite"
        self.email = (email or "anonymous").strip().lower()
        self.session_id = str(uuid.uuid4())
        self._session_start = time.time()
        self._init_db()
        self.event("session_start", {"session_id": self.session_id})

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        try:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        email TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        payload TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_events_email ON events(email);
                    CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind);
                    """
                )
        except Exception:
            pass

    def set_email(self, email: str) -> None:
        self.email = (email or "anonymous").strip().lower()

    def event(self, kind: str, payload: dict[str, Any] | None = None) -> None:
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO events (ts, email, session_id, kind, payload) VALUES (?,?,?,?,?)",
                    (
                        utc_now(),
                        self.email,
                        self.session_id,
                        kind,
                        json.dumps(payload or {}, default=str),
                    ),
                )
        except Exception:
            pass

    def track_analyzed(self, path: str | None, score: int | None = None, fmt: str | None = None) -> None:
        self.event(
            "track_analyzed",
            {
                "path": Path(path).name if path else None,
                "score": score,
                "format": fmt or (Path(path).suffix.lower() if path else None),
            },
        )

    def track_repaired(self, path: str | None, score_before: int | None, score_after: int | None) -> None:
        self.event(
            "track_repaired",
            {
                "path": Path(path).name if path else None,
                "score_before": score_before,
                "score_after": score_after,
            },
        )

    def ab_switch(self, side: str) -> None:
        self.event("ab_switch", {"side": side})

    def session_heartbeat(self) -> None:
        self.event(
            "heartbeat",
            {"uptime_sec": round(time.time() - self._session_start, 1)},
        )

    def session_end(self) -> None:
        self.event(
            "session_end",
            {
                "session_id": self.session_id,
                "duration_sec": round(time.time() - self._session_start, 1),
            },
        )

    def summary_for_email(self, email: str | None = None) -> dict[str, Any]:
        em = (email or self.email).strip().lower()
        out: dict[str, Any] = {
            "email": em,
            "sessions": 0,
            "total_session_sec": 0.0,
            "tracks_analyzed": 0,
            "unique_tracks": 0,
            "repairs": 0,
            "ab_switches": 0,
            "first_seen": None,
            "last_seen": None,
        }
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT ts, kind, payload FROM events WHERE email = ? ORDER BY id",
                    (em,),
                ).fetchall()
            tracks: set[str] = set()
            session_starts: dict[str, float] = {}
            for r in rows:
                ts, kind, payload_s = r["ts"], r["kind"], r["payload"]
                out["last_seen"] = ts
                if out["first_seen"] is None:
                    out["first_seen"] = ts
                try:
                    payload = json.loads(payload_s or "{}")
                except Exception:
                    payload = {}
                if kind == "session_start":
                    out["sessions"] += 1
                    session_starts[payload.get("session_id") or ""] = 0.0
                elif kind == "session_end":
                    out["total_session_sec"] += float(payload.get("duration_sec") or 0)
                elif kind == "track_analyzed":
                    out["tracks_analyzed"] += 1
                    if payload.get("path"):
                        tracks.add(str(payload["path"]))
                elif kind == "track_repaired":
                    out["repairs"] += 1
                elif kind == "ab_switch":
                    out["ab_switches"] += 1
            out["unique_tracks"] = len(tracks)
            out["total_session_sec"] = round(out["total_session_sec"], 1)
        except Exception as exc:
            out["error"] = str(exc)
        return out

    def export_all_summaries(self) -> list[dict[str, Any]]:
        emails: list[str] = []
        try:
            with self._connect() as conn:
                emails = [
                    r[0]
                    for r in conn.execute(
                        "SELECT DISTINCT email FROM events ORDER BY email"
                    ).fetchall()
                ]
        except Exception:
            return []
        return [self.summary_for_email(e) for e in emails]

    def write_owner_report(self, path: Path | None = None) -> Path:
        dest = Path(path) if path else self.logs / "beta_usage_report.json"
        data = {
            "generated_at": utc_now(),
            "testers": self.export_all_summaries(),
        }
        dest.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return dest
