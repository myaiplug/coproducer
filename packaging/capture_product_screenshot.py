# -*- coding: utf-8 -*-
"""
Capture real CoProducer Desktop screenshots after analyzing a song:
  1) Dashboard (post-analysis)
  2) Reference Match (mix loaded / compared)

Saves into nodaw-web assets for the marketing site.

Usage:
  py -3.11 packaging/capture_product_screenshot.py [mix.wav] [optional_reference.wav]
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path
from time import sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "app"))

WEB_MEDIA = Path(
    r"D:\Projects\Active\NoDAW Audio Analysis"
    r"\NoDAW_Audio_Quality_Analyzer\NoDAW_Audio_Quality_Analyzer_PRO"
    r"\nodaw-web\assets\media"
)


def _pick_audio(arg_idx: int, defaults: list[Path]) -> Path | None:
    if len(sys.argv) > arg_idx:
        p = Path(sys.argv[arg_idx])
        if p.is_file():
            return p
    for c in defaults:
        if c.is_file():
            return c
    return None


def _settle(app, n: int = 25, dt: float = 0.04) -> None:
    for _ in range(n):
        app.processEvents()
        sleep(dt)


def _grab(win, app, path: Path) -> bool:
    from PySide6.QtGui import QGuiApplication

    app.processEvents()
    pix = win.grab()
    if pix.isNull():
        screen = QGuiApplication.primaryScreen()
        geo = win.frameGeometry()
        pix = screen.grabWindow(0, geo.x(), geo.y(), geo.width(), geo.height())
    path.parent.mkdir(parents=True, exist_ok=True)
    ok = pix.save(str(path), "PNG")
    print(f"  saved {path.name} ok={ok} bytes={path.stat().st_size if path.is_file() else 0}")
    return ok and path.is_file()


def main() -> int:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QPixmap

    repairs = ROOT / "exports" / "repairs"
    mix = _pick_audio(
        1,
        [
            repairs / "dtown_repaired.wav",
            repairs / "beatgohard_repaired.wav",
            repairs / "DOPEMAN_repaired.wav",
        ],
    )
    # Prefer a different file as reference when possible
    ref = _pick_audio(
        2,
        [
            repairs / "beatgohard_repaired.wav",
            repairs / "DOPEMAN_repaired.wav",
            repairs / "Gang Gang Type Beat 2025_repaired.wav",
            repairs / "b33zy - idk_repaired.wav",
        ],
    )
    if mix is None:
        print("ERROR: no mix audio found")
        return 2
    if ref is None or ref.resolve() == mix.resolve():
        # fall back to any other wav
        for w in sorted(repairs.glob("*.wav"), key=lambda p: p.stat().st_size, reverse=True):
            if w.resolve() != mix.resolve() and w.stat().st_size > 500_000:
                ref = w
                break
    if ref is None:
        ref = mix  # last resort — still show Reference Match UI

    print("Mix:", mix)
    print("Ref:", ref)

    app = QApplication.instance() or QApplication(sys.argv)
    import CoProducerDesktop as desktop

    win = desktop.CoProducerWindow()
    win.resize(1440, 920)
    win.show()
    win.raise_()
    win.activateWindow()
    _settle(app, 15)

    # ---- Analyze mix → Dashboard ----
    print("Analyzing mix for dashboard...")
    try:
        report = win._analyze_path_direct(mix)
    except Exception:
        traceback.print_exc()
        report = None
    if not report:
        print("ERROR: analysis failed")
        return 3

    print("Score:", report.get("score"), report.get("rating"))
    win.last_result = report
    win._analysis_mode = "analyze"
    try:
        win._on_analysis_done(report)
    except Exception:
        traceback.print_exc()
        try:
            win._refresh_dashboard()
            win._navigate(0)
        except Exception:
            traceback.print_exc()

    win._navigate(0)
    win.resize(1440, 920)
    _settle(app, 30, 0.05)

    WEB_MEDIA.mkdir(parents=True, exist_ok=True)
    dash_path = WEB_MEDIA / "coproducer-screenshot.png"
    ref_path = WEB_MEDIA / "coproducer-reference.png"
    og_path = WEB_MEDIA / "og-cover.png"

    ok1 = _grab(win, app, dash_path)

    # OG from dashboard
    pix = QPixmap(str(dash_path))
    if not pix.isNull():
        og = pix.scaled(
            1200,
            630,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        if og.width() > 1200 or og.height() > 630:
            x = max(0, (og.width() - 1200) // 2)
            y = max(0, (og.height() - 630) // 2)
            og = og.copy(x, y, 1200, 630)
        og.save(str(og_path), "PNG")
        print("  OG:", og_path.name, og_path.stat().st_size)

    # ---- Reference Match ----
    print("Opening Reference Match...")
    try:
        win._set_ref_slot("mix", str(mix), "Your mix")
        win._set_ref_slot("ref", str(ref), "Reference track")
        win._navigate(2)
        _settle(app, 20, 0.04)

        # Run reference compare for real results (if different files)
        if mix.resolve() != ref.resolve():
            print("Running reference compare...")
            try:
                from nodaw.core.engine import WorkflowRunner
                import logging

                logger = logging.getLogger("capture")
                logger.addHandler(logging.NullHandler())
                runner = WorkflowRunner(ROOT, logger, generate_previews=False)
                ref_report = runner.reference(mix, ref)
                win.last_result = ref_report
                win._analysis_mode = "reference"
                win._populate_reference(ref_report)
                try:
                    win._populate_report(ref_report)
                except Exception:
                    pass
                win.main_area.setCurrentIndex(2)
                win._update_nav_styles(2)
            except Exception:
                traceback.print_exc()
                # Still show prepared Reference Match UI with slots loaded
                try:
                    win._prepare_reference_match()
                except Exception:
                    pass
        else:
            try:
                win._prepare_reference_match()
            except Exception:
                pass

        _settle(app, 40, 0.06)
        ok2 = _grab(win, app, ref_path)
    except Exception:
        traceback.print_exc()
        ok2 = False

    # Archive copies
    arch = ROOT / "packaging" / "output"
    arch.mkdir(parents=True, exist_ok=True)
    if dash_path.is_file():
        (arch / "coproducer-screenshot-dashboard.png").write_bytes(dash_path.read_bytes())
    if ref_path.is_file():
        (arch / "coproducer-screenshot-reference.png").write_bytes(ref_path.read_bytes())

    # Drop unused video if present (site no longer needs it)
    demo = WEB_MEDIA / "coproducer-demo.mp4"
    if demo.is_file():
        try:
            demo.unlink()
            print("Removed", demo.name)
        except Exception:
            pass

    win.close()
    app.processEvents()
    print("DONE dashboard=", ok1, "reference=", ok2)
    return 0 if ok1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
