#!/usr/bin/env python3
"""Export beta usage stats (sessions, tracks, repairs) for owner review."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from nodaw.beta.telemetry import TelemetryStore  # noqa: E402


def main() -> int:
    store = TelemetryStore(ROOT, "owner")
    path = store.write_owner_report()
    data = json.loads(path.read_text(encoding="utf-8"))
    print(f"Wrote {path}")
    for t in data.get("testers") or []:
        print(
            f"  {t.get('email')}: sessions={t.get('sessions')} "
            f"tracks={t.get('tracks_analyzed')} unique={t.get('unique_tracks')} "
            f"repairs={t.get('repairs')} ab={t.get('ab_switches')} "
            f"time_sec={t.get('total_session_sec')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
