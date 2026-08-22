from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import APP_NAME, __version__
from .config import ProjectPaths
from .core.engine import WorkflowRunner
from .utils.logging_utils import configure_logging

MODES = (
    "analyze",
    "reference",
    "both",
    "batch",
    "folder",
    "album",
    "codecs",
    "streaming",
    "fixes",
    "repairs",
    "history",
    "export",
    "all",
    "complete",
    "doctor",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nodaw",
        description=f"{APP_NAME} v{__version__}",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--input", type=Path, help="Override the single-song input file.")
    parser.add_argument("--reference", type=Path, help="Override the reference input file.")
    parser.add_argument("--folder", type=Path, help="Override the batch or album input folder.")
    parser.add_argument(
        "--no-previews",
        action="store_true",
        help="Analyze codec/streaming readiness without rendering previews.",
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--list-modes", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.list_modes:
        print("\n".join(MODES))
        return 0
    if not args.mode:
        parser.error("--mode is required unless --list-modes or --version is used.")

    paths = ProjectPaths(args.root.resolve())
    paths.ensure()
    logger = configure_logging(paths.logs, args.verbose)
    try:
        runner = WorkflowRunner(paths.root, logger, generate_previews=not args.no_previews)
        mode = {"folder": "batch", "repairs": "fixes", "complete": "all"}.get(args.mode, args.mode)
        if mode == "analyze":
            report = runner.single(args.input)
        elif mode == "reference":
            report = runner.reference(args.input, args.reference)
        elif mode == "both":
            single = runner.single(args.input)
            reference = runner.reference(args.input, args.reference)
            report = {
                "report_type": "both",
                "summary": "Single-file and reference analyses completed.",
                "runs": [single["run_id"], reference["run_id"]],
            }
        elif mode == "batch":
            report = runner.batch(args.folder)
        elif mode == "album":
            report = runner.album(args.folder)
        elif mode == "codecs":
            report = runner.codecs(args.input)
        elif mode == "streaming":
            report = runner.streaming(args.input)
        elif mode == "fixes":
            report = runner.fixes(args.input)
        elif mode == "history":
            report = runner.history()
        elif mode == "export":
            report = runner.export()
        elif mode == "doctor":
            report = runner.doctor()
        else:
            report = runner.complete(args.input, args.reference, args.folder)
        print(
            json.dumps(
                {
                    "status": "completed",
                    "mode": args.mode,
                    "report_type": report["report_type"],
                    "summary": report.get("summary"),
                },
                ensure_ascii=False,
            )
        )
        if mode == "doctor" and any(item["status"] == "fail" for item in report["operations"]):
            return 2
        return 0
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        logger.error("%s", exc, exc_info=args.verbose)
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except Exception:
        logger.exception("Unexpected failure")
        print(f"[ERROR] Unexpected failure. See {paths.logs / 'nodaw.log'}", file=sys.stderr)
        return 1
    finally:
        for handler in list(logger.handlers):
            handler.flush()
            handler.close()
            logger.removeHandler(handler)


if __name__ == "__main__":
    raise SystemExit(main())
