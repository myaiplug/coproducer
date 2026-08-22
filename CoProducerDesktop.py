# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
CoProducer - AI Production Assistant (Desktop)
Phase 3.2 Premium PySide6 UI

This is a presentation layer only.
All analysis logic lives in the frozen engine.

Design System: app/nodaw/ui/
    theme.py       - color, typography, spacing, elevation tokens
    components.py  - reusable Card, ScoreDisplay, DropZone, Badge, etc.
    animations.py  - fade, score count utilities
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import subprocess

# Hide any inherited console immediately (before heavy imports paint a flash)
if sys.platform == "win32":
    try:
        import ctypes

        _hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if _hwnd:
            ctypes.windll.user32.ShowWindow(_hwnd, 0)  # SW_HIDE
            ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass

from PySide6.QtCore import (
    Qt, QThread, Signal, QObject, QPoint, QSize, QUrl, QTimer, Slot,
)
from PySide6.QtGui import (
    QColor, QFont, QIcon, QPalette, QAction, QGuiApplication, QCursor, QPixmap,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog,
    QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSizePolicy, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

# == Engine / paths (dev + frozen installer) ==================
def _resolve_roots() -> tuple[Path, Path]:
    """
    Returns (project_root, bundle_root).

    - project_root: writable install / project dir (reports, exports, config)
    - bundle_root: packaged assets (icons, brand) — _MEIPASS when frozen
    """
    if getattr(sys, "frozen", False):
        install = Path(sys.executable).resolve().parent
        bundle = Path(getattr(sys, "_MEIPASS", install))
        # Prefer install dir for user data; ensure key folders exist
        for sub in ("reports", "exports", "exports/repairs", "exports/previews", "config", "logs", "input"):
            try:
                (install / sub).mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
        # Bundled FFmpeg on PATH for the process
        ff_bin = install / "runtime" / "ffmpeg" / "bin"
        if ff_bin.is_dir():
            os.environ["PATH"] = str(ff_bin) + os.pathsep + os.environ.get("PATH", "")
        return install, bundle
    root = Path(__file__).resolve().parent
    return root, root


PROJECT_ROOT, BUNDLE_ROOT = _resolve_roots()
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(PROJECT_ROOT / "app"))

try:
    from nodaw.core.engine import WorkflowRunner
    from nodaw import __version__, APP_NAME
    # High-fidelity PortAudio preview (never QMediaPlayer — no WMF resample/stutter)
    from nodaw.audio.pcm_player import DualHiFiPlayer, HiFiPlayer
except Exception as e:
    print("FATAL: Could not import CoProducer engine.", e)
    sys.exit(1)

# == Design System ============================================
from nodaw.ui.theme import (
    Color, Type, Space, Radius, Duration, Easing, Elevation, Layout,
    score_color, score_rating, dialog_stylesheet,
    pick_ui_font, pick_display_font,
    apply_skin, current_skin_id, list_skins, get_skin, DEFAULT_SKIN,
)
from nodaw.ui.components import (
    Card, MetricCard, RecommendationCard, DropZone,
    ScoreDisplay, VerdictBadge, StatusBadge, CollapsibleSection,
    FindingCard, EmptyState, LoadingBar, RecentCard,
    ReferenceTrackCard, DiffCard, PlatformRow,
    CircularScoreRing, BottomMetricsBar, ExportCard, SweepButton,
)
from nodaw.ui.charts import (
    WaveformCanvas, SpectrumCanvas, ChartPanel, SpectralChartPanel, MetricTile,
    HomeWaveformPanel, load_waveform_peaks, load_spectrum_bands,
)
from nodaw.ui.metric_status import value_color, metric_status
from nodaw.ui.animations import fade_in
from nodaw.ui.player import StudioPlayerWindow, resolve_audio_path
from nodaw.ui.ab_studio import ABComparePage, MetricCompareRow
from nodaw.ui.prefs import (
    load_prefs, save_prefs, repair_catalog, build_repair_command,
    DEFAULT_REPAIR_OPTIONS,
)
from nodaw.features.repairs import (
    detect_repair_plan, build_auto_repair_command, run_auto_repair, RepairPlan,
)
from nodaw.beta import TelemetryStore
from nodaw import licensing as app_license
from nodaw.ui.track_meta import TrackMetadataPanel

PRODUCT_NAME = "CoProducer"
TAGLINE = "AI Production Assistant"


def _gui_logger(name: str = "coproducer.desktop"):
    """
    Real logging.Logger for engine/UI (not DummyLogger).
    Supports info/warning/error/debug/exception; writes under PROJECT_ROOT/logs.
    """
    import logging
    from logging.handlers import RotatingFileHandler

    log = logging.getLogger(name)
    if getattr(log, "_coproducer_configured", False):
        return log
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    log.propagate = False
    try:
        log_dir = PROJECT_ROOT / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "coproducer-desktop.log",
            maxBytes=2_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        log.addHandler(fh)
    except Exception:
        log.addHandler(logging.NullHandler())
    log._coproducer_configured = True  # type: ignore[attr-defined]
    return log


def _asset_dir(*parts: str) -> Path:
    """Locate packaged UI assets (works in dev and frozen install)."""
    candidates = [
        BUNDLE_ROOT.joinpath(*parts),
        PROJECT_ROOT.joinpath(*parts),
        BUNDLE_ROOT.joinpath("app", *parts) if parts and parts[0] != "app" else BUNDLE_ROOT.joinpath(*parts),
        PROJECT_ROOT.joinpath("app", *parts) if parts and parts[0] != "app" else PROJECT_ROOT.joinpath(*parts),
    ]
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


ICONS_DIR = _asset_dir("app", "nodaw", "ui", "assets", "icons")
if not ICONS_DIR.is_dir():
    ICONS_DIR = _asset_dir("nodaw", "ui", "assets", "icons")


def _open_path(path: Path | str) -> None:
    """Open a file or folder with the OS shell."""
    p = Path(path)
    if p.exists():
        os.startfile(str(p))


# == Repair Complete Dialog ===================================

class RepairCompleteDialog(QDialog):
    """Repair finished - repaired stats are always applied to dashboard + reports."""

    # action: "done" | "folder" | "studio" | "compare"
    action_chosen: str = "done"

    def __init__(
        self,
        parent: QWidget | None,
        out_path: str | None,
        score_before: Any = None,
        score_after: Any = None,
        accepted: bool = True,
        score_dropped: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Repair Applied")
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self.setFixedWidth(320)
        self._out = Path(out_path) if out_path else None
        self.action_chosen = "done"
        self.accepted = True  # always applied to UI now

        self.setStyleSheet(f"""
            QDialog {{
                background: {Color.DIALOG_BG};
                color: {Color.DIALOG_TEXT};
                border: 1px solid {Color.LINE_HOVER};
                border-radius: {Radius.LG}px;
            }}
            QLabel {{ color: {Color.DIALOG_TEXT}; background: transparent; }}
            QPushButton {{
                background: {Color.ELEVATED};
                color: {Color.DIALOG_TEXT};
                border: 1px solid {Color.LINE_HOVER};
                border-radius: 6px;
                padding: 9px 12px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background: {Color.HOVER};
                border-color: {Color.ACCENT};
                color: {Color.WHITE};
            }}
            QPushButton#Primary {{
                background: {Color.ACCENT};
                color: {Color.WHITE};
                border: none;
            }}
            QPushButton#Primary:hover {{
                background: {Color.ACCENT_SOFT};
                color: {Color.BG};
            }}
        """)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 14)
        lay.setSpacing(10)

        title = QLabel("Repair applied")
        title_color = Color.WARNING if score_dropped else Color.SUCCESS
        title.setStyleSheet(
            f"font-size: {Type.BODY + 1}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {title_color};"
        )
        lay.addWidget(title)

        name = self._out.name if self._out else "Repaired file"
        score_line = (
            f"Score  {score_before if score_before is not None else '-'}  →  "
            f"{score_after if score_after is not None else '-'}"
        )
        note = (
            "\n\nNote: technical score went down vs original - "
            "dashboard/reports still show the repaired file (A/B available)."
            if score_dropped else
            "\n\nDashboard and Reports now show the repaired track stats."
        )
        body = QLabel(f"{name}\n{score_line}{note}")
        body.setWordWrap(True)
        body.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.MUTED};")
        lay.addWidget(body)

        if self._out and self._out.is_file():
            cmp_btn = QPushButton("Open A/B Analysis")
            cmp_btn.setObjectName("Primary")
            cmp_btn.setCursor(Qt.PointingHandCursor)
            cmp_btn.clicked.connect(lambda: self._choose("compare"))
            lay.addWidget(cmp_btn)
            studio_btn = QPushButton("Open Repaired in Player")
            studio_btn.setCursor(Qt.PointingHandCursor)
            studio_btn.clicked.connect(lambda: self._choose("studio"))
            lay.addWidget(studio_btn)
            folder_btn = QPushButton("Open Folder")
            folder_btn.setCursor(Qt.PointingHandCursor)
            folder_btn.clicked.connect(lambda: self._choose("folder"))
            lay.addWidget(folder_btn)

        ok = QPushButton("Done")
        ok.setCursor(Qt.PointingHandCursor)
        ok.clicked.connect(lambda: self._choose("done"))
        lay.addWidget(ok)

    def _choose(self, action: str):
        self.action_chosen = action
        if action == "folder" and self._out:
            _open_path(self._out.parent)
        self.accept()


# == Custom Title Bar =========================================

class TitleBar(QFrame):
    """Frameless chrome: logo secret menu · title · min/max/close."""

    def __init__(self, window: "CoProducerWindow"):
        super().__init__(window)
        self._win = window
        self._drag_pos: QPoint | None = None
        self.setFixedHeight(40)
        self.setStyleSheet(f"""
            QFrame#TitleBar {{
                background: {Color.SURFACE};
                border-bottom: 1px solid {Color.LINE};
            }}
            QLabel {{
                background: transparent;
                color: {Color.TEXT};
            }}
            QToolButton {{
                background: transparent;
                border: none;
                color: {Color.MUTED};
                padding: 4px 10px;
                border-radius: 4px;
                font-size: 12px;
            }}
            QToolButton:hover {{
                background: {Color.HOVER};
                color: {Color.TEXT};
            }}
            QToolButton#CloseBtn:hover {{
                background: {Color.ERROR};
                color: {Color.WHITE};
            }}
            QToolButton#LogoBtn {{
                padding: 2px 6px;
                border-radius: 6px;
            }}
            QToolButton#LogoBtn:hover {{
                background: {Color.with_alpha(Color.ACCENT, 0.15)};
            }}
        """)
        self.setObjectName("TitleBar")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 0, 6, 0)
        lay.setSpacing(6)

        # Logo = secret producer menu
        self.logo_btn = QToolButton()
        self.logo_btn.setObjectName("LogoBtn")
        self.logo_btn.setCursor(Qt.PointingHandCursor)
        self.logo_btn.setToolTip("Producer tools (secret menu)")
        ico = _asset_dir("assets", "icon.ico")
        if not ico.is_file():
            ico = PROJECT_ROOT / "assets" / "icon.ico"
        mark = ICONS_DIR / "nodaw_mark.svg"
        if ico.is_file():
            self.logo_btn.setIcon(QIcon(str(ico)))
            self.logo_btn.setIconSize(QSize(22, 22))
        elif mark.is_file():
            self.logo_btn.setIcon(QIcon(str(mark)))
            self.logo_btn.setIconSize(QSize(22, 22))
        else:
            self.logo_btn.setText("◆")
            self.logo_btn.setStyleSheet(
                self.logo_btn.styleSheet()
                + f"QToolButton#LogoBtn {{ color: {Color.GOLD}; font-size: 16px; font-weight: 700; }}"
            )
        self.logo_btn.clicked.connect(self._show_secret_menu)
        lay.addWidget(self.logo_btn)

        self.title_lbl = QLabel(f"{PRODUCT_NAME} Core Analyzer Report")
        self.title_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: {Type.WEIGHTS['medium']}; "
            f"font-family: {Type.FAMILY}; color: {Color.MUTED}; letter-spacing: 0.4px;"
        )
        lay.addWidget(self.title_lbl)
        lay.addStretch()

        for tip, slot, name in (
            ("Minimize", window.showMinimized, "MinBtn"),
            ("Maximize", self._toggle_max, "MaxBtn"),
            ("Close", window.close, "CloseBtn"),
        ):
            b = QToolButton()
            b.setObjectName(name)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            if name == "MinBtn":
                b.setText("─")
            elif name == "MaxBtn":
                b.setText("□")
                self._max_btn = b
            else:
                b.setText("✕")
            b.clicked.connect(slot)
            lay.addWidget(b)

    def _toggle_max(self):
        if self._win.isMaximized():
            self._win.showNormal()
            self._max_btn.setText("□")
        else:
            self._win.showMaximized()
            self._max_btn.setText("❐")

    def _show_secret_menu(self):
        self._win._open_producer_menu(self.logo_btn)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self._win.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self._win.isMaximized():
                return
            self._win.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._toggle_max()


# == Worker ===================================================

class AnalysisWorker(QObject):
    """Run engine analysis off the main thread."""
    finished = Signal(dict)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, root: Path, mode: str, inputs: dict[str, Any]):
        super().__init__()
        self.root = root
        self.mode = mode
        self.inputs = inputs

    def run(self):
        try:
            self.progress.emit("Loading engine...")
            logger = _gui_logger("coproducer.worker")
            runner = WorkflowRunner(self.root, logger, generate_previews=False)

            self.progress.emit("Analyzing...")
            if self.mode == "analyze":
                path = self.inputs.get("song")
                report = runner.single(Path(path) if path else None)
            elif self.mode == "reference":
                song = self.inputs.get("song")
                ref = self.inputs.get("reference")
                report = runner.reference(Path(song) if song else None, Path(ref) if ref else None)
            elif self.mode == "batch":
                folder = self.inputs.get("folder")
                if not folder:
                    raise ValueError("Select a folder that contains audio files for batch analysis.")
                self.progress.emit(f"Batch analyzing folder…")
                report = runner.batch(Path(folder))
            elif self.mode == "album":
                folder = self.inputs.get("folder")
                if not folder:
                    raise ValueError("Select a folder with at least two tracks for album analysis.")
                report = runner.album(Path(folder))
            elif self.mode == "doctor":
                report = runner.doctor()
            else:
                path = self.inputs.get("song") or self.inputs.get("folder")
                report = runner.single(Path(path) if path else None)

            self.finished.emit(report)
        except Exception as exc:
            self.error.emit(str(exc))


# == Recent Manager ===========================================

class RecentManager:
    """Thin adapter over AnalysisHistory for legacy recent cards (top 8)."""

    def __init__(self, path: Path, history: "AnalysisHistory | None" = None):
        from nodaw.ui.analysis_history import AnalysisHistory

        self.history = history or AnalysisHistory(
            path.parent / "analysis_history.json"
            if path.name == "recent.json"
            else path
        )
        self.path = path
        self.items: list[dict] = []
        self._sync_items()

    def _sync_items(self):
        self.items = [
            {
                "id": it.get("id"),
                "title": it.get("title"),
                "score": it.get("score"),
                "date": (it.get("date") or "")[:16].replace("T", " "),
                "path": it.get("path"),
                "favorite": it.get("favorite"),
                "data": it.get("data"),
            }
            for it in self.history.all_items()[:8]
        ]

    def add(self, item: dict):
        # Prefer full report logging via history when data present
        data = item.get("data") if isinstance(item.get("data"), dict) else {}
        report = dict(data) if data else {}
        report.setdefault("score", item.get("score"))
        if item.get("path") and not report.get("path"):
            report["path"] = item.get("path")
        if report.get("track") or report.get("score") is not None:
            # Full dict restore: pass complete report (includes metrics)
            self.history.add_from_report(report, audio_path=item.get("path"))
        self._sync_items()
        # also write slim recent.json for back-compat
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            slim = [
                {
                    "id": x.get("id"),
                    "title": x.get("title"),
                    "score": x.get("score"),
                    "date": x.get("date"),
                    "path": x.get("path"),
                    "favorite": x.get("favorite"),
                }
                for x in self.items
            ]
            self.path.write_text(json.dumps(slim, indent=2), encoding="utf-8")
        except Exception:
            pass


# == Main Window ==============================================

class CoProducerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{PRODUCT_NAME} - {TAGLINE}")
        # Frameless product chrome (custom title bar)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        )
        # Taller, narrower product window (mockup report aspect - height first)
        self.setMinimumSize(1080, 860)
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            # ~68% width · ~94% height - vertical studio console, not ultra-wide web app
            w = min(max(int(geo.width() * 0.68), 1120), int(geo.width() * 0.82))
            h = min(max(int(geo.height() * 0.94), 900), geo.height() - 8)
            self.resize(w, h)
            self.move(
                geo.x() + (geo.width() - w) // 2,
                geo.y() + max(0, (geo.height() - h) // 2),
            )
        else:
            self.resize(1200, 980)

        ico = _asset_dir("assets", "icon.ico")
        if not ico.is_file():
            ico = PROJECT_ROOT / "assets" / "icon.ico"
        png = _asset_dir("assets", "icon.png")
        if not png.is_file():
            png = PROJECT_ROOT / "assets" / "icon.png"
        if ico.is_file():
            self.setWindowIcon(QIcon(str(ico)))
        elif png.is_file():
            self.setWindowIcon(QIcon(str(png)))

        # Brand skins (NoDAW 5 + Liquid Logic) - persisted
        self._skin_id = self._load_skin_pref()
        apply_skin(self._skin_id)
        self._prefs = load_prefs(PROJECT_ROOT)

        self._apply_theme()
        from nodaw.ui.analysis_history import AnalysisHistory

        self.history = AnalysisHistory(PROJECT_ROOT / "reports" / "analysis_history.json")
        self.recent = RecentManager(PROJECT_ROOT / "reports" / "recent.json", self.history)

        self.setCentralWidget(self._build_ui())

        self.worker: Optional[AnalysisWorker] = None
        self.thread: Optional[QThread] = None
        self.last_result: Optional[dict[str, Any]] = None
        self._analysis_mode: str = "analyze"
        self._ref_mix: Optional[str] = None
        self._ref_track: Optional[str] = None
        self._always_on_top = False
        self._studio_windows: list[StudioPlayerWindow] = []
        self._pre_repair_report: Optional[dict[str, Any]] = None
        self._post_repair_report: Optional[dict[str, Any]] = None
        self._ab_original_path: Optional[str] = None
        self._ab_repaired_path: Optional[str] = None
        self._pending_repair_dialog = False
        self._repair_compare_live = False
        self._last_repair_in: Optional[str] = None
        self._last_repair_out: Optional[str] = None
        self._last_repair_command: Optional[str] = None

        # Dual continuous A/B decks — single PortAudio stream, HQ crossfade switch
        # (no dual QMediaPlayer, no 16-bit/48k downgrade, no drift seeks)
        self._ab_dual = DualHiFiPlayer(self)
        self._player_a = self._ab_dual.deck_a  # proxy API compat
        self._player_b = self._ab_dual.deck_b
        self._player = self._ab_dual  # legacy alias
        self._ab_active = "a"
        self._ab_lookahead_sec = 0.35
        self._ab_sync_timer = QTimer(self)
        self._ab_sync_timer.setInterval(50)
        self._ab_sync_timer.timeout.connect(self._ab_sync_decks)
        self._ab_dual.positionChanged.connect(self._ab_update_position)
        self._ab_dual.playbackStateChanged.connect(self._ab_state_changed)

        # Telemetry (email set after license activation)
        self._telemetry: TelemetryStore | None = None
        st = app_license.get_license_status()
        if st.activated:
            self._telemetry = TelemetryStore(PROJECT_ROOT, st.email)
            self._hb_timer = QTimer(self)
            self._hb_timer.setInterval(60_000)
            self._hb_timer.timeout.connect(lambda: self._telemetry and self._telemetry.session_heartbeat())
            self._hb_timer.start()

        self._show_dashboard()
        self._sync_title_skin_label()

    # == Theme ================================================

    def _apply_theme(self):
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor(Color.BG))
        palette.setColor(QPalette.Base, QColor(Color.SURFACE))
        palette.setColor(QPalette.Text, QColor(Color.TEXT))
        self.setPalette(palette)

        self.setStyleSheet(f"""
            QWidget {{
                color: {Color.TEXT};
                font-family: {Type.FAMILY};
                font-size: {Type.BODY}px;
                letter-spacing: 0.1px;
            }}
            QMainWindow {{ background: {Color.BG}; }}
            QLabel {{
                font-family: {Type.FAMILY};
                selection-background-color: {Color.with_alpha(Color.ACCENT, 0.35)};
            }}
            QScrollArea {{ background: {Color.BG}; border: none; }}
            QScrollBar:vertical {{
                background: transparent; width: 6px; border: none; margin: 4px 2px;
            }}
            QScrollBar::handle:vertical {{
                background: {Color.with_alpha(Color.ACCENT, 0.28)};
                border-radius: 3px; min-height: 48px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: {Color.with_alpha(Color.ACCENT, 0.5)};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
            QPushButton {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                padding: 9px 16px;
                border-radius: {Radius.BUTTON}px;
                font-family: {Type.FAMILY};
                font-weight: {Type.WEIGHTS['medium']};
                font-size: {Type.BODY}px;
                letter-spacing: 0.2px;
                color: {Color.TEXT};
            }}
            QPushButton:hover {{
                background: {Color.HOVER};
                border-color: {Color.with_alpha(Color.ACCENT, 0.55)};
                color: {Color.WHITE};
            }}
            QPushButton#Primary {{
                background: {Color.ACCENT};
                color: {Color.BG};
                border: 1px solid {Color.with_alpha(Color.ACCENT_SOFT, 0.35)};
                font-weight: {Type.WEIGHTS['semibold']};
                letter-spacing: 0.3px;
            }}
            QPushButton#Primary:hover {{
                background: {Color.ACCENT_SOFT};
                color: {Color.BG};
                border-color: {Color.ACCENT};
            }}
            QMessageBox {{
                background-color: {Color.DIALOG_BG};
            }}
            QMessageBox QLabel {{
                color: {Color.DIALOG_TEXT};
                background: transparent;
                min-width: 160px;
                max-width: 280px;
                font-size: {Type.BODY}px;
                font-family: {Type.FAMILY};
            }}
            QMessageBox QPushButton {{
                background-color: {Color.ELEVATED};
                color: {Color.DIALOG_TEXT};
                border: 1px solid {Color.LINE_HOVER};
                border-radius: 6px;
                padding: 8px 14px;
                min-width: 72px;
                font-weight: 600;
                font-family: {Type.FAMILY};
            }}
            QMessageBox QPushButton:hover {{
                background-color: {Color.HOVER};
                border-color: {Color.ACCENT};
                color: {Color.WHITE};
            }}
            QMessageBox QPushButton:default {{
                background-color: {Color.ACCENT};
                color: {Color.WHITE};
                border: none;
            }}
            QMenu {{
                background: {Color.SURFACE};
                color: {Color.TEXT};
                border: 1px solid {Color.LINE_HOVER};
                border-radius: 10px;
                padding: 8px;
                font-family: {Type.FAMILY};
                font-size: {Type.BODY}px;
            }}
            QMenu::item {{
                padding: 9px 32px 9px 14px;
                border-radius: 6px;
            }}
            QMenu::item:selected {{
                background: {Color.with_alpha(Color.ACCENT, 0.2)};
                color: {Color.WHITE};
            }}
            QMenu::separator {{
                height: 1px;
                background: {Color.LINE};
                margin: 5px 10px;
            }}
            QProgressBar {{
                background: {Color.LINE};
                border: none;
                border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {Color.ACCENT};
                border-radius: 3px;
            }}
            /* Premium combobox - no white Windows focus/selection boxes */
            QComboBox {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: 8px;
                padding: 8px 12px;
                color: {Color.TEXT};
                font-family: {Type.FAMILY};
                font-size: {Type.BODY}px;
                font-weight: 500;
                min-height: 20px;
                outline: none;
            }}
            QComboBox:hover {{ border-color: {Color.ACCENT}; }}
            QComboBox:focus {{
                border: 1px solid {Color.ACCENT};
                outline: none;
            }}
            QComboBox:on {{
                border: 1px solid {Color.ACCENT};
                background: {Color.SURFACE};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 28px;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                width: 0; height: 0;
                border-left: 5px solid transparent;
                border-right: 5px solid transparent;
                border-top: 6px solid {Color.MUTED};
                margin-right: 10px;
            }}
            QComboBox QAbstractItemView {{
                background: {Color.SURFACE};
                border: 1px solid {Color.LINE_HOVER};
                border-radius: 8px;
                color: {Color.TEXT};
                padding: 6px;
                outline: none;
                font-family: {Type.FAMILY};
                font-size: {Type.BODY}px;
                selection-background-color: {Color.with_alpha(Color.ACCENT, 0.28)};
                selection-color: {Color.WHITE};
            }}
            QComboBox QAbstractItemView::item {{
                min-height: 28px;
                padding: 6px 10px;
                border: none;
                border-radius: 6px;
                color: {Color.TEXT};
                background: transparent;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background: {Color.with_alpha(Color.ACCENT, 0.18)};
                color: {Color.WHITE};
                border: none;
            }}
            QComboBox QAbstractItemView::item:selected {{
                background: {Color.with_alpha(Color.ACCENT, 0.32)};
                color: {Color.WHITE};
                border: none;
            }}
            QListView {{
                background: {Color.SURFACE};
                color: {Color.TEXT};
                outline: none;
                border: none;
            }}
            QListView::item {{
                color: {Color.TEXT};
                background: transparent;
                border: none;
                border-radius: 6px;
                padding: 6px 10px;
                min-height: 28px;
            }}
            QListView::item:hover {{
                background: {Color.with_alpha(Color.ACCENT, 0.18)};
                color: {Color.WHITE};
            }}
            QListView::item:selected {{
                background: {Color.with_alpha(Color.ACCENT, 0.32)};
                color: {Color.WHITE};
                border: none;
            }}
            QListView::item:selected:active {{
                background: {Color.with_alpha(Color.ACCENT, 0.32)};
                color: {Color.WHITE};
            }}
            QListView::item:selected:!active {{
                background: {Color.with_alpha(Color.ACCENT, 0.22)};
                color: {Color.TEXT};
            }}
            QCheckBox {{
                color: {Color.TEXT};
                font-family: {Type.FAMILY};
                font-size: {Type.CAPTION}px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 16px; height: 16px;
                border-radius: 4px;
                border: 1px solid {Color.LINE_HOVER};
                background: {Color.BG};
            }}
            QCheckBox::indicator:checked {{
                background: {Color.ACCENT};
                border-color: {Color.ACCENT};
            }}
            QToolTip {{
                background-color: {Color.SURFACE};
                color: {Color.TEXT};
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.4)};
                border-radius: 8px;
                padding: 10px 12px;
                font-family: {Type.FAMILY};
                font-size: 12px;
                max-width: 280px;
            }}
        """)

    # == Layout ===============================================

    def _build_ui(self) -> QWidget:
        shell = QWidget()
        shell.setObjectName("AppShell")
        shell.setStyleSheet(f"""
            QWidget#AppShell {{
                background: {Color.BG};
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.18)};
            }}
        """)
        shell_lay = QVBoxLayout(shell)
        shell_lay.setContentsMargins(0, 0, 0, 0)
        shell_lay.setSpacing(0)

        self.title_bar = TitleBar(self)
        shell_lay.addWidget(self.title_bar)

        body = QWidget()
        root_layout = QHBoxLayout(body)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = self._build_sidebar()
        root_layout.addWidget(sidebar, 0)

        self.main_area = QStackedWidget()
        self.dashboard = self._build_dashboard()         # index 0
        self.report_viewer = self._build_report_viewer() # index 1
        self.ref_screen = self._build_reference_screen() # index 2
        # A/B studio lives embedded under Reference Match (not a separate page)
        self.fx_artifact_page = self._build_artifact_hunter_page()   # index 3
        self.fx_bleedfix_page = self._build_bleedfix_page()          # index 4

        self.main_area.addWidget(self.dashboard)
        self.main_area.addWidget(self.report_viewer)
        self.main_area.addWidget(self.ref_screen)
        self.main_area.addWidget(self.fx_artifact_page)
        self.main_area.addWidget(self.fx_bleedfix_page)

        root_layout.addWidget(self.main_area, 1)
        shell_lay.addWidget(body, 1)
        return shell

    # == Sidebar =============================================

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setStyleSheet(f"""
            background: {Color.SURFACE};
            border-right: 1px solid {Color.LINE};
        """)
        # Slim rail: just past CoProducer mark (~180px logo + margins)
        sw = max(192, min(228, int(Layout.SIDEBAR_WIDTH)))
        side.setFixedWidth(sw)
        side.setMinimumWidth(sw)

        outer = QVBoxLayout(side)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setStyleSheet("background: transparent; border: none;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        inner.setMinimumWidth(sw - 12)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(8, Space.MD, 6, Space.MD)
        lay.setSpacing(2)

        # Side menu brand: CoProducer logo
        brand_dir = _asset_dir("app", "nodaw", "ui", "assets", "brand")
        if not brand_dir.is_dir():
            brand_dir = _asset_dir("nodaw", "ui", "assets", "brand")
        brand = QLabel()
        brand.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        brand.setStyleSheet("background: transparent; border: none;")
        brand.setToolTip("CoProducer")
        cp_pix = None
        for cand in (
            brand_dir / "coproducer_logo.png",
            brand_dir / "coproducer_logo.svg",
            brand_dir / "coproducer.png",
        ):
            if cand.is_file():
                p = QPixmap(str(cand))
                if not p.isNull():
                    cp_pix = p
                    break
        if cp_pix is not None:
            # Fit mark to rail width with small side padding
            max_w = max(100, sw - 20)
            scaled = cp_pix.scaled(
                max_w,
                28,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            brand.setPixmap(scaled)
            brand.setFixedHeight(scaled.height() + 2)
        else:
            brand.setText("CoProducer")
            brand.setStyleSheet(
                f"font-size: 16px; font-weight: {Type.WEIGHTS['bold']}; font-family: {Type.DISPLAY}; "
                f"color: {Color.WHITE}; letter-spacing: {Type.TITLE_TRACKING}; background: transparent;"
            )
        lay.addWidget(brand)
        tag = QLabel(Layout.PRODUCT_TAG)
        tag.setStyleSheet(
            f"font-size: 9px; font-weight: 600; color: {Color.ACCENT}; "
            f"letter-spacing: 2px; margin-bottom: 8px; margin-top: 4px; background: transparent;"
        )
        lay.addWidget(tag)

        self._sidebar_sections: dict[str, dict] = {}

        def side_btn(text: str, tip: str, slot, parent_lay: QVBoxLayout) -> QPushButton:
            b = QPushButton(text)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(tip)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setStyleSheet(f"""
                QPushButton {{
                    text-align: left; padding: 8px 6px; font-size: 11px;
                    border: none; background: transparent; border-radius: 6px;
                    color: {Color.MUTED}; font-family: {Type.FAMILY};
                }}
                QPushButton:hover {{
                    background: {Color.with_alpha(Color.ACCENT, 0.12)};
                    color: {Color.TEXT};
                }}
            """)
            b.clicked.connect(slot)
            parent_lay.addWidget(b)
            return b

        def cat(title: str, expanded: bool = True) -> QVBoxLayout:
            """Collapsible sidebar category; returns the body layout for child buttons."""
            from nodaw.ui.icons import IconWidget

            wrap = QFrame()
            wrap.setStyleSheet("background: transparent; border: none;")
            wrap.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(0, 6, 0, 2)
            wl.setSpacing(0)

            hdr = QPushButton()
            hdr.setObjectName("SidebarCat")
            hdr.setCursor(Qt.PointingHandCursor)
            hdr.setFlat(True)
            hdr.setMinimumHeight(32)
            hdr.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            hdr.setStyleSheet(f"""
                QPushButton#SidebarCat {{
                    text-align: left;
                    padding: 0px;
                    background: transparent;
                    border: none;
                    border-radius: 6px;
                    min-height: 32px;
                }}
                QPushButton#SidebarCat:hover {{
                    background: {Color.with_alpha(Color.ACCENT, 0.08)};
                }}
            """)
            hl = QHBoxLayout(hdr)
            hl.setContentsMargins(4, 6, 4, 6)
            hl.setSpacing(8)
            chevron = IconWidget(
                "chevron_down" if expanded else "chevron_right",
                size=12,
                color=Color.MUTED,
            )
            title_lbl = QLabel(title)
            title_lbl.setWordWrap(False)
            title_lbl.setMinimumWidth(140)
            title_lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            title_lbl.setStyleSheet(
                f"font-size: 10px; font-weight: 700; letter-spacing: 0.8px; "
                f"color: {Color.ACCENT_SOFT if expanded else Color.MUTED}; "
                f"background: transparent; border: none;"
            )
            hl.addWidget(chevron, 0, Qt.AlignVCenter)
            hl.addWidget(title_lbl, 1, Qt.AlignVCenter)
            hdr.setToolTip(
                f"Click to collapse {title}" if expanded else f"Click to expand {title}"
            )

            body = QWidget()
            body.setStyleSheet("background: transparent;")
            body.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
            body_lay = QVBoxLayout(body)
            body_lay.setContentsMargins(0, 0, 0, 4)
            body_lay.setSpacing(2)
            body.setVisible(expanded)

            state = {"expanded": expanded}

            def _toggle():
                state["expanded"] = not state["expanded"]
                on = state["expanded"]
                body.setVisible(on)
                chevron.set_name("chevron_down" if on else "chevron_right")
                title_lbl.setStyleSheet(
                    f"font-size: 10px; font-weight: 700; letter-spacing: 0.8px; "
                    f"color: {Color.ACCENT_SOFT if on else Color.MUTED}; "
                    f"background: transparent; border: none;"
                )
                hdr.setToolTip(
                    f"Click to collapse {title}" if on else f"Click to expand {title}"
                )

            hdr.clicked.connect(_toggle)
            wl.addWidget(hdr)
            wl.addWidget(body)
            lay.addWidget(wrap)
            self._sidebar_sections[title] = {
                "header": hdr, "body": body, "layout": body_lay, "toggle": _toggle,
            }
            return body_lay

        # NAVIGATE
        nav_lay = cat("NAVIGATE", expanded=True)
        self._nav_btns = {}
        for text, index, tip in [
            ("Dashboard", 0, "Home - score, metrics, waveform, convert & repair."),
            ("Reference Match", 2, "Compare tracks + full A/B meters, spectra, spectrograms."),
            ("Reports", 1, "Full technical report with export options."),
            ("Artifact Hunter", 3, "Scan any audio file for clicks, DC offset and dropout edges."),
            ("Bleedfix", 4, "Duck mic bleed / room wash with an expander gate."),
        ]:
            b = side_btn(text, tip, lambda checked=False, i=index: self._navigate(i), nav_lay)
            self._nav_btns[index] = b

        # HISTORY (logged analyses — restore stats / delete / favorite)
        hist_lay = cat("HISTORY", expanded=True)
        self._history_list_host = QWidget()
        self._history_list_host.setStyleSheet("background: transparent;")
        self._history_list_layout = QVBoxLayout(self._history_list_host)
        self._history_list_layout.setContentsMargins(0, 0, 0, 0)
        self._history_list_layout.setSpacing(4)
        hist_lay.addWidget(self._history_list_host)
        self._rebuild_history_sidebar()

        # LIBRARY
        lib_lay = cat("LIBRARY", expanded=True)
        side_btn("Repairs folder", "Open folder where repaired WAVs are saved.", self._menu_open_repairs, lib_lay)
        side_btn("Reports folder", "HTML / JSON / TXT analysis reports.", self._menu_open_reports, lib_lay)
        side_btn("Logs folder", "Application and engine logs.", self._menu_open_logs, lib_lay)
        side_btn("Project root", "Open the CoProducer install / project folder.", lambda: _open_path(PROJECT_ROOT), lib_lay)
        side_btn("Latest HTML report", "Open the most recent interactive HTML report.", self._open_latest_html, lib_lay)
        side_btn("Reveal last mix", "Show the last analyzed file in Explorer.", self._menu_reveal_last_mix, lib_lay)

        # TOOLS
        tools_lay = cat("TOOLS", expanded=True)
        side_btn("Studio Player…", "Inspect, play, scrub, trim, and convert any audio file.", self._menu_open_studio, tools_lay)
        side_btn("Quick convert…", "Convert the current or selected mix to another format.", self._home_convert, tools_lay)
        side_btn("Change output folder…", "Choose where repaired/converted files are saved (remembered).", self._pick_output_folder, tools_lay)

        # SYSTEM
        sys_lay = cat("SYSTEM", expanded=False)
        side_btn("Run System Doctor", "Check FFmpeg, Python, encoders, and install health.", self._run_doctor, sys_lay)
        side_btn("FFmpeg status", "Show installed FFmpeg / FFprobe version lines.", self._menu_ffmpeg_status, sys_lay)
        side_btn("Always on top", "Pin the window above other apps.", lambda: self._menu_toggle_on_top(not self._always_on_top), sys_lay)
        side_btn("Reset window size", "Restore the tall product window geometry.", self._menu_reset_geometry, sys_lay)

        # APPEARANCE
        app_lay = cat("APPEARANCE", expanded=False)
        skin_lbl = QLabel("Brand skin")
        skin_lbl.setStyleSheet(f"font-size: 11px; color: {Color.MUTED}; background: transparent;")
        skin_lbl.setToolTip("NoDAW / MyAIPlug brand skins - each stays distinct.")
        app_lay.addWidget(skin_lbl)
        self.skin_combo = QComboBox()
        self.skin_combo.setToolTip("Switch the full product color skin. Choice is saved.")
        self.skin_combo.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Force themed popup list (avoids white Windows selection frame)
        self.skin_combo.setStyleSheet(self._combo_popup_stylesheet())
        view = self.skin_combo.view()
        view.setStyleSheet(self._combo_popup_stylesheet())
        view.setFrameShape(QFrame.Shape.NoFrame)
        # Palette so Windows doesn't paint white selection/focus rectangles
        pal = view.palette()
        pal.setColor(QPalette.ColorRole.Base, QColor(Color.SURFACE))
        pal.setColor(QPalette.ColorRole.Text, QColor(Color.TEXT))
        pal.setColor(QPalette.ColorRole.Highlight, QColor(Color.ACCENT))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor(Color.WHITE))
        pal.setColor(QPalette.ColorRole.Window, QColor(Color.SURFACE))
        pal.setColor(QPalette.ColorRole.Button, QColor(Color.SURFACE))
        view.setPalette(pal)
        self.skin_combo.setPalette(pal)
        for skin in list_skins():
            self.skin_combo.addItem(skin["name"], skin["id"])
            idx = self.skin_combo.count() - 1
            self.skin_combo.setItemData(idx, skin.get("blurb", ""), Qt.ItemDataRole.ToolTipRole)
        # select current
        cur = current_skin_id()
        for i in range(self.skin_combo.count()):
            if self.skin_combo.itemData(i) == cur:
                self.skin_combo.setCurrentIndex(i)
                break
        self.skin_combo.currentIndexChanged.connect(self._on_skin_combo)
        app_lay.addWidget(self.skin_combo)

        lay.addStretch()
        about = side_btn(
            f"About v{__version__}",
            "Product version and offline promise.",
            self._menu_about,
            lay,
        )

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        return side

    def _combo_popup_stylesheet(self) -> str:
        """Dark popup list styles - kills white focus boxes on brand skins."""
        return f"""
            QComboBox {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: 8px;
                padding: 8px 12px;
                color: {Color.TEXT};
                font-family: {Type.FAMILY};
                font-size: {Type.BODY}px;
                font-weight: 500;
                outline: none;
            }}
            QComboBox:hover, QComboBox:focus, QComboBox:on {{
                border: 1px solid {Color.ACCENT};
                outline: none;
                background: {Color.SURFACE};
            }}
            QComboBox QAbstractItemView {{
                background: {Color.SURFACE};
                border: 1px solid {Color.LINE_HOVER};
                color: {Color.TEXT};
                outline: none;
                selection-background-color: {Color.with_alpha(Color.ACCENT, 0.35)};
                selection-color: {Color.WHITE};
                padding: 4px;
            }}
            QListView {{
                background: {Color.SURFACE};
                color: {Color.TEXT};
                outline: none;
                border: none;
            }}
            QListView::item {{
                color: {Color.TEXT};
                background: {Color.SURFACE};
                border: none;
                border-radius: 6px;
                padding: 8px 10px;
                min-height: 26px;
            }}
            QListView::item:hover {{
                background: {Color.with_alpha(Color.ACCENT, 0.20)};
                color: {Color.WHITE};
                border: none;
            }}
            QListView::item:selected,
            QListView::item:selected:active,
            QListView::item:selected:!active {{
                background: {Color.with_alpha(Color.ACCENT, 0.35)};
                color: {Color.WHITE};
                border: none;
            }}
        """

    def _on_skin_combo(self, index: int):
        sid = self.skin_combo.itemData(index)
        if sid:
            self._set_skin(str(sid))

    def _nav_button(self, text: str, index: int, key=None) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                text-align: left; padding: 8px 6px; font-size: 11px;
                border: none; background: transparent; border-radius: 6px;
                color: {Color.MUTED};
            }}
            QPushButton:hover {{ background: {Color.ELEVATED}; color: {Color.TEXT}; }}
        """)
        b.clicked.connect(lambda: self._navigate(index))
        return b

    def _update_nav_styles(self, index: int):
        active_style = f"""
            QPushButton {{
                text-align: left; padding: 8px 6px; font-size: 11px;
                border: none; background: {Color.with_alpha(Color.ACCENT, 0.18)};
                border-radius: 6px; color: {Color.WHITE};
                font-weight: {Type.WEIGHTS['semibold']}; font-family: {Type.FAMILY};
            }}
            QPushButton:hover {{ background: {Color.with_alpha(Color.ACCENT, 0.25)}; color: {Color.WHITE}; }}
        """
        inactive_style = f"""
            QPushButton {{
                text-align: left; padding: 8px 6px; font-size: 11px;
                border: none; background: transparent; border-radius: 6px;
                color: {Color.MUTED}; font-family: {Type.FAMILY};
            }}
            QPushButton:hover {{
                background: {Color.with_alpha(Color.ACCENT, 0.12)}; color: {Color.TEXT};
            }}
        """
        for idx, btn in self._nav_btns.items():
            if isinstance(idx, int):
                btn.setStyleSheet(active_style if idx == index else inactive_style)

    def _navigate(self, index: int):
        self.main_area.setCurrentIndex(index)
        self._update_nav_styles(index)
        # Reference Match: auto-load last mix / original vs repaired (no auto-run loop)
        if index == 2:
            self._prepare_reference_match()

    # == Artifact Hunter / Bleedfix pages ======================

    def _build_artifact_hunter_page(self) -> QWidget:
        from nodaw.ui.fx_tools import ArtifactHunterTool

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(Space.XL, Space.MD, Space.XL, Space.LG)
        lay.setSpacing(Space.MD)
        self.artifact_hunter = ArtifactHunterTool(w)
        lay.addWidget(self.artifact_hunter)
        lay.addStretch()
        return w

    def _build_bleedfix_page(self) -> QWidget:
        from nodaw.ui.fx_tools import BleedfixTool

        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(Space.XL, Space.MD, Space.XL, Space.LG)
        lay.setSpacing(Space.MD)
        self.bleedfix_tool = BleedfixTool(w)
        lay.addWidget(self.bleedfix_tool)
        lay.addStretch()
        return w

    # == Dashboard (mockup-aligned Home) =======================

    def _build_dashboard(self) -> QWidget:
        """Premium Home: drop zone, score-at-a-glance, scorecards, recs, recent."""
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(Space.XL, Space.MD, Space.XL, Space.LG)
        lay.setSpacing(Space.MD)

        # Loading
        self.loading_bar = LoadingBar()
        lay.addWidget(self.loading_bar)
        self.loading_label = QLabel("")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent;"
        )
        self.loading_label.hide()
        lay.addWidget(self.loading_label)

        # ---- Brand hero: centered NoDAW Labs (CoProducer lives in sidebar) ----
        self.home_hero = QWidget()
        self.home_hero.setStyleSheet("background: transparent;")
        self.home_hero.setMinimumHeight(64)
        hero = QHBoxLayout(self.home_hero)
        hero.setContentsMargins(4, 6, 4, 10)
        hero.setSpacing(0)

        brand_dir = _asset_dir("app", "nodaw", "ui", "assets", "brand")
        if not brand_dir.is_dir():
            brand_dir = _asset_dir("nodaw", "ui", "assets", "brand")
        try:
            brand_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

        self.hero_nodaw = QLabel()
        self.hero_nodaw.setAlignment(Qt.AlignCenter)
        self.hero_nodaw.setStyleSheet("background: transparent; border: none;")
        self.hero_nodaw.setToolTip("NoDAW Labs")
        nd_pix = None
        for cand in (
            brand_dir / "nodaw_logo.png",
            brand_dir / "nodaw_logo_prev.png",
            brand_dir / "nodaw_logo.svg",
            brand_dir / "nodaw.png",
            brand_dir / "NoDAW.png",
            ICONS_DIR / "nodaw_mark.svg",
        ):
            if cand.is_file():
                p = QPixmap(str(cand))
                if not p.isNull():
                    nd_pix = p
                    break
        if nd_pix is not None:
            max_w, max_h = 320, 52
            scaled = nd_pix.scaled(
                max_w,
                max_h,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.hero_nodaw.setPixmap(scaled)
            self.hero_nodaw.setFixedHeight(scaled.height() + 6)
            self.hero_nodaw.setMaximumWidth(max_w + 8)
        else:
            self.hero_nodaw.setText("NoDAW Labs")
            self.hero_nodaw.setStyleSheet(
                f"font-size: 20px; font-weight: 600; font-family: {Type.DISPLAY}; "
                f"color: {Color.TEXT}; letter-spacing: -0.3px; "
                f"background: transparent; border: none;"
            )
        hero.addStretch(1)
        hero.addWidget(self.hero_nodaw, 0, Qt.AlignVCenter | Qt.AlignHCenter)
        hero.addStretch(1)

        lay.addWidget(self.home_hero)

        # ---- Drop zone: motion glass + feature chips (no separate empty-state banner) ----
        self.home_drop = DropZone(
            "Import mix to analyze",
            "WAV · MP3 · FLAC · M4A · AIFF  ·  click or drop",
            chips=[
                "Score at a Glance",
                "Technical Scorecards",
                "Reference Match",
                "100% Local",
            ],
        )
        self.home_drop.setMinimumHeight(158)
        self.home_drop.setMaximumHeight(188)
        self.home_drop.filesDropped.connect(lambda fs: self._handle_drop(fs, "analyze"))
        lay.addWidget(self.home_drop)
        # Keep attribute so older show/hide calls are no-ops
        self.empty_state = QWidget()
        self.empty_state.hide()

        # ---- Results shell (always visible; placeholders until analysis) ----
        self.home_results = QWidget()
        self.home_results.setStyleSheet("background: transparent;")
        # Visible on first paint with "-" - filled after analysis
        self.home_results.show()
        hr = QVBoxLayout(self.home_results)
        hr.setContentsMargins(0, 0, 0, 0)
        hr.setSpacing(Space.MD)

        # Score + Repair card (directly under Import Mix)
        score_header = QFrame()
        score_header.setObjectName("ScoreRepairCard")
        score_header.setStyleSheet(f"""
            QFrame#ScoreRepairCard {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        card_lay = QVBoxLayout(score_header)
        card_lay.setContentsMargins(Space.XL, Space.MD, Space.XL, Space.LG)
        card_lay.setSpacing(Space.MD)

        # ---- Score row: identity + auto-repair left · mix score ring right ----
        sh = QHBoxLayout()
        sh.setSpacing(Space.XL)
        sh.setContentsMargins(0, 0, 0, 0)

        left_col = QVBoxLayout()
        left_col.setSpacing(6)
        left_col.setContentsMargins(0, Space.SM, 0, 0)
        self.home_track_name = QLabel("AWAITING MIX")
        self.home_track_name.setWordWrap(True)
        self.home_track_name.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.home_track_name.setStyleSheet(
            f"font-size: {Type.H2}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"font-family: {Type.DISPLAY}; color: {Color.TEXT}; background: transparent;"
        )
        left_col.addWidget(self.home_track_name)
        self.home_verdict = QLabel("Drop or browse a file - metrics fill when analysis completes.")
        self.home_verdict.setWordWrap(True)
        self.home_verdict.setMinimumHeight(36)
        self.home_verdict.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.home_verdict.setStyleSheet(
            f"font-size: {Type.BODY}px; color: {Color.MUTED}; background: transparent;"
        )
        left_col.addWidget(self.home_verdict)
        self.home_badge = QLabel("  STANDBY  ")
        self.home_badge.setWordWrap(False)
        self.home_badge.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.home_badge.setStyleSheet(f"""
            background: {Color.with_alpha(Color.MUTED, 0.12)};
            color: {Color.MUTED};
            border: 1px solid {Color.with_alpha(Color.MUTED, 0.25)};
            border-radius: {Radius.PILL}px;
            padding: 6px 14px;
            font-size: 11px; font-weight: {Type.WEIGHTS['semibold']};
        """)
        self.home_badge.show()
        left_col.addWidget(self.home_badge, 0, Qt.AlignLeft)

        # Auto-detected repair — tight under the status badge (score ring stays right)
        left_col.addSpacing(8)
        auto_hdr = QHBoxLayout()
        auto_hdr.setSpacing(Space.SM)
        auto_lbl = QLabel("Auto-detected repair")
        auto_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 600; color: {Color.ACCENT_SOFT}; "
            f"background: transparent;"
        )
        auto_lbl.setToolTip(
            "Only technical gates this mix fails. No soft compress, EQ boosts, or extras."
        )
        auto_hdr.addWidget(auto_lbl)
        auto_hdr.addStretch()
        guard = QLabel("Auto-detect · stats auto-apply")
        guard.setStyleSheet(
            f"font-size: 9px; font-weight: 600; color: {Color.SUCCESS}; "
            f"background: {Color.with_alpha(Color.SUCCESS, 0.12)}; "
            f"border-radius: 6px; padding: 2px 7px;"
        )
        auto_hdr.addWidget(guard)
        left_col.addLayout(auto_hdr)

        self.auto_repair_body = QLabel("Analyze a mix to detect what needs repair.")
        self.auto_repair_body.setWordWrap(True)
        self.auto_repair_body.setStyleSheet(
            f"font-size: 12px; color: {Color.TEXT}; background: transparent; line-height: 1.35;"
        )
        left_col.addWidget(self.auto_repair_body)
        self.auto_repair_caution = QLabel("")
        self.auto_repair_caution.setWordWrap(True)
        self.auto_repair_caution.setStyleSheet(
            f"font-size: 11px; color: {Color.WARNING}; background: transparent;"
        )
        self.auto_repair_caution.hide()
        left_col.addWidget(self.auto_repair_caution)
        self._repair_checks: dict[str, QCheckBox] = {}
        self._auto_repair_plan: RepairPlan | None = None

        run_row = QHBoxLayout()
        run_row.setContentsMargins(0, 2, 0, 0)
        self.run_repair_btn = SweepButton("Run Auto Repair")
        self.run_repair_btn.setMinimumWidth(160)
        self.run_repair_btn.setMaximumWidth(240)
        self.run_repair_btn.setEnabled(False)
        self.run_repair_btn.setCursor(Qt.CursorShape.ForbiddenCursor)
        self.run_repair_btn.setToolTip("Upload and analyze a mix first.")
        self.run_repair_btn.clicked.connect(self._run_custom_repair)
        run_row.addWidget(self.run_repair_btn)
        run_row.addStretch()
        left_col.addLayout(run_row)
        self.repair_disabled_hint = QLabel("")
        self.repair_disabled_hint.setWordWrap(True)
        self.repair_disabled_hint.setStyleSheet(
            f"font-size: 11px; color: {Color.MUTED}; background: transparent;"
        )
        self.repair_disabled_hint.hide()
        left_col.addWidget(self.repair_disabled_hint)
        left_col.addStretch(1)
        sh.addLayout(left_col, 1)

        # Mix score ring — stays top-right
        ring_col = QWidget()
        ring_col.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        ring_wrap = QVBoxLayout(ring_col)
        ring_wrap.setContentsMargins(0, 0, 0, 0)
        ring_wrap.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        ring_wrap.setSpacing(Space.XS)
        self.home_score_label = QLabel(Layout.SCORE_LABEL)
        self.home_score_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.home_score_label.setWordWrap(True)
        self.home_score_label.setMinimumWidth(180)
        self.home_score_label.setStyleSheet(
            f"font-size: 10px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.ACCENT}; letter-spacing: 0.8px; background: transparent;"
        )
        ring_wrap.addWidget(self.home_score_label, 0, Qt.AlignHCenter)
        _rs = int(Layout.RING_SIZE)
        self.score_ring = CircularScoreRing(size=_rs)
        ring_wrap.addWidget(self.score_ring, 0, Qt.AlignHCenter)
        ring_col.setMinimumWidth(max(200, _rs + 20))
        sh.addWidget(ring_col, 0, Qt.AlignTop)
        card_lay.addLayout(sh)

        # Divider
        div = QFrame()
        div.setFixedHeight(1)
        div.setStyleSheet(f"background: {Color.LINE}; border: none;")
        card_lay.addWidget(div)

        # ---- Engine checklist (full width, under score row) ----
        eng_lbl = QLabel("Engine checklist")
        eng_lbl.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {Color.ACCENT_SOFT};")
        card_lay.addWidget(eng_lbl)
        self.recs_scroll = QScrollArea()
        self.recs_scroll.setWidgetResizable(True)
        self.recs_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.recs_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recs_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.recs_scroll.setMinimumHeight(320)
        self.recs_scroll.setMaximumHeight(560)
        self.recs_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.recs_scroll.setStyleSheet(f"""
            QScrollArea {{
                background: transparent;
                border: 1px solid {Color.LINE};
                border-radius: {Radius.LG}px;
            }}
            QScrollBar:vertical {{
                background: {Color.BG};
                width: 8px;
                margin: 2px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical {{
                background: {Color.with_alpha(Color.ACCENT, 0.35)};
                border-radius: 4px;
                min-height: 24px;
            }}
        """)
        self.recs_card = RecommendationCard()
        self.recs_card.setStyleSheet(f"""
            QFrame {{
                background: {Color.SURFACE};
                border: none;
                border-radius: {Radius.LG}px;
            }}
        """)
        self.recs_card.setMinimumHeight(280)
        self.recs_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.MinimumExpanding)
        self.recs_scroll.setWidget(self.recs_card)
        card_lay.addWidget(self.recs_scroll, 1)

        # ---- Release metadata ----
        self.track_meta = TrackMetadataPanel()
        self.track_meta.saved.connect(self._on_metadata_saved)
        card_lay.addWidget(self.track_meta)
        hr.addWidget(score_header)

        # Primary scorecards - equal MetricTiles (color-banded values)
        self.home_metric_tiles: dict[str, MetricTile] = {}
        self.home_scorecards_wrap = QWidget()
        self.home_scorecards_wrap.setStyleSheet("background: transparent;")
        sc_row = QHBoxLayout(self.home_scorecards_wrap)
        sc_row.setContentsMargins(0, 0, 0, 0)
        sc_row.setSpacing(Space.SM)
        primary_metrics = [
            ("lufs", "LOUDNESS", "LUFS"),
            ("tp", "TRUE PEAK", "dBTP"),
            ("lra", "LRA", "LU"),
            ("peak", "PEAK", "dBFS"),
            ("rms", "RMS", "dBFS"),
            ("crest", "CREST", "×"),
        ]
        for key, label, unit in primary_metrics:
            tile = MetricTile(key, label, unit)
            self.home_metric_tiles[key] = tile
            sc_row.addWidget(tile, 1)
        # legacy alias for any code expecting home_scorecards labels
        self.home_scorecards = {k: t.val for k, t in self.home_metric_tiles.items()}
        hr.addWidget(self.home_scorecards_wrap)

        # Extended metrics (librosa / technical - same color logic)
        self.home_extra_tiles: dict[str, MetricTile] = {}
        self.home_extra_wrap = QWidget()
        self.home_extra_wrap.setStyleSheet("background: transparent;")
        ex_row = QHBoxLayout(self.home_extra_wrap)
        ex_row.setContentsMargins(0, 0, 0, 0)
        ex_row.setSpacing(Space.SM)
        extra_metrics = [
            ("dr", "DYN RANGE", "dB"),
            ("width", "STEREO WIDTH", "%"),
            ("phase", "PHASE", "corr"),
            ("noise", "NOISE FLOOR", "dBFS"),
            ("tempo", "TEMPO", "BPM"),
            ("centroid", "BRIGHTNESS", "Hz"),
            ("clip", "CLIPPING", "samples"),
            ("silence", "SILENCE", "ratio"),
        ]
        for key, label, unit in extra_metrics:
            tile = MetricTile(key, label, unit)
            self.home_extra_tiles[key] = tile
            ex_row.addWidget(tile, 1)
        hr.addWidget(self.home_extra_wrap)
        if not Layout.SHOW_EXTRA_METRICS:
            self.home_extra_wrap.hide()

        # HQ Waveform (studio transport) + Spectrum
        self.home_charts_wrap = QWidget()
        self.home_charts_wrap.setStyleSheet("background: transparent;")
        charts = QHBoxLayout(self.home_charts_wrap)
        charts.setContentsMargins(0, 0, 0, 0)
        charts.setSpacing(Space.MD)
        self.waveform_panel = HomeWaveformPanel()
        self._wf_canvas = self.waveform_panel.canvas
        self.waveform_panel.seekRequested.connect(self._home_seek)
        self.waveform_panel.playToggled.connect(self._home_toggle_play)
        self.waveform_panel.stopRequested.connect(self._home_stop)
        self.waveform_panel.rewindRequested.connect(self._home_rewind)
        self.waveform_panel.openEditorRequested.connect(self._home_open_studio_editor)
        self.waveform_panel.lookaheadChanged.connect(self._home_set_lookahead)
        self.waveform_panel.eqApplyRequested.connect(self._home_apply_eq)
        self.waveform_panel.eqDownloadRequested.connect(self._home_download_eq)
        self._home_source_clean: Optional[str] = None
        self._home_eq_path: Optional[str] = None
        self.spectrum_panel = SpectralChartPanel("SPECTROGRAM")
        self._sp_canvas = self.spectrum_panel.balance_canvas
        self.spectrum_panel.seekRequested.connect(self._home_seek)
        try:
            self.spectrum_panel.set_mode("spectrogram")  # load spectrogram view first
        except Exception:
            pass
        charts.addWidget(self.waveform_panel, max(1, int(Layout.CHARTS_WF_WEIGHT)))
        charts.addWidget(self.spectrum_panel, max(1, int(Layout.CHARTS_SP_WEIGHT)))
        hr.addWidget(self.home_charts_wrap)

        # Dedicated home mix player — HiFi PCM (native SR, soft ramps, no FX)
        self._home_player = HiFiPlayer(self)
        self._home_player.setVolume(1.0)
        self._home_player.positionChanged.connect(self._home_on_position)
        self._home_player.durationChanged.connect(self._home_on_duration)
        self._home_player.playbackStateChanged.connect(self._home_on_state)
        self._home_audio_path: Optional[str] = None

        # Deep-read row (file + MIR options already available via current libs)
        self.home_deep_wrap = QFrame()
        self.home_deep_wrap.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.LG}px;
            }}
        """)
        dl = QVBoxLayout(self.home_deep_wrap)
        dl.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        dl.setSpacing(4)
        dh = QLabel("DEEP READOUT  ·  from engine + librosa / numpy (no extra backend)")
        dh.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.2px; color: {Color.MUTED};"
        )
        dl.addWidget(dh)
        self.home_deep_body = QLabel("-  Sample rate  ·  Bit depth  ·  Rolloff  ·  Bandwidth  ·  ZCR  ·  Onset  ·  Energy L/M/H  ·  Mono fit  ·  DC")
        self.home_deep_body.setWordWrap(True)
        self.home_deep_body.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-family: {Type.MONO}; color: {Color.MUTED};"
        )
        dl.addWidget(self.home_deep_body)
        hr.addWidget(self.home_deep_wrap)
        if not Layout.SHOW_DEEP_READOUT:
            self.home_deep_wrap.hide()

        # ---- Convert (simple) + Repair options ----
        tools_row = QHBoxLayout()
        tools_row.setSpacing(Space.MD)

        # Convert card
        conv = QFrame()
        conv.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        cl = QVBoxLayout(conv)
        cl.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        cl.setSpacing(Space.SM)
        ch = QLabel("QUICK CONVERT")
        ch.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.2px; color: {Color.MUTED};"
        )
        ch.setToolTip("Change format only (no loudness repair). Uses FFmpeg already on your system.")
        cl.addWidget(ch)
        fmt_hint = QLabel("Check one or more formats — converts run one at a time in the background")
        fmt_hint.setWordWrap(True)
        fmt_hint.setStyleSheet(f"font-size: 10px; color: {Color.MUTED}; background: transparent;")
        cl.addWidget(fmt_hint)
        self._convert_checks: dict[str, QCheckBox] = {}
        from nodaw.audio.convert import CONVERT_FORMATS

        formats = list(CONVERT_FORMATS)
        pref_raw = (self._prefs or {}).get("convert_formats") or (self._prefs or {}).get(
            "convert_format", "wav"
        )
        if isinstance(pref_raw, str):
            pref_set = {pref_raw}
        else:
            pref_set = set(pref_raw or ["wav"])
        # Two horizontal rows so every option is visible without a dropdown
        row1 = QHBoxLayout()
        row1.setSpacing(Space.SM)
        row2 = QHBoxLayout()
        row2.setSpacing(Space.SM)
        for i, (fmt, label) in enumerate(formats):
            cb = QCheckBox(label)
            cb.setCursor(Qt.PointingHandCursor)
            cb.setToolTip(f"Include {label} in the convert batch")
            self._convert_checks[fmt] = cb
            if fmt in pref_set or (not pref_set and fmt == "wav"):
                cb.setChecked(True)
            cb.toggled.connect(self._persist_convert_format)
            (row1 if i < 3 else row2).addWidget(cb)
        # Ensure at least wav if nothing matched prefs
        if not any(cb.isChecked() for cb in self._convert_checks.values()):
            self._convert_checks.get("wav", list(self._convert_checks.values())[0]).setChecked(True)
        row1.addStretch()
        row2.addStretch()
        cl.addLayout(row1)
        cl.addLayout(row2)
        crow = QHBoxLayout()
        cbtn = SweepButton("Convert")
        cbtn.setMinimumWidth(140)
        cbtn.setToolTip(
            "Convert the last analyzed mix (or pick a file) into every checked format, "
            "sequentially in the background."
        )
        cbtn.clicked.connect(self._home_convert)
        crow.addWidget(cbtn)
        crow.addStretch()
        cl.addLayout(crow)
        tools_row.addWidget(conv, 1)

        # Output folder card
        outf = QFrame()
        outf.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        ol = QVBoxLayout(outf)
        ol.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        ol.setSpacing(Space.SM)
        oh = QLabel("OUTPUT FOLDER")
        oh.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.2px; color: {Color.MUTED};"
        )
        oh.setToolTip("Where repaired and converted files are written. Saved after you change it.")
        ol.addWidget(oh)
        self.output_folder_lbl = QLabel(str((self._prefs or {}).get("output_folder", "")))
        self.output_folder_lbl.setWordWrap(True)
        self.output_folder_lbl.setStyleSheet(
            f"font-size: 11px; font-family: {Type.MONO}; color: {Color.TEXT};"
        )
        ol.addWidget(self.output_folder_lbl)
        ob = QPushButton("Change…")
        ob.setCursor(Qt.PointingHandCursor)
        ob.setToolTip("Pick a folder. Preference is remembered when you reopen CoProducer.")
        ob.clicked.connect(self._pick_output_folder)
        ol.addWidget(ob)
        tools_row.addWidget(outf, 1)
        hr.addLayout(tools_row)

        # A/B test panel (full width above recent + export row; hidden until repair)
        self.ab_panel = QFrame()
        self.ab_panel.setStyleSheet(
            f"background: {Color.ELEVATED}; border: 1px solid {Color.LINE}; "
            f"border-radius: {Radius.XL}px;"
        )
        self.ab_panel.hide()
        ab_lay = QVBoxLayout(self.ab_panel)
        ab_lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        ab_lay.setSpacing(Space.SM)
        ab_hdr = QLabel("A/B Test: Original vs Repaired")
        ab_hdr.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none;"
        )
        ab_lay.addWidget(ab_hdr)
        ab_ctrl = QHBoxLayout()
        self.ab_btn_a = QPushButton("A: Orig")
        self.ab_btn_a.setCheckable(True)
        self.ab_btn_a.setChecked(True)
        self.ab_btn_a.setCursor(Qt.PointingHandCursor)
        self.ab_btn_b = QPushButton("B: Repair")
        self.ab_btn_b.setCheckable(True)
        self.ab_btn_b.setCursor(Qt.PointingHandCursor)
        self.ab_play_btn = QPushButton("Play")
        self.ab_play_btn.setCursor(Qt.PointingHandCursor)
        self.ab_play_btn.setObjectName("Primary")
        self.ab_play_btn.setFixedWidth(80)
        for btn in (self.ab_btn_a, self.ab_btn_b):
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: {Color.ELEVATED}; border: 1px solid {Color.LINE};
                    padding: 4px {Space.MD}px; border-radius: {Radius.MD}px;
                    font-size: {Type.CAPTION - 1}px; color: {Color.TEXT};
                }}
                QPushButton:checked {{
                    background: {Color.with_alpha(Color.ACCENT, 0.15)};
                    border-color: {Color.ACCENT}; color: {Color.ACCENT};
                }}
            """)
        self.ab_btn_a.clicked.connect(lambda: self._ab_select("a"))
        self.ab_btn_b.clicked.connect(lambda: self._ab_select("b"))
        self.ab_play_btn.clicked.connect(self._ab_toggle_play)
        ab_ctrl.addWidget(self.ab_btn_a)
        ab_ctrl.addWidget(self.ab_btn_b)
        ab_ctrl.addStretch()
        ab_ctrl.addWidget(self.ab_play_btn)
        ab_lay.addLayout(ab_ctrl)
        self.ab_status = QLabel("")
        self.ab_status.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; color: {Color.MUTED}; background: transparent;"
        )
        ab_lay.addWidget(self.ab_status)
        hr.addWidget(self.ab_panel)

        # Recent tracks + Export Report: same row, tops & bottoms locked together
        # Row 0: section label | (spacer matching label height)
        # Row 1: recent grid   | Export Report card (fills same height as grid)
        pair = QGridLayout()
        pair.setHorizontalSpacing(Space.LG)
        pair.setVerticalSpacing(Space.SM)
        pair.setContentsMargins(0, 0, 0, 0)
        pair.setColumnStretch(0, 1)
        pair.setColumnStretch(1, 0)
        pair.setRowStretch(0, 0)
        pair.setRowStretch(1, 1)

        # Favorites quick bar
        fav_wrap = QWidget()
        fav_wrap.setStyleSheet("background: transparent;")
        fav_l = QVBoxLayout(fav_wrap)
        fav_l.setContentsMargins(0, 0, 0, Space.SM)
        fav_l.setSpacing(6)
        fav_hdr = self._section_label("FAVORITES")
        fav_l.addWidget(fav_hdr)
        self._favorites_bar = QHBoxLayout()
        self._favorites_bar.setSpacing(Space.SM)
        fav_l.addLayout(self._favorites_bar)
        self._favorites_empty = QLabel("Star items in History to pin them here")
        self._favorites_empty.setStyleSheet(
            f"font-size: 11px; color: {Color.MUTED}; background: transparent;"
        )
        fav_l.addWidget(self._favorites_empty)
        hr.addWidget(fav_wrap)
        # Populate any existing favorites after layout exists
        QTimer.singleShot(0, self._refresh_favorites_bar)

        sec_recent = self._section_label("RECENT ANALYSES")
        pair.addWidget(sec_recent, 0, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # Keep right column label-row height identical so Export top meets track cards
        sec_spacer = QLabel("")
        sec_spacer.setFixedHeight(max(18, sec_recent.sizeHint().height()))
        sec_spacer.setStyleSheet("background: transparent; border: none;")
        pair.addWidget(sec_spacer, 0, 1)

        recent_grid_w = QWidget()
        recent_grid_w.setStyleSheet("background: transparent;")
        recent_grid = QGridLayout(recent_grid_w)
        recent_grid.setContentsMargins(0, 0, 0, 0)
        recent_grid.setHorizontalSpacing(Space.MD)
        recent_grid.setVerticalSpacing(Space.MD)
        self.recent_cards: list[RecentCard] = []
        for i in range(4):
            card = RecentCard("--", "--", "")
            card.hide()
            card.clicked.connect(self._open_recent_item)
            recent_grid.addWidget(card, i // 2, i % 2)
            self.recent_cards.append(card)
        pair.addWidget(recent_grid_w, 1, 0)

        self.export_card = ExportCard()
        self.export_card.exportRequested.connect(self._on_export)
        self.export_card.setFixedWidth(300)
        # Expand vertically so top/bottom match the recent tracks block
        self.export_card.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        pair.addWidget(self.export_card, 1, 1)

        hr.addLayout(pair)

        open_report = QPushButton("Open Full Report")
        open_report.setObjectName("Primary")
        open_report.setCursor(Qt.PointingHandCursor)
        open_report.setFixedWidth(300)
        open_report.clicked.connect(lambda: self._navigate(1))
        open_row = QHBoxLayout()
        open_row.addStretch(1)
        open_row.addWidget(open_report, 0, Qt.AlignmentFlag.AlignRight)
        hr.addLayout(open_row)

        # Keep metrics_bar for compatibility with _refresh_dashboard
        self.metrics_bar = BottomMetricsBar()
        self.metrics_bar.hide()
        hr.addWidget(self.metrics_bar)

        lay.addWidget(self.home_results)

        # Batch shortcut
        batch = QPushButton("Analyze Folder (Batch)")
        batch.setCursor(Qt.PointingHandCursor)
        batch.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px dashed {Color.LINE};
                padding: {Space.MD}px {Space.XL}px; border-radius: {Radius.XL}px;
                font-size: {Type.CAPTION}px; color: {Color.MUTED};
            }}
            QPushButton:hover {{
                background: {Color.HOVER}; color: {Color.ACCENT}; border-color: {Color.ACCENT};
            }}
        """)
        batch.clicked.connect(self._pick_folder_batch)
        lay.addWidget(batch)

        lay.addStretch()
        scroll.setWidget(inner)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

    def _pick_folder_batch(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Select folder for batch analysis",
            str(PROJECT_ROOT / "input" / "batch"),
        )
        if folder:
            self._run_analysis("batch", {"folder": folder})

    def _section_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"""
            font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']};
            color: {Color.MUTED}; letter-spacing: 0.5px; background: transparent;
        """)
        return lbl

    # == Reference Match Screen ===============================

    def _build_reference_screen(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(Space.MASSIVE, Space.HUGE, Space.MASSIVE, Space.HUGE)
        lay.setSpacing(Space.XXL)

        # Header
        hdr = QVBoxLayout()
        hdr.setSpacing(Space.SM)
        hdr.addWidget(self._hero_title("Reference Match"))
        hdr.addWidget(self._hero_subtitle("Compare your mix to a professional reference."))
        lay.addLayout(hdr)

        # Drop zones
        zones = QHBoxLayout()
        zones.setSpacing(Space.XXL)
        self.ref_mix_zone = DropZone("Your Mix", "Drop or click to browse")
        self.ref_mix_zone.setMinimumHeight(120)
        self.ref_mix_zone.filesDropped.connect(lambda fs: self._on_ref_drop(fs, "mix"))
        self.ref_ref_zone = DropZone("Reference Track", "Drop or click to browse")
        self.ref_ref_zone.setMinimumHeight(120)
        self.ref_ref_zone.filesDropped.connect(lambda fs: self._on_ref_drop(fs, "ref"))
        zones.addWidget(self.ref_mix_zone)
        zones.addWidget(self.ref_ref_zone)
        lay.addLayout(zones)

        self.ref_status = QLabel("")
        self.ref_status.setWordWrap(True)
        self.ref_status.setStyleSheet(f"font-size: 12px; color: {Color.MUTED}; background: transparent;")
        lay.addWidget(self.ref_status)

        # Compare button - secondary sweep (animates until hover)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.compare_btn = SweepButton("Compare Tracks")
        self.compare_btn.setMinimumWidth(240)
        self.compare_btn.setMinimumHeight(48)
        self.compare_btn.setToolTip(
            "Run full analysis: similarity, every metric, meters, waveforms, spectrum, spectrograms."
        )
        self.compare_btn.clicked.connect(self._run_reference_compare)
        btn_row.addWidget(self.compare_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        # Results area
        self.ref_results = QFrame()
        self.ref_results.setStyleSheet("background: transparent; border: none;")
        self.ref_results.hide()
        rl = QVBoxLayout(self.ref_results)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(Space.XL)

        # Similarity card
        self.ref_sim_card = Card("panel", elevation=Elevation.HIGH)
        self.ref_sim_card.setGraphicsEffect(None)
        scl = QVBoxLayout(self.ref_sim_card)
        scl.setContentsMargins(Space.XXL, Space.XXL, Space.XXL, Space.XXL)
        scl.setAlignment(Qt.AlignCenter)

        vs_row = QHBoxLayout()
        vs_row.setSpacing(Space.XXL)
        vs_row.setAlignment(Qt.AlignCenter)

        self.ref_track_a = ReferenceTrackCard("Your Mix")
        self.ref_track_b = ReferenceTrackCard("Reference")

        vs_center = QVBoxLayout()
        vs_center.setAlignment(Qt.AlignCenter)
        vs_center.setSpacing(Space.XS)
        self.ref_sim_score = QLabel("-")
        self.ref_sim_score.setAlignment(Qt.AlignCenter)
        self.ref_sim_score.setStyleSheet(
            f"font-size: {Type.DISPLAY_XL}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {Color.ACCENT}; background: transparent;"
        )
        vs_center.addWidget(self.ref_sim_score)
        vs_label = QLabel("Similarity")
        vs_label.setAlignment(Qt.AlignCenter)
        vs_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; letter-spacing: 0.5px;"
        )
        vs_center.addWidget(vs_label)
        vs_center_w = QWidget()
        vs_center_w.setLayout(vs_center)

        vs_row.addWidget(self.ref_track_a, 1)
        vs_row.addWidget(vs_center_w, 0)
        vs_row.addWidget(self.ref_track_b, 1)
        scl.addLayout(vs_row)

        # Engine difference cards
        self.ref_diffs = QVBoxLayout()
        self.ref_diffs.setSpacing(Space.SM)

        rl.addWidget(self.ref_sim_card)
        rl.addLayout(self.ref_diffs)

        # Side-by-side variable table (every detectable metric)
        self.ref_vars_frame = QFrame()
        self.ref_vars_frame.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        rvl = QVBoxLayout(self.ref_vars_frame)
        rvl.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        rvl.setSpacing(4)
        rvh = QLabel("SIDE-BY-SIDE VARIABLES  ·  Your Mix  →  Reference")
        rvh.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.2px; color: {Color.MUTED};"
        )
        rvl.addWidget(rvh)
        self.ref_var_rows = QVBoxLayout()
        self.ref_var_rows.setSpacing(2)
        rvl.addLayout(self.ref_var_rows)
        rl.addWidget(self.ref_vars_frame)

        # Recommendations
        self.ref_recs = Card("elevated")
        rr_lay = QVBoxLayout(self.ref_recs)
        rr_lay.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        rr_lay.setSpacing(Space.SM)
        rr_lay.addWidget(self._section_label("Recommendations"))
        self.ref_recs_body = QLabel("")
        self.ref_recs_body.setWordWrap(True)
        self.ref_recs_body.setStyleSheet(
            f"font-size: {Type.BODY}px; color: {Color.TEXT}; background: transparent; "
            f"border: none; line-height: 1.5;"
        )
        rr_lay.addWidget(self.ref_recs_body)
        rl.addWidget(self.ref_recs)

        # Exhaustive A/B studio (embedded - not a separate page)
        self.ref_ab_panel = ABComparePage(embedded=True)
        self.ref_ab_panel.hide()
        # When Reference A/B plays, stop Home / mini A/B so device isn't shared
        self.ref_ab_panel.requestStopOthers.connect(self._stop_non_reference_players)
        rl.addWidget(self.ref_ab_panel)

        rl.addStretch()
        lay.addWidget(self.ref_results)
        lay.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

    def _set_ref_slot(self, target: str, path: str, subtitle: str | None = None):
        """Assign a file to Your Mix or Reference and update the drop zone label."""
        p = Path(path)
        name = p.name[:36] if p.name else "-"
        if target == "mix":
            self._ref_mix = str(p)
            self.ref_mix_zone.title.setText(name)
            if subtitle:
                self.ref_mix_zone.subtitle.setText(subtitle)
            else:
                self.ref_mix_zone.subtitle.setText("Your mix")
        else:
            self._ref_track = str(p)
            self.ref_ref_zone.title.setText(name)
            if subtitle:
                self.ref_ref_zone.subtitle.setText(subtitle)
            else:
                self.ref_ref_zone.subtitle.setText("Reference track")

    def _on_ref_drop(self, files: list[str], target: str):
        """Click opens a browser; drop sets the path."""
        path = None
        if files:
            path = files[0]
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select Your Mix" if target == "mix" else "Select Reference Track",
                filter="Audio Files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.aiff *.aif)",
            )
        if not path:
            return
        self._set_ref_slot(target, path)
        if hasattr(self, "ref_status"):
            self.ref_status.setText(
                f"{'Your mix' if target == 'mix' else 'Reference'}: {Path(path).name}"
            )

    def _prepare_reference_match(self):
        """
        When opening Reference Match (never auto-runs analysis - avoids compare loop):
        - Last analyzed song → Your Mix
        - If repair pair exists: Original → Your Mix, Repaired → Reference (ready for one Compare click)
        """
        # Don't wipe results if we just finished a compare
        last = resolve_audio_path(self.last_result) if self.last_result else None
        # If last_result is a reference report, keep showing it
        if self.last_result and self.last_result.get("report_type") == "reference":
            if getattr(self, "ref_results", None) and not self.ref_results.isVisible():
                try:
                    self._populate_reference(self.last_result)
                except Exception:
                    pass
            return

        orig = self._ab_original_path
        rep = self._ab_repaired_path
        orig_ok = bool(orig and Path(str(orig)).is_file())
        rep_ok = bool(rep and Path(str(rep)).is_file())
        last_ok = bool(last and last.is_file())

        if orig_ok and rep_ok:
            self._set_ref_slot("mix", str(orig), "Original (your mix)")
            self._set_ref_slot("ref", str(rep), "Repaired (comparison)")
            self.ref_status.setText(
                "Loaded original → Your Mix and repaired → Reference. "
                "Click Compare Tracks once for the full A/B readout."
            )
            return

        if last_ok:
            self._set_ref_slot("mix", str(last), "From last analysis")
            if self._ref_track and Path(str(self._ref_track)).is_file():
                self.ref_status.setText(
                    f"Your mix: {last.name}. Reference ready - click Compare Tracks."
                )
            else:
                self.ref_status.setText(
                    f"Your mix: {last.name}. Load a Reference (click the card), then Compare Tracks."
                )
            return

        self.ref_status.setText(
            "Drop or click to load Your Mix and a Reference, then click Compare Tracks."
        )

    def _run_reference_compare(self):
        """Compare with current slots; fill missing paths via dialogs."""
        song = self._ref_mix
        ref = self._ref_track
        # Prefer last analysis if mix empty
        if not song or not Path(str(song)).is_file():
            last = resolve_audio_path(self.last_result) if self.last_result else None
            if last and last.is_file():
                song = str(last)
                self._set_ref_slot("mix", song, "From last analysis")
        if not song or not Path(str(song)).is_file():
            song, _ = QFileDialog.getOpenFileName(
                self, "Select Your Mix",
                filter="Audio Files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.aiff *.aif)",
            )
            if song:
                self._set_ref_slot("mix", song)
        if not ref or not Path(str(ref)).is_file():
            # If we have original+repaired pair, use repaired as reference
            if (
                self._ab_repaired_path
                and Path(str(self._ab_repaired_path)).is_file()
                and song
                and Path(song).resolve() != Path(str(self._ab_repaired_path)).resolve()
            ):
                ref = str(self._ab_repaired_path)
                self._set_ref_slot("ref", ref, "Repaired (comparison)")
            else:
                ref, _ = QFileDialog.getOpenFileName(
                    self, "Select Reference Track",
                    filter="Audio Files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.aiff *.aif)",
                )
                if ref:
                    self._set_ref_slot("ref", ref)
        if not song or not ref:
            QMessageBox.information(
                self, "Reference Match",
                "Need both Your Mix and a Reference track to compare.",
            )
            return
        if Path(song).resolve() == Path(ref).resolve():
            QMessageBox.information(
                self, "Reference Match",
                "Your Mix and Reference are the same file. Pick a different reference.",
            )
            return
        self.ref_status.setText("Comparing… results appear below when finished.")
        self.compare_btn.setEnabled(False)
        self._run_analysis("reference", {"song": song, "reference": ref})

    # == Report Viewer (matches product mockup) ================

    def _build_report_viewer(self) -> QWidget:
        """Premium report layout matching the CoProducer product mockup."""
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(Space.XXL, Space.XL, Space.XXL, Space.XXL)
        lay.setSpacing(Space.LG)

        # --- Window title bar strip ---
        title_bar = QHBoxLayout()
        title_lbl = QLabel("CoProducer Core Analyzer Report")
        title_lbl.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; letter-spacing: 0.3px;"
        )
        title_bar.addWidget(title_lbl)
        title_bar.addStretch()
        back = QPushButton("Dashboard")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(lambda: self._navigate(0))
        title_bar.addWidget(back)
        lay.addLayout(title_bar)

        # --- File header + Overall Score ---
        header_card = QFrame()
        header_card.setObjectName("HeaderCard")
        header_card.setStyleSheet(f"""
            QFrame#HeaderCard {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        hc = QHBoxLayout(header_card)
        hc.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        hc.setSpacing(Space.XL)

        # Left: file info
        left_info = QVBoxLayout()
        left_info.setSpacing(Space.SM)
        self.viewer_track_name = QLabel("-")
        self.viewer_track_name.setStyleSheet(
            f"font-size: {Type.H2}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {Color.TEXT}; background: transparent;"
        )
        left_info.addWidget(self.viewer_track_name)

        self.viewer_meta_line = QLabel("")
        self.viewer_meta_line.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent;"
        )
        left_info.addWidget(self.viewer_meta_line)

        # Metadata chips row
        self.viewer_chips = QHBoxLayout()
        self.viewer_chips.setSpacing(Space.SM)
        left_info.addLayout(self.viewer_chips)
        left_info.addStretch()
        hc.addLayout(left_info, 2)

        # Right: Overall Mix Score (mockup style)
        score_box = QVBoxLayout()
        score_box.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        score_box.setSpacing(Space.XS)
        score_lbl = QLabel("OVERALL MIX SCORE")
        score_lbl.setAlignment(Qt.AlignRight)
        score_lbl.setStyleSheet(
            f"font-size: {Type.TINY}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.ACCENT}; letter-spacing: 1.2px; background: transparent;"
        )
        score_box.addWidget(score_lbl)

        score_row = QHBoxLayout()
        score_row.setAlignment(Qt.AlignRight)
        self.viewer_score_num = QLabel("-")
        self.viewer_score_num.setStyleSheet(
            f"font-size: 56px; font-weight: {Type.WEIGHTS['bold']}; "
            f"font-family: {Type.DISPLAY}; color: {Color.ACCENT}; "
            f"letter-spacing: -1.5px; background: transparent;"
        )
        self.viewer_score_den = QLabel("/ 100")
        self.viewer_score_den.setStyleSheet(
            f"font-size: {Type.H2}px; font-family: {Type.DISPLAY}; color: {Color.MUTED}; "
            f"background: transparent; padding-top: 18px;"
        )
        score_row.addWidget(self.viewer_score_num)
        score_row.addWidget(self.viewer_score_den)
        score_box.addLayout(score_row)

        self.viewer_quality = QLabel("")
        self.viewer_quality.setAlignment(Qt.AlignRight)
        self.viewer_quality.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent;"
        )
        score_box.addWidget(self.viewer_quality)

        # Progress bar under score
        self.viewer_score_bar = QProgressBar()
        self.viewer_score_bar.setRange(0, 100)
        self.viewer_score_bar.setTextVisible(False)
        self.viewer_score_bar.setFixedHeight(6)
        self.viewer_score_bar.setFixedWidth(200)
        self.viewer_score_bar.setStyleSheet(f"""
            QProgressBar {{
                background: {Color.LINE}; border: none; border-radius: 3px;
            }}
            QProgressBar::chunk {{
                background: {Color.ACCENT};
                border-radius: 3px;
            }}
        """)
        score_box.addWidget(self.viewer_score_bar, 0, Qt.AlignRight)
        hc.addLayout(score_box, 1)
        lay.addWidget(header_card)

        # --- Technical Scorecards (6 metrics like mockup) ---
        self.scorecard_row = QHBoxLayout()
        self.scorecard_row.setSpacing(Space.MD)
        self.scorecards: dict[str, QLabel] = {}
        for key, label, unit in [
            ("lufs", "LOUDNESS (ITU-R BS.1770)", "LUFS Integrated"),
            ("tp", "TRUE PEAK", "dBTP"),
            ("lra", "LOUDNESS RANGE", "LU"),
            ("peak", "PEAK", "dBFS"),
            ("rms", "RMS", "dBFS"),
            ("crest", "CREST FACTOR", "dB"),
        ]:
            card = QFrame()
            card.setStyleSheet(f"""
                QFrame {{
                    background: {Color.ELEVATED};
                    border: 1px solid {Color.LINE};
                    border-radius: {Radius.LG}px;
                }}
            """)
            cl = QVBoxLayout(card)
            cl.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
            cl.setSpacing(2)
            t = QLabel(label)
            t.setStyleSheet(
                f"font-size: 9px; font-weight: {Type.WEIGHTS['semibold']}; "
                f"color: {Color.MUTED}; letter-spacing: 0.6px; background: transparent;"
            )
            t.setWordWrap(True)
            cl.addWidget(t)
            val = QLabel("-")
            val.setStyleSheet(
                f"font-size: 22px; font-weight: {Type.WEIGHTS['bold']}; "
                f"color: {Color.ACCENT}; background: transparent;"
            )
            cl.addWidget(val)
            u = QLabel(unit)
            u.setStyleSheet(
                f"font-size: 10px; color: {Color.MUTED}; background: transparent;"
            )
            cl.addWidget(u)
            self.scorecards[key] = val
            self.scorecard_row.addWidget(card, 1)
        lay.addLayout(self.scorecard_row)

        # --- Charts row (HQ canvases) ---
        charts = QHBoxLayout()
        charts.setSpacing(Space.MD)
        self._report_wf_canvas = WaveformCanvas()
        self.report_waveform = ChartPanel("WAVEFORM ENVELOPE", self._report_wf_canvas)
        self.report_spectrum = SpectralChartPanel("SPECTRAL BALANCE")
        self._report_sp_canvas = self.report_spectrum.balance_canvas
        charts.addWidget(self.report_waveform, 1)
        charts.addWidget(self.report_spectrum, 1)
        lay.addLayout(charts)

        # --- Reference Match + Recommendations ---
        mid = QHBoxLayout()
        mid.setSpacing(Space.MD)

        # Reference match card
        ref_card = QFrame()
        ref_card.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        rcl = QVBoxLayout(ref_card)
        rcl.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        rcl.setSpacing(Space.SM)
        rh = QLabel("REFERENCE MATCH")
        rh.setStyleSheet(
            f"font-size: 10px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; letter-spacing: 1px; background: transparent;"
        )
        rcl.addWidget(rh)
        self.ref_sim_big = QLabel("-")
        self.ref_sim_big.setStyleSheet(
            f"font-size: 36px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {Color.ACCENT}; background: transparent;"
        )
        rcl.addWidget(self.ref_sim_big)
        self.ref_sim_bar = QProgressBar()
        self.ref_sim_bar.setRange(0, 100)
        self.ref_sim_bar.setTextVisible(False)
        self.ref_sim_bar.setFixedHeight(5)
        self.ref_sim_bar.setStyleSheet(f"""
            QProgressBar {{ background: {Color.LINE}; border: none; border-radius: 2px; }}
            QProgressBar::chunk {{
                background: {Color.ACCENT}; border-radius: 2px;
            }}
        """)
        rcl.addWidget(self.ref_sim_bar)
        self.ref_diffs_label = QLabel("")
        self.ref_diffs_label.setWordWrap(True)
        self.ref_diffs_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.TEXT}; background: transparent;"
        )
        rcl.addWidget(self.ref_diffs_label)
        mid.addWidget(ref_card, 1)

        # Actionable recommendations
        rec_card = QFrame()
        rec_card.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        rrl = QVBoxLayout(rec_card)
        rrl.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        rrl.setSpacing(Space.SM)
        rrh = QLabel("ACTIONABLE RECOMMENDATIONS")
        rrh.setStyleSheet(
            f"font-size: 10px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; letter-spacing: 1px; background: transparent;"
        )
        rrl.addWidget(rrh)
        self.viewer_recs = RecommendationCard()
        # Flatten RecommendationCard into this section (no double card border)
        self.viewer_recs.setStyleSheet("background: transparent; border: none;")
        rrl.addWidget(self.viewer_recs)
        mid.addWidget(rec_card, 1)
        lay.addLayout(mid)

        # --- Technical Summary + Analysis Info ---
        bottom = QHBoxLayout()
        bottom.setSpacing(Space.MD)

        tech_card = QFrame()
        tech_card.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        tcl = QVBoxLayout(tech_card)
        tcl.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        th = QLabel("TECHNICAL SUMMARY")
        th.setStyleSheet(
            f"font-size: 10px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; letter-spacing: 1px; background: transparent;"
        )
        tcl.addWidget(th)
        self.tech_summary_label = QLabel("")
        self.tech_summary_label.setWordWrap(True)
        self.tech_summary_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.TEXT}; background: transparent;"
        )
        tcl.addWidget(self.tech_summary_label)
        bottom.addWidget(tech_card, 1)

        info_card = QFrame()
        info_card.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        icl = QVBoxLayout(info_card)
        icl.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        ih = QLabel("ANALYSIS INFO")
        ih.setStyleSheet(
            f"font-size: 10px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; letter-spacing: 1px; background: transparent;"
        )
        icl.addWidget(ih)
        self.analysis_info_label = QLabel("")
        self.analysis_info_label.setWordWrap(True)
        self.analysis_info_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.TEXT}; background: transparent;"
        )
        icl.addWidget(self.analysis_info_label)
        bottom.addWidget(info_card, 1)
        lay.addLayout(bottom)

        # --- Export bar ---
        export_bar = QHBoxLayout()
        export_bar.setSpacing(Space.MD)
        for fmt, label in [("html", "HTML"), ("json", "JSON"), ("txt", "TXT")]:
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"""
                QPushButton {{
                    background: {Color.ELEVATED}; border: 1px solid {Color.LINE};
                    border-radius: {Radius.MD}px; padding: 10px 20px;
                    font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['medium']};
                    color: {Color.TEXT};
                }}
                QPushButton:hover {{ border-color: {Color.ACCENT}; color: {Color.ACCENT}; }}
            """)
            b.clicked.connect(lambda checked=False, f=fmt: self._on_export(f))
            export_bar.addWidget(b)

        local_badge = QLabel("  100% LOCAL  ·  No Cloud  ·  Private  ")
        local_badge.setStyleSheet(f"""
            background: {Color.with_alpha(Color.SUCCESS, 0.12)};
            color: {Color.SUCCESS};
            border: 1px solid {Color.with_alpha(Color.SUCCESS, 0.3)};
            border-radius: {Radius.PILL}px;
            padding: 6px 14px;
            font-size: 11px; font-weight: {Type.WEIGHTS['semibold']};
        """)
        export_bar.addStretch()
        export_bar.addWidget(local_badge)
        lay.addLayout(export_bar)

        # Keep sections_area for advanced collapsible details
        self.sections_area = QVBoxLayout()
        self.sections_area.setSpacing(Space.MD)
        lay.addLayout(self.sections_area)
        lay.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

    def _add_section(self, title: str, content: QWidget, expanded: bool = False):
        sec = CollapsibleSection(title, content, expanded)
        self.sections_area.addWidget(sec)

    def _make_chip(self, text: str) -> QLabel:
        chip = QLabel(text)
        chip.setStyleSheet(f"""
            background: {Color.with_alpha(Color.ACCENT, 0.1)};
            color: {Color.ACCENT_SOFT};
            border: 1px solid {Color.with_alpha(Color.ACCENT, 0.25)};
            border-radius: 6px;
            padding: 3px 8px;
            font-size: 11px;
        """)
        return chip

    def _build_findings_widget(self, findings: list[dict]) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(Space.XL, Space.XS, Space.XL, Space.MD)
        l.setSpacing(Space.SM)
        if not findings:
            l.addWidget(QLabel("No issues detected."))
            return w
        for f in findings:
            l.addWidget(FindingCard(
                f.get("severity", "notice"),
                f.get("title", ""),
                f.get("message", ""),
                f.get("action", ""),
            ))
        return w

    def _build_technical_widget(self, track: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(0, Space.XS, 0, Space.MD)
        l.setSpacing(0)

        m = track.get("metrics", {}) or {}
        lm = m.get("loudness", {}) or {}
        rows = [
            ("Integrated LUFS", f"{lm.get('integrated_lufs', '-')} LUFS"),
            ("True Peak", f"{lm.get('true_peak_dbtp', '-')} dBTP"),
            ("Dynamic Range", f"{m.get('dynamic_range_db', '-')} dB"),
            ("Crest Factor", f"{m.get('crest_factor', '-')}x"),
            ("Peak", f"{m.get('peak_dbfs', '-')} dBFS"),
            ("RMS", f"{m.get('rms_dbfs', '-')} dBFS"),
            ("Clipping", str(m.get('clipped_samples_estimate', '-'))),
        ]
        for label, value in rows:
            l.addWidget(MetricCard(label, value))

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {Color.LINE}; border: none; margin: {Space.SM}px {Space.XL}px;")
        l.addWidget(sep)

        audio = track.get("audio", {}) or {}
        extra = [
            ("Sample Rate", f"{audio.get('sample_rate_hz', '-')} Hz"),
            ("Bit Depth", f"{audio.get('bit_depth', '-')}-bit"),
            ("Channels", str(audio.get('channels', '-'))),
            ("Format", str(audio.get('format_name', '-'))),
            ("Codec", str(audio.get('codec_name', '-'))),
            ("Duration", f"{audio.get('duration_seconds', '-')}s"),
        ]
        for label, value in extra:
            l.addWidget(MetricCard(label, value))
        return w

    def _build_reference_widget(self, ref: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(Space.XL, Space.XS, Space.XL, Space.MD)
        l.setSpacing(Space.SM)

        sim = ref.get("similarity_score", "-")
        l.addWidget(MetricCard("Similarity Score", f"{sim}/100"))

        diffs = ref.get("differences", []) or []
        for d in diffs:
            l.addWidget(MetricCard(
                d.get("metric", ""),
                f"{d.get('user_value', '-')} vs {d.get('reference_value', '-')}",
                f"{d.get('delta', '')}",
                d.get("severity") == "pass"
            ))
        return w

    def _build_streaming_widget(self, streaming: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(Space.XL, Space.XS, Space.XL, Space.MD)
        l.setSpacing(Space.XS)
        platforms = streaming.get("platforms", []) or []
        for p in platforms:
            l.addWidget(PlatformRow(p.get("platform", ""), p.get("status", "")))
        if not platforms:
            l.addWidget(QLabel("No streaming data."))
        return w

    def _build_codec_widget(self, codec: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(Space.XL, Space.XS, Space.XL, Space.MD)
        l.setSpacing(Space.SM)
        src = codec.get("source", {}) or {}
        l.addWidget(MetricCard("Source Class", src.get("source_class", "-")))
        suitable = src.get("source_suitable_for_mastering", False)
        l.addWidget(MetricCard("Suitable for Mastering", "Yes" if suitable else "No"))
        warning = src.get("warning")
        if warning:
            lbl = QLabel(f"⚠ {warning}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.WARNING}; background: transparent;")
            l.addWidget(lbl)
        return w

    def _build_advanced_widget(self, report: dict) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(Space.XL, Space.XS, Space.XL, Space.MD)
        l.setSpacing(0)
        for key in ("run_id", "generated_at", "report_type", "version"):
            val = report.get(key, "-") or "-"
            l.addWidget(MetricCard(key.replace("_", " ").title(), str(val)))
        return w

    # == Hero helpers =========================================

    def _hero_title(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: {Type.DISPLAY_L}px; font-weight: {Type.WEIGHTS['bold']}; letter-spacing: -0.5px; color: {Color.TEXT}; background: transparent;")
        return lbl

    def _hero_subtitle(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet(f"font-size: {Type.SUBTITLE}px; color: {Color.MUTED}; background: transparent;")
        return lbl

    # == Actions ==============================================

    def _handle_drop(self, files: list[str], mode: str):
        if not files:
            self._pick_and_run(mode)
            return
        path = files[0]
        if mode == "analyze":
            self._run_analysis("analyze", {"song": path})
        elif mode == "reference":
            ref, _ = QFileDialog.getOpenFileName(self, "Select Reference Track")
            self._run_analysis("reference", {"song": path, "reference": ref or path})

    def _pick_and_run(self, mode: str):
        if mode == "analyze":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Audio File",
                filter="Audio Files (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.opus *.aiff *.aif)"
            )
            if path:
                self._run_analysis("analyze", {"song": path})
        elif mode == "reference":
            self._run_reference_compare()

    def _run_analysis(self, mode: str, inputs: dict):
        self._analysis_mode = mode
        # Normalize song path to absolute so re-analyze never depends on cwd
        try:
            song = inputs.get("song")
            if song:
                inputs = dict(inputs)
                inputs["song"] = str(Path(song).expanduser().resolve())
            ref = inputs.get("reference")
            if ref:
                inputs = dict(inputs)
                inputs["reference"] = str(Path(ref).expanduser().resolve())
        except Exception:
            pass
        self._show_loading(True)
        if hasattr(self, "loading_label"):
            self.loading_label.setText(
                "Re-analyzing repaired track..."
                if getattr(self, "_pending_repair_dialog", False)
                else "Analyzing..."
            )
            self.loading_label.show()

        self.thread = QThread()
        self.worker = AnalysisWorker(PROJECT_ROOT, mode, inputs)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        # QueuedConnection: always deliver results on the GUI thread after the
        # worker returns, so dashboard updates are not dropped mid-signal.
        self.worker.finished.connect(
            self._on_analysis_done, Qt.ConnectionType.QueuedConnection
        )
        self.worker.error.connect(
            self._on_analysis_error, Qt.ConnectionType.QueuedConnection
        )
        self.worker.progress.connect(
            lambda m: self.loading_label.setText(m),
            Qt.ConnectionType.QueuedConnection,
        )

        self.worker.finished.connect(self.thread.quit)
        self.worker.error.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.error.connect(self.worker.deleteLater)
        self.thread.finished.connect(self.thread.deleteLater)
        self.thread.finished.connect(lambda: self._show_loading(False))
        self.thread.start()

    def _show_loading(self, visible: bool):
        self.loading_label.setVisible(visible)
        if visible:
            self.loading_bar.start()
        else:
            self.loading_bar.stop()

    def _on_analysis_done(self, report: dict):
        try:
            self.last_result = report or {}
            mode = getattr(self, "_analysis_mode", "") or self.last_result.get("report_type", "")
            is_doctor = mode == "doctor" or self.last_result.get("report_type") == "doctor"

            if is_doctor:
                title = "System Doctor"
                audio_path = None
            else:
                track = self.last_result.get("track") or {}
                audio = track.get("audio") if isinstance(track, dict) else {}
                audio = audio or {}
                fname = audio.get("file_name") or "Mix"
                try:
                    title = Path(str(fname)).stem
                except Exception:
                    title = "Mix"
                audio_path = None
                for key in ("path", "file_path", "source_path"):
                    raw = audio.get(key)
                    if raw:
                        audio_path = str(raw)
                        break

            # Telemetry: non-repair analysis completions
            if (
                self._telemetry
                and not is_doctor
                and not getattr(self, "_pending_repair_dialog", False)
            ):
                try:
                    sc = self.last_result.get("score")
                    self._telemetry.track_analyzed(
                        audio_path,
                        int(sc) if sc is not None else None,
                    )
                except Exception:
                    pass

            # Post-repair re-analysis: ALWAYS apply repaired stats to dashboard + reports
            if getattr(self, "_pending_repair_dialog", False) and not is_doctor:
                self._pending_repair_dialog = False
                # Prefer the known repaired output path over anything nested in the report
                prefer = self._last_repair_out or self._ab_repaired_path or audio_path
                report_copy = dict(self.last_result or {})
                # Defer one tick so we are fully out of the worker signal stack
                QTimer.singleShot(
                    0,
                    lambda r=report_copy, p=prefer, t=title: self._commit_repaired_analysis(r, p, t),
                )
                return

            # Fresh analysis (not post-repair): clear Original/Repaired hover compare
            self._repair_compare_live = False

            # Full report snapshot so History restore brings back all stats
            self.recent.add({
                "title": title[:28],
                "score": self.last_result.get("score"),
                "date": "just now",
                "path": audio_path,
                "data": dict(self.last_result or {}),
            })
            try:
                self._rebuild_history_sidebar()
                self._refresh_favorites_bar()
            except Exception:
                pass
            self._refresh_dashboard()

            if is_doctor:
                # Doctor is not a mix report - stay on Home, skip mix report populate
                self._navigate(0)
                return

            if mode == "reference" or (report or {}).get("report_type") == "reference":
                # Populate first, then lock navigation on Reference Match
                try:
                    self._populate_reference(report)
                except Exception as exc:
                    print("populate_reference failed:", exc)
                    # Hard fallback: at least keep the page open and show status
                    try:
                        self.main_area.setCurrentIndex(2)
                        self.ref_results.setGraphicsEffect(None)
                        self.ref_results.show()
                        if hasattr(self, "ref_status"):
                            self.ref_status.setText(f"Comparison finished but UI update failed: {exc}")
                    except Exception:
                        pass
                try:
                    self._populate_report(report)
                except Exception as exc:
                    print("populate_report (reference) failed:", exc)
                # Stay on Reference Match without re-running prepare (breaks compare loop)
                self.main_area.setCurrentIndex(2)
                self._update_nav_styles(2)
                if hasattr(self, "compare_btn"):
                    self.compare_btn.setEnabled(True)
                if hasattr(self, "ref_status") and self.ref_results.isVisible():
                    # Nudge scroll so results are in view after layout
                    try:
                        if hasattr(self, "ref_screen"):
                            for child in self.ref_screen.findChildren(QScrollArea):
                                child.ensureWidgetVisible(self.ref_results, 0, 40)
                                break
                    except Exception:
                        pass
            elif mode == "batch":
                self._populate_report(report)
                self._navigate(0)
                n = len(report.get("tracks") or [])
                avg = report.get("score")
                QMessageBox.information(
                    self,
                    "Batch Complete",
                    f"Analyzed {n} file(s).\nAverage score: {avg}/100\n\n"
                    f"{report.get('summary', '')}\n\n"
                    "CSV and full reports are in the reports folder.",
                )
            else:
                # Stay on Home - conclusions first (score ring, scorecards, recs)
                self._populate_report(report)
                self._navigate(0)
        except Exception as exc:
            self._pending_repair_dialog = False
            QMessageBox.critical(
                self,
                "Display Error",
                f"Analysis finished but the UI failed to update:\n{exc}",
            )
            self._show_dashboard()

    def _on_analysis_error(self, msg: str):
        if hasattr(self, "compare_btn"):
            self.compare_btn.setEnabled(True)
        pending = getattr(self, "_pending_repair_dialog", False)
        self._pending_repair_dialog = False
        if pending and self._last_repair_out and Path(str(self._last_repair_out)).is_file():
            # Repair file exists; try a direct in-process re-score so the page still updates
            try:
                self.loading_label.setText("Repair done - re-scoring repaired file...")
                self.loading_label.show()
                self.loading_bar.start()
                QApplication.processEvents()
                report = self._analyze_path_direct(self._last_repair_out)
                self.loading_bar.stop()
                self.loading_label.hide()
                if report:
                    self._commit_repaired_analysis(
                        report, self._last_repair_out, Path(str(self._last_repair_out)).stem
                    )
                    return
            except Exception as exc:
                try:
                    self.loading_bar.stop()
                    self.loading_label.hide()
                except Exception:
                    pass
                msg = f"{msg}\n\nFallback re-score also failed: {exc}"
        QMessageBox.critical(self, "Analysis Error", msg)
        self._show_dashboard()

    def _analyze_path_direct(self, path: str | Path) -> dict[str, Any] | None:
        """Synchronous engine analyze (GUI-thread fallback after repair)."""
        p = Path(path).expanduser().resolve()
        if not p.is_file():
            return None
        logger = _gui_logger("coproducer.direct")
        runner = WorkflowRunner(PROJECT_ROOT, logger, generate_previews=False)
        floor = None
        try:
            pre = getattr(self, "_pre_repair_report", None) or {}
            floor = pre.get("score")
            if floor is None:
                floor = getattr(self, "_repair_score_before", None)
            if floor is not None:
                floor = int(floor)
        except Exception:
            floor = None
        applied = None
        try:
            cmd = getattr(self, "_last_repair_command", None) or ""
            if '-af "' in cmd:
                applied = cmd.split('-af "', 1)[1].split('"', 1)[0]
        except Exception:
            applied = None
        return runner.single(
            p,
            floor_score=floor,
            applied_repair_filters=applied,
        )

    def _run_doctor(self):
        self._run_analysis("doctor", {})

    def _save_prefs(self):
        if not hasattr(self, "_prefs") or self._prefs is None:
            self._prefs = load_prefs(PROJECT_ROOT)
        save_prefs(PROJECT_ROOT, self._prefs)
        if hasattr(self, "output_folder_lbl"):
            self.output_folder_lbl.setText(str(self._prefs.get("output_folder", "")))

    def _persist_repair_options(self, *_args):
        if not hasattr(self, "_repair_checks"):
            return
        if not hasattr(self, "_prefs") or self._prefs is None:
            self._prefs = load_prefs(PROJECT_ROOT)
        opts = dict(self._prefs.get("repair_options") or DEFAULT_REPAIR_OPTIONS)
        for kid, cb in self._repair_checks.items():
            opts[kid] = cb.isChecked()
        self._prefs["repair_options"] = opts
        self._save_prefs()

    def _pick_output_folder(self):
        if not hasattr(self, "_prefs") or self._prefs is None:
            self._prefs = load_prefs(PROJECT_ROOT)
        start = self._prefs.get("output_folder") or str(PROJECT_ROOT / "exports" / "repairs")
        folder = QFileDialog.getExistingDirectory(self, "Output folder for repairs & converts", start)
        if folder:
            self._prefs["output_folder"] = folder
            self._save_prefs()
            # Full path dialog with Open folder (paths were getting clipped before)
            from nodaw.ui.convert_dialog import ConvertResultDialog

            dlg = ConvertResultDialog(
                self,
                [{"ok": True, "dest": folder, "fmt": "folder"}],
                title="Output folder saved",
            )
            dlg.exec()

    def _current_audio_path(self) -> Path | None:
        p = resolve_audio_path(self.last_result) if self.last_result else None
        if p and p.is_file():
            return p
        return None

    def _selected_convert_formats(self) -> list[str]:
        """All checked formats (multi-select)."""
        out = [
            str(fmt)
            for fmt, cb in getattr(self, "_convert_checks", {}).items()
            if cb.isChecked()
        ]
        return out or ["wav"]

    def _selected_convert_format(self) -> str:
        fmts = self._selected_convert_formats()
        return fmts[0] if fmts else "wav"

    def _persist_convert_format(self, *_args):
        # Keep at least one format checked
        if not any(cb.isChecked() for cb in getattr(self, "_convert_checks", {}).values()):
            wav = (self._convert_checks or {}).get("wav")
            if wav is not None:
                wav.blockSignals(True)
                wav.setChecked(True)
                wav.blockSignals(False)
        if hasattr(self, "_prefs"):
            fmts = self._selected_convert_formats()
            self._prefs["convert_formats"] = fmts
            self._prefs["convert_format"] = fmts[0] if fmts else "wav"
            self._save_prefs()

    def _home_convert(self):
        """Convert current mix (or file pick) to every checked format, one-by-one."""
        path = self._current_audio_path()
        if path is None:
            picked, _ = QFileDialog.getOpenFileName(
                self, "Choose file to convert",
                filter="Audio (*.wav *.mp3 *.flac *.m4a *.mp4 *.aac *.ogg *.aiff *.aif)",
            )
            if not picked:
                return
            path = Path(picked)
        fmts = self._selected_convert_formats()
        self._persist_convert_format()
        out_dir = Path((self._prefs or load_prefs(PROJECT_ROOT)).get(
            "output_folder", str(PROJECT_ROOT / "exports" / "repairs")
        ))
        out_dir.mkdir(parents=True, exist_ok=True)

        from nodaw.audio.convert import default_dest

        jobs = [(fmt, default_dest(path, fmt, out_dir)) for fmt in fmts]
        self._convert_queue = jobs
        self._convert_results: list[dict] = []
        self._convert_source = path
        self._run_next_convert_job()

    def _run_next_convert_job(self):
        """Background sequential convert (queue of (fmt, dest))."""
        from nodaw.audio.convert import convert_one

        queue = getattr(self, "_convert_queue", None) or []
        if not queue:
            self.loading_bar.stop()
            self.loading_label.hide()
            from nodaw.ui.convert_dialog import show_convert_results

            show_convert_results(self, getattr(self, "_convert_results", []))
            return

        fmt, dest = queue[0]
        self._convert_queue = queue[1:]
        src = getattr(self, "_convert_source", None)
        total_done = len(getattr(self, "_convert_results", [])) + 1
        total_all = total_done + len(self._convert_queue)
        self.loading_label.setText(f"Converting {fmt} ({total_done}/{total_all})…")
        self.loading_label.show()
        self.loading_bar.start()

        class ConvertRunner(QObject):
            done = Signal(object)

            def run(self, source, dest_path):
                try:
                    self.done.emit(convert_one(source, dest_path))
                except Exception as exc:
                    self.done.emit(
                        {
                            "ok": False,
                            "dest": str(dest_path),
                            "fmt": Path(str(dest_path)).suffix.lstrip("."),
                            "error": str(exc),
                        }
                    )

        self._conv_thread = QThread()
        self._conv_worker = ConvertRunner()
        self._conv_worker.moveToThread(self._conv_thread)
        self._conv_thread.started.connect(
            lambda: self._conv_worker.run(str(src), str(dest))
        )
        self._conv_worker.done.connect(
            self._on_convert_job_done, Qt.ConnectionType.QueuedConnection
        )
        self._conv_worker.done.connect(self._conv_thread.quit)
        self._conv_worker.done.connect(self._conv_worker.deleteLater)
        self._conv_thread.finished.connect(self._conv_thread.deleteLater)
        self._conv_thread.start()

    def _on_convert_job_done(self, result):
        """One format finished — record and start next (or show results)."""
        if not isinstance(result, dict):
            result = {"ok": False, "error": str(result), "fmt": "?", "dest": ""}
        self._convert_results = list(getattr(self, "_convert_results", [])) + [result]
        # Continue queue without blocking UI
        QTimer.singleShot(0, self._run_next_convert_job)

    def _on_convert_done(self, result, dest):
        """Legacy single-result handler (kept for safety)."""
        self.loading_bar.stop()
        self.loading_label.hide()
        if isinstance(result, Exception):
            QMessageBox.critical(self, "Convert failed", str(result))
            return
        if isinstance(result, dict):
            from nodaw.ui.convert_dialog import show_convert_results

            show_convert_results(self, [result])
            return
        if getattr(result, "returncode", 1) == 0 and Path(dest).is_file():
            from nodaw.ui.convert_dialog import show_convert_results

            show_convert_results(
                self,
                [{"ok": True, "dest": str(dest), "fmt": Path(dest).suffix.lstrip(".")}],
            )
        else:
            err = (getattr(result, "stderr", None) or "")[-400:]
            QMessageBox.critical(self, "Convert failed", err or "ffmpeg error")

    def _set_repair_enabled(self, score: int | None):
        """
        Grey out Run Auto Repair until a mix is analyzed; keep grey when
        score is maxed or auto-detect finds nothing to fix.
        """
        btn = getattr(self, "run_repair_btn", None)
        hint = getattr(self, "repair_disabled_hint", None)
        if btn is None:
            return

        result = getattr(self, "last_result", None)
        has_analysis = (
            isinstance(result, dict)
            and result.get("report_type") != "doctor"
            and score is not None
        )

        if not has_analysis:
            btn.setEnabled(False)
            btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            btn.setToolTip("Upload and analyze a mix first.")
            if hint is not None:
                hint.setText("Analyze a track to unlock auto repair.")
                hint.hide()  # keep standby quiet; tooltip is enough
            btn.update()
            return

        at_ceiling = int(score) >= 100
        plan = getattr(self, "_auto_repair_plan", None)
        no_actions = plan is not None and not plan.actions
        disabled = at_ceiling or no_actions
        btn.setEnabled(not disabled)

        if at_ceiling:
            btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            btn.setToolTip(
                "Score is 100/100 — automated repair is disabled. "
                "There are no technical gates left for the engine to correct."
            )
            if hint is not None:
                hint.setText(
                    "Repair unavailable at 100/100. Technical thresholds already pass — "
                    "remaining quality is listening / creative judgment, not auto-repair."
                )
                hint.show()
        elif no_actions:
            btn.setCursor(Qt.CursorShape.ForbiddenCursor)
            btn.setToolTip("Auto-detect found no safe technical repair for this mix.")
            if hint is not None:
                hint.setText(
                    "No auto repair needed. Loudness, true peak, and subsonic gates look fine."
                )
                hint.show()
        else:
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip(
                "Applies only auto-detected technical fixes, then re-analyzes "
                "and updates Dashboard and Reports."
            )
            if hint is not None:
                hint.hide()
        btn.update()

    def _update_auto_repair_plan(self, report: dict | None = None):
        """Recompute metric-driven repair plan and refresh the Auto-detected panel."""
        report = report if report is not None else self.last_result
        body = getattr(self, "auto_repair_body", None)
        caution = getattr(self, "auto_repair_caution", None)
        if body is None:
            return
        if not report or report.get("report_type") == "doctor":
            self._auto_repair_plan = None
            body.setText("Analyze a mix to detect what needs repair.")
            body.setStyleSheet(
                f"font-size: 12px; color: {Color.MUTED}; background: transparent; line-height: 1.4;"
            )
            if caution is not None:
                caution.hide()
            self._set_repair_enabled(None)
            return
        plan = detect_repair_plan(report)
        self._auto_repair_plan = plan
        if not plan.actions:
            body.setText(
                plan.summary or "No automatic technical repair needed for current gates."
            )
            body.setStyleSheet(
                f"font-size: 12px; color: {Color.SUCCESS}; background: transparent; line-height: 1.4;"
            )
        else:
            lines = []
            for a in plan.actions:
                conf = int(round(a.confidence * 100))
                sev = a.severity.upper()
                lines.append(f"• {a.label}  [{sev} · {conf}%]\n    {a.reason}")
            body.setText("\n".join(lines))
            body.setStyleSheet(
                f"font-size: 12px; color: {Color.TEXT}; background: transparent; line-height: 1.4;"
            )
        if caution is not None:
            if plan.cautions:
                caution.setText(" · ".join(plan.cautions))
                caution.show()
            else:
                caution.hide()
        # Sync Run Auto Repair enable state with detection
        try:
            sc = report.get("score") if isinstance(report, dict) else None
            self._set_repair_enabled(int(sc) if sc is not None else None)
        except (TypeError, ValueError):
            self._set_repair_enabled(None)

    def _run_custom_repair(self):
        """Auto-detect needed fixes from current analysis; run only those; re-score."""
        score = None
        if self.last_result and self.last_result.get("score") is not None:
            try:
                score = int(self.last_result.get("score"))
            except (TypeError, ValueError):
                score = None
        if score is not None and score >= 100:
            QMessageBox.information(
                self, "Repair",
                "This mix already scores 100/100 on technical gates.\n"
                "Automated repair is disabled - there's nothing left for the engine to fix.",
            )
            return
        path = self._current_audio_path()
        if path is None:
            QMessageBox.information(
                self, "Repair",
                "Analyze a mix first (drop a file), then run repair.",
            )
            return
        # Always re-detect from latest analysis (never use stale manual extras)
        plan = detect_repair_plan(self.last_result or {})
        self._auto_repair_plan = plan
        self._update_auto_repair_plan(self.last_result)
        if not plan.actions:
            QMessageBox.information(
                self, "Repair",
                "Auto-detect found no technical repair for this mix.\n"
                "Loudness, true peak, and subsonic gates look fine.",
            )
            return
        out_dir = Path((self._prefs or load_prefs(PROJECT_ROOT)).get(
            "output_folder", str(PROJECT_ROOT / "exports" / "repairs")
        ))
        try:
            out_dir = out_dir.expanduser().resolve()
        except Exception:
            out_dir = Path(PROJECT_ROOT / "exports" / "repairs").resolve()
        cmd, out, chain = build_auto_repair_command(Path(path).resolve(), out_dir, plan)
        self._repair_score_before = score
        self._last_repair_command = cmd
        self._last_repair_chain = chain
        # Prefer Pedalboard high-quality offline render; FFmpeg is fallback
        self._run_repair_engine(Path(path).resolve(), out_dir, plan, cmd=cmd, out_path=out)

    # == Home waveform studio transport =========================

    def _home_load_audio(self, path: Path):
        if not hasattr(self, "_home_player"):
            return
        p = str(path.resolve())
        if self._home_audio_path == p:
            return
        self._home_audio_path = p
        # Remember dry source for EQ bake (unless path is already an EQ cache)
        if "_eq_preview" not in Path(p).name:
            self._home_source_clean = p
        self._home_player.stop()
        self._home_player.setSource(p)  # HiFiPlayer: native path, float32, no WMF
        if hasattr(self.waveform_panel, "set_playing"):
            self.waveform_panel.set_playing(False)

    def _home_apply_eq(self, low_db: float, mid_db: float, high_db: float):
        """Bake creative EQ into a preview WAV and hot-swap the home player.
        Power light OFF or all zeros → dry source.
        """
        from nodaw.ui.eq_knobs import apply_eq_pedalboard

        src = self._home_source_clean or (
            str(self._current_audio_path()) if self._current_audio_path() else None
        )
        if not src or not Path(src).is_file():
            QMessageBox.information(
                self, "Tone Sculpt", "Analyze or load a mix first, then shape LOW/MID/HIGH."
            )
            return
        was_playing = (
            self._home_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        pos = int(self._home_player.position() or 0)
        strip = getattr(self.waveform_panel, "eq_strip", None)
        out_dir = PROJECT_ROOT / "exports" / "eq_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{Path(src).stem}_eq_preview.wav"
        # Power off or flat → dry
        if abs(low_db) + abs(mid_db) + abs(high_db) < 0.08:
            self._home_eq_path = None
            self._home_audio_path = None
            self._home_load_audio(Path(src))
            self._home_source_clean = src
            self._home_player.setPosition(pos)
            if was_playing:
                self._stop_all_audio(except_source="home")
                self._home_player.play()
            if strip:
                strip.set_status_hint(
                    "power off · dry signal" if not strip.is_powered() else "powered · flat"
                )
            return
        self.loading_label.setText("Sculpting tone…")
        self.loading_label.show()
        QApplication.processEvents()
        res = apply_eq_pedalboard(src, dest, low_db, mid_db, high_db)
        self.loading_label.hide()
        if not res.get("ok"):
            QMessageBox.warning(self, "EQ failed", res.get("error") or "unknown")
            return
        self._home_eq_path = str(dest)
        self._home_audio_path = None
        self._home_load_audio(Path(dest))
        self._home_source_clean = src
        self._home_player.setPosition(pos)
        if was_playing:
            self._stop_all_audio(except_source="home")
            self._home_player.play()
        if strip:
            strip.set_status_hint(f"on  L{low_db:+.0f} M{mid_db:+.0f} H{high_db:+.0f} dB")

    def _home_download_eq(self, low_db: float, mid_db: float, high_db: float):
        """Export EQ'd mix to a user-chosen path (always bakes current knobs)."""
        from nodaw.ui.eq_knobs import apply_eq_pedalboard

        src = self._home_source_clean or (
            str(self._current_audio_path()) if self._current_audio_path() else None
        )
        if not src or not Path(src).is_file():
            QMessageBox.information(self, "Download EQ", "Load a mix first.")
            return
        if abs(low_db) + abs(mid_db) + abs(high_db) < 0.08:
            QMessageBox.information(
                self, "Download EQ", "Twiddle LOW/MID/HIGH first — all bands are flat."
            )
            return
        default = PROJECT_ROOT / "exports" / "eq_preview" / f"{Path(src).stem}_eq.wav"
        default.parent.mkdir(parents=True, exist_ok=True)
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save EQ'd version",
            str(default),
            "WAV (*.wav);;FLAC (*.flac);;MP3 (*.mp3)",
        )
        if not dest:
            return
        dest_p = Path(dest)
        if dest_p.suffix.lower() not in {".wav", ".flac", ".mp3"}:
            dest_p = dest_p.with_suffix(".wav")
        # Bake to wav first, convert if needed
        wav_tmp = dest_p if dest_p.suffix.lower() == ".wav" else (
            PROJECT_ROOT / "exports" / "eq_preview" / f"{dest_p.stem}_tmp.wav"
        )
        self.loading_label.setText("Exporting EQ'd audio…")
        self.loading_label.show()
        QApplication.processEvents()
        res = apply_eq_pedalboard(src, wav_tmp, low_db, mid_db, high_db)
        if not res.get("ok"):
            self.loading_label.hide()
            QMessageBox.warning(self, "Export failed", res.get("error") or "unknown")
            return
        if dest_p.suffix.lower() != ".wav":
            from nodaw.audio.convert import convert_one

            cres = convert_one(wav_tmp, dest_p)
            if not cres.get("ok"):
                self.loading_label.hide()
                QMessageBox.warning(self, "Export failed", cres.get("error") or "convert failed")
                return
        else:
            dest_p = Path(wav_tmp)
        self.loading_label.hide()
        self._home_eq_path = str(dest_p)
        from nodaw.ui.convert_dialog import show_convert_results

        show_convert_results(
            self,
            [{"ok": True, "dest": str(dest_p), "fmt": dest_p.suffix.lstrip(".")}],
        )

    def _home_seek(self, seconds: float):
        if not hasattr(self, "_home_player"):
            return
        self._home_player.setPosition(int(max(0.0, seconds) * 1000))

    def _stop_all_audio(self, except_source: str | None = None):
        """
        Stop every transport so only one surface plays at a time.
        except_source: 'home' | 'mini_ab' | 'ref_ab' | 'studio' | None (stop all)
        """
        if except_source != "home":
            try:
                if hasattr(self, "_home_player") and self._home_player is not None:
                    self._home_player.stop()
            except Exception:
                pass
        if except_source != "mini_ab":
            try:
                if hasattr(self, "_ab_dual") and self._ab_dual is not None:
                    self._ab_dual.stop()
                elif hasattr(self, "_player_a"):
                    self._player_a.stop()
                    if hasattr(self, "_player_b"):
                        self._player_b.stop()
                if hasattr(self, "ab_play_btn"):
                    self.ab_play_btn.setText("Play")
                if hasattr(self, "_ab_sync_timer"):
                    self._ab_sync_timer.stop()
            except Exception:
                pass
        if except_source != "ref_ab":
            try:
                if hasattr(self, "ref_ab_panel") and self.ref_ab_panel is not None:
                    self.ref_ab_panel._ab_stop()
            except Exception:
                pass
        if except_source != "studio":
            try:
                for win in list(getattr(self, "_studio_windows", []) or []):
                    try:
                        if hasattr(win, "stop_playback"):
                            win.stop_playback()
                        elif hasattr(win, "_player") and win._player is not None:
                            win._player.stop()
                    except Exception:
                        pass
            except Exception:
                pass

    def _stop_non_reference_players(self):
        """Stop Home + mini A/B + studio so Reference A/B has exclusive clean playback."""
        self._stop_all_audio(except_source="ref_ab")

    def _stop_reference_ab_playback(self):
        """Stop Reference Match dual A/B when Home starts playing."""
        try:
            if hasattr(self, "ref_ab_panel") and self.ref_ab_panel is not None:
                self.ref_ab_panel._ab_stop()
        except Exception:
            pass

    def _home_toggle_play(self):
        if not hasattr(self, "_home_player"):
            return
        path = self._current_audio_path()
        if path is None or not path.is_file():
            QMessageBox.information(self, "Playback", "Analyze a mix first to load audio.")
            return
        if self._home_audio_path != str(path.resolve()):
            self._home_load_audio(path)
        if self._home_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._home_player.pause()
        else:
            self._stop_all_audio(except_source="home")
            self._home_player.play()

    def _home_stop(self):
        if not hasattr(self, "_home_player"):
            return
        self._home_player.stop()
        self._home_player.setPosition(0)
        if hasattr(self.waveform_panel, "set_position_ms"):
            self.waveform_panel.set_position_ms(0, int(self._home_player.duration() or 0))
        if hasattr(self.waveform_panel, "set_playing"):
            self.waveform_panel.set_playing(False)

    def _home_rewind(self):
        if not hasattr(self, "_home_player"):
            return
        self._home_player.setPosition(0)
        if hasattr(self.waveform_panel, "set_position_ms"):
            self.waveform_panel.set_position_ms(0, int(self._home_player.duration() or 0))

    def _home_set_lookahead(self, seconds: float):
        if hasattr(self, "_wf_canvas") and hasattr(self._wf_canvas, "set_lookahead"):
            self._wf_canvas.set_lookahead(seconds)

    def _home_on_position(self, pos: int):
        if hasattr(self.waveform_panel, "set_position_ms"):
            self.waveform_panel.set_position_ms(pos, int(self._home_player.duration() or 0))
        try:
            if hasattr(self, "spectrum_panel"):
                self.spectrum_panel.set_position(pos / 1000.0)
        except Exception:
            pass

    def _home_on_duration(self, dur: int):
        if hasattr(self, "_wf_canvas") and dur > 0:
            self._wf_canvas.set_duration(dur / 1000.0)
        if hasattr(self.waveform_panel, "set_position_ms"):
            self.waveform_panel.set_position_ms(
                int(self._home_player.position()), int(dur)
            )
        try:
            if hasattr(self, "spectrum_panel") and hasattr(
                self.spectrum_panel, "spectrogram_canvas"
            ) and dur > 0:
                self.spectrum_panel.spectrogram_canvas.set_duration(dur / 1000.0)
        except Exception:
            pass

    def _home_on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        if hasattr(self.waveform_panel, "set_playing"):
            self.waveform_panel.set_playing(playing)

    def _home_open_studio_editor(self):
        path = self._current_audio_path()
        if path is None or not path.is_file():
            picked, _ = QFileDialog.getOpenFileName(
                self, "Open in Studio Editor",
                filter="Audio (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.aiff *.aif)",
            )
            if not picked:
                return
            path = Path(picked)
        self.open_studio_player(path, self.last_result if self.last_result else {})

    def _run_repair_engine(
        self,
        in_path: Path,
        out_dir: Path,
        plan: RepairPlan,
        *,
        cmd: str,
        out_path: Path,
    ):
        """Background Pedalboard-first repair (FFmpeg fallback)."""
        self._last_repair_command = cmd
        self._last_repair_in = str(Path(in_path).resolve())
        self._last_repair_out = str(Path(out_path).resolve())
        if self.last_result and self.last_result.get("report_type") != "doctor":
            try:
                self._pre_repair_report = dict(self.last_result)
            except Exception:
                self._pre_repair_report = self.last_result
        self.loading_label.setText("Running repair (Pedalboard / FFmpeg)...")
        self.loading_label.show()
        self.loading_bar.start()

        class RepairEngineWorker(QObject):
            done = Signal(object)

            def run(self, src, odir, pl):
                try:
                    self.done.emit(run_auto_repair(src, odir, pl, prefer_pedalboard=True))
                except Exception as exc:
                    self.done.emit({"ok": False, "error": str(exc), "engine": "error"})

        self._repair_thread = QThread()
        self._repair_worker = RepairEngineWorker()
        self._repair_worker.moveToThread(self._repair_thread)
        self._repair_thread.started.connect(
            lambda: self._repair_worker.run(str(in_path), str(out_dir), plan)
        )
        self._repair_worker.done.connect(
            self._on_repair_engine_done, Qt.ConnectionType.QueuedConnection
        )
        self._repair_worker.done.connect(self._repair_thread.quit)
        self._repair_worker.done.connect(self._repair_worker.deleteLater)
        self._repair_thread.finished.connect(self._repair_thread.deleteLater)
        self._repair_thread.start()

    def _on_repair_engine_done(self, result: object):
        """Handle Pedalboard/FFmpeg repair result dict."""
        self.loading_bar.stop()
        self.loading_label.hide()
        if not isinstance(result, dict):
            QMessageBox.critical(self, "Repair Failed", str(result))
            return
        if not result.get("ok"):
            err = result.get("error") or result.get("pedalboard_error") or "Unknown repair error"
            QMessageBox.critical(
                self,
                "Repair Failed",
                f"Engine: {result.get('engine')}\n{err}",
            )
            return
        out = result.get("out_path") or self._last_repair_out
        inp = result.get("in_path") or self._last_repair_in
        self._last_repair_out = out
        self._last_repair_in = inp
        self._ab_original_path = inp
        self._ab_repaired_path = out
        eng = result.get("engine") or "repair"
        applied = result.get("applied") or []
        self.ab_status.setText(f"Repaired via {eng}: {', '.join(str(a) for a in applied)[:80]}")
        try:
            self._ab_setup(inp, out)
        except Exception:
            pass
        if not self._pre_repair_report and self.last_result:
            try:
                if self.last_result.get("report_type") != "doctor":
                    self._pre_repair_report = dict(self.last_result)
            except Exception:
                pass
        try:
            self.loading_label.setText("Copying metadata and cover to repaired file...")
            self.loading_label.show()
            QApplication.processEvents()
            self._copy_meta_to_repaired(inp, out)
        except Exception as exc:
            print("repair meta copy:", exc)
        self.loading_label.setText("Re-analyzing repaired track...")
        self.loading_label.show()
        self.loading_bar.start()
        self._pending_repair_dialog = True
        if self._telemetry:
            try:
                self._telemetry.track_repaired(
                    out,
                    getattr(self, "_repair_score_before", None),
                    None,
                )
            except Exception:
                pass
        self._run_analysis("analyze", {"song": out})

    def _run_repair(
        self,
        command: str,
        in_path: Path | str | None = None,
        out_path: Path | str | None = None,
    ):
        """Execute an FFmpeg repair command in a background thread (legacy path)."""
        self._last_repair_command = command
        # Prefer explicit paths (absolute) so post-repair UI never relies on command parsing
        if in_path:
            try:
                self._last_repair_in = str(Path(in_path).expanduser().resolve())
            except Exception:
                self._last_repair_in = str(in_path)
        if out_path:
            try:
                self._last_repair_out = str(Path(out_path).expanduser().resolve())
            except Exception:
                self._last_repair_out = str(out_path)
        if not self._last_repair_in or not self._last_repair_out:
            pin, pout = self._parse_repair_paths(command)
            if not self._last_repair_in and pin:
                self._last_repair_in = pin
            if not self._last_repair_out and pout:
                self._last_repair_out = pout
        # Snapshot pre-repair report for A/B studio + hover compare
        if self.last_result and self.last_result.get("report_type") != "doctor":
            try:
                self._pre_repair_report = dict(self.last_result)
            except Exception:
                self._pre_repair_report = self.last_result
        self.loading_label.setText("Running repair...")
        self.loading_label.show()
        self.loading_bar.start()

        class RepairRunner(QObject):
            done = Signal(object)

            def run(self, cmd):
                try:
                    r = subprocess.run(
                        cmd, capture_output=True, text=True,
                        shell=True, timeout=300
                    )
                    self.done.emit(r)
                except subprocess.TimeoutExpired:
                    self.done.emit(None)
                except Exception as exc:
                    self.done.emit(exc)

        self._repair_thread = QThread()
        self._repair_worker = RepairRunner()
        self._repair_worker.moveToThread(self._repair_thread)
        self._repair_thread.started.connect(lambda: self._repair_worker.run(command))
        self._repair_worker.done.connect(
            self._on_repair_done, Qt.ConnectionType.QueuedConnection
        )
        self._repair_worker.done.connect(self._repair_thread.quit)
        self._repair_worker.done.connect(self._repair_worker.deleteLater)
        self._repair_thread.finished.connect(self._repair_thread.deleteLater)
        self._repair_thread.start()

    def _parse_repair_paths(self, command: str) -> tuple[str | None, str | None]:
        """Extract input and output paths from an FFmpeg command (quote-aware)."""
        import shlex
        inp = None
        out = None
        try:
            parts = shlex.split(command, posix=False)
        except Exception:
            parts = command.split()
        i_flag = False
        audio_ext = (".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".aiff", ".aif")
        candidates: list[str] = []
        for p in parts:
            stripped = p.strip().strip("\"'")
            if p == "-i" or stripped == "-i":
                i_flag = True
                continue
            if i_flag and stripped and not stripped.startswith("-"):
                inp = stripped
                i_flag = False
                continue
            low = stripped.lower()
            if stripped and not stripped.startswith("-") and low.endswith(audio_ext):
                candidates.append(stripped)
        if candidates:
            out = candidates[-1]
            if inp is None and len(candidates) >= 2:
                inp = candidates[0]
        # Resolve to absolute when possible
        def _abs(p: str | None) -> str | None:
            if not p:
                return None
            try:
                pp = Path(p)
                if not pp.is_absolute():
                    pp = (PROJECT_ROOT / pp).resolve()
                else:
                    pp = pp.resolve()
                return str(pp)
            except Exception:
                return p
        return _abs(inp), _abs(out)

    def _on_repair_done(self, result):
        self.loading_bar.stop()
        self.loading_label.hide()
        if result is None:
            QMessageBox.warning(self, "Repair", "Repair timed out (300s).")
            return
        if isinstance(result, Exception):
            QMessageBox.critical(self, "Repair Failed", str(result))
            return
        if getattr(result, "returncode", 1) != 0:
            err = (getattr(result, "stderr", None) or "").strip()[:400]
            QMessageBox.critical(
                self, "Repair Failed", f"Exit {result.returncode}:\n{err}"
            )
            return

        # Resolve repaired file: stored path first, then parse command, then glob
        out = self._last_repair_out
        inp = self._last_repair_in
        if not out or not Path(str(out)).is_file():
            pin, pout = self._parse_repair_paths(getattr(self, "_last_repair_command", "") or "")
            if pout and Path(pout).is_file():
                out = pout
            if pin:
                inp = inp or pin
        if out:
            try:
                out = str(Path(out).expanduser().resolve())
            except Exception:
                out = str(out)
        if inp:
            try:
                inp = str(Path(inp).expanduser().resolve())
            except Exception:
                inp = str(inp)

        if not out or not Path(out).is_file():
            QMessageBox.warning(
                self,
                "Repair",
                "Repair finished but the output file was not found.\n"
                "Check the output folder and try again.",
            )
            return

        self._last_repair_out = out
        self._last_repair_in = inp
        self._ab_original_path = inp
        self._ab_repaired_path = out
        try:
            self._ab_setup(inp, out)
        except Exception:
            pass
        if not self._pre_repair_report and self.last_result:
            try:
                if self.last_result.get("report_type") != "doctor":
                    self._pre_repair_report = dict(self.last_result)
            except Exception:
                pass

        # FFmpeg strips tags/cover - restore them onto the repaired file before re-score
        try:
            self.loading_label.setText("Copying metadata and cover to repaired file...")
            self.loading_label.show()
            QApplication.processEvents()
            self._copy_meta_to_repaired(inp, out)
        except Exception as exc:
            print("repair meta copy:", exc)

        # Always re-analyze the repaired file, then force dashboard/report apply
        self.loading_label.setText("Re-analyzing repaired track...")
        self.loading_label.show()
        self.loading_bar.start()
        self._pending_repair_dialog = True
        self._run_analysis("analyze", {"song": out})

    def _snapshot_ui_tags(self) -> dict[str, str]:
        """Current Home metadata fields (saved or still editing)."""
        out: dict[str, str] = {}
        try:
            panel = getattr(self, "track_meta", None)
            if panel is None:
                return out
            for k, edit in getattr(panel, "_fields", {}).items():
                t = edit.text().strip()
                if t:
                    out[k] = t
        except Exception:
            pass
        return out

    def _live_repair_dicts(self, report: dict | None) -> list[dict]:
        """
        Build fresh repair recommendations with full suggested FFmpeg commands
        for the current analyzed file (absolute paths + per-action filters).
        """
        from dataclasses import asdict
        from nodaw.features.repairs import build_repairs, detect_repair_plan, _ffmpeg_cmd
        from nodaw.core.engine import WorkflowRunner

        report = report or self.last_result or {}
        track = report.get("track") if isinstance(report.get("track"), dict) else {}
        audio = track.get("audio") if isinstance(track.get("audio"), dict) else {}
        path = None
        for key in ("path", "file_path", "source_path"):
            raw = audio.get(key)
            if raw and Path(str(raw)).is_file():
                path = Path(str(raw)).resolve()
                break
        if path is None:
            try:
                p = self._current_audio_path()
                path = p.resolve() if p else None
            except Exception:
                path = None
        if path is None or not path.is_file():
            return list(report.get("repairs") or [])

        out_dir = Path((self._prefs or load_prefs(PROJECT_ROOT)).get(
            "output_folder", str(PROJECT_ROOT / "exports" / "repairs")
        )).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)

        # Prefer engine build_repairs when we can reconstruct TrackAnalysis cheaply
        # via re-using report metrics path: detect plan + craft commands from filters
        plan = detect_repair_plan(report)
        stem = path.stem
        items: list[dict] = []
        if not plan.actions:
            out = out_dir / f"{stem}_repaired.wav"
            items.append({
                "title": "No automatic repair needed",
                "reason": plan.summary,
                "ffmpeg_filter": "anull",
                "command": _ffmpeg_cmd(path, out, "anull"),
                "caution": " ".join(plan.cautions)
                or "Technical gates pass. Remaining quality is listening judgment.",
            })
            return items

        # Combined
        out_all = out_dir / f"{stem}_repaired.wav"
        chain = plan.filter_chain or "anull"
        items.append({
            "title": "Auto technical repair (all suggested)",
            "reason": plan.summary + " " + " ".join(a.reason for a in plan.actions),
            "ffmpeg_filter": chain,
            "command": _ffmpeg_cmd(path, out_all, chain),
            "caution": " ".join(plan.cautions)
            or "Review the rendered file by ear before replacing any master.",
        })
        for a in plan.actions:
            out_one = out_dir / f"{stem}_{a.id}_repaired.wav"
            items.append({
                "title": a.label,
                "reason": a.reason,
                "ffmpeg_filter": a.filter,
                "command": _ffmpeg_cmd(path, out_one, a.filter),
                "caution": f"Confidence {int(a.confidence * 100)}% · {a.severity}",
            })
        # Persist onto last_result so Reports/export see the same commands
        try:
            if isinstance(self.last_result, dict):
                self.last_result["repairs"] = items
        except Exception:
            pass
        return items

    def _copy_meta_to_repaired(self, source: str | None, dest: str | None) -> None:
        """Copy tags + cover from original (and UI fields) onto repaired output."""
        if not source or not dest:
            return
        if not Path(str(source)).is_file() or not Path(str(dest)).is_file():
            return
        from nodaw.audio.tags_media import copy_metadata_and_cover

        extra = self._snapshot_ui_tags()
        # Prefer tags already on the source file; UI overrides fill gaps / edits
        ok, msg = copy_metadata_and_cover(source, dest, extra_tags=extra or None)
        print("repair meta:", ok, msg)

    def _commit_repaired_analysis(
        self,
        report: dict,
        audio_path: str | None,
        title: str = "Mix",
    ):
        """
        Always publish repaired analysis to Dashboard + Reports.
        Never leave the UI on the pre-repair snapshot after a successful re-score.
        """
        try:
            self.loading_bar.stop()
            self.loading_label.hide()
        except Exception:
            pass

        repaired_report = dict(report or {})
        # If report is empty, try one more direct analyze so the page still updates
        active_path = self._last_repair_out or self._ab_repaired_path or audio_path
        if active_path:
            try:
                active_path = str(Path(active_path).expanduser().resolve())
            except Exception:
                active_path = str(active_path)
        if (not repaired_report or repaired_report.get("score") is None) and active_path:
            try:
                direct = self._analyze_path_direct(active_path)
                if direct:
                    repaired_report = dict(direct)
            except Exception as exc:
                print("commit direct re-score:", exc)

        pre = dict(self._pre_repair_report or {})
        before = pre.get("score")
        if before is None:
            before = getattr(self, "_repair_score_before", None)
        after = repaired_report.get("score")
        # Product guarantee: successful technical repair never lowers score
        try:
            from nodaw.core.scoring import floor_score_after_repair, rating as score_rating_fn
            from nodaw.core.models import Finding
            from dataclasses import asdict as _asdict

            if before is not None and after is not None:
                applied = getattr(self, "_last_repair_command", None) or ""
                # extract -af chain if present
                af = None
                if '-af "' in applied:
                    try:
                        af = applied.split('-af "', 1)[1].split('"', 1)[0]
                    except Exception:
                        af = applied[:120]
                findings_raw = repaired_report.get("findings") or []
                # rebuild Finding-like for floor helper
                findings_objs = []
                for f in findings_raw:
                    if isinstance(f, dict):
                        findings_objs.append(
                            Finding(
                                f.get("severity", "notice"),
                                f.get("title", ""),
                                f.get("message", ""),
                                f.get("action", ""),
                                int(f.get("score_penalty") or 0),
                            )
                        )
                floored, findings_out = floor_score_after_repair(
                    int(before),
                    int(after),
                    findings=findings_objs,
                    applied_filters=af,
                )
                repaired_report["raw_score"] = int(after)
                repaired_report["pre_repair_score_floor"] = int(before)
                repaired_report["score_floor_applied"] = floored != int(after)
                repaired_report["score"] = int(floored)
                repaired_report["rating"] = score_rating_fn(int(floored))
                repaired_report["findings"] = [_asdict(f) for f in findings_out]
                if floored != int(after):
                    repaired_report["summary"] = (
                        f"Repaired mix scored {floored}/100 "
                        f"(raw re-score {int(after)}; floored to pre-repair readiness). "
                        "Technical repair readiness is non-decreasing."
                    )
                after = floored
        except Exception as exc:
            print("repair score floor:", exc)

        score_dropped = False
        try:
            if before is not None and after is not None and int(after) < int(before):
                score_dropped = True  # should never happen after floor
        except (TypeError, ValueError):
            score_dropped = False
        if self._telemetry:
            try:
                self._telemetry.track_repaired(
                    active_path,
                    int(before) if before is not None else None,
                    int(after) if after is not None else None,
                )
            except Exception:
                pass

        # Stamp track.audio so every path reader sees the repaired file
        try:
            tr = repaired_report.get("track")
            tr = dict(tr) if isinstance(tr, dict) else {}
            au = tr.get("audio") if isinstance(tr.get("audio"), dict) else {}
            au = dict(au) if au else {}
            if active_path:
                au["path"] = active_path
                au["file_name"] = Path(active_path).name
            tr["audio"] = au
            # Ensure tags from repaired file (post meta-copy) are on the report
            try:
                from nodaw.audio.tags_media import read_tags

                tags = read_tags(active_path) if active_path else {}
                # Merge any UI snapshot (title/artist user just saved on original)
                for k, v in self._snapshot_ui_tags().items():
                    tags.setdefault(k, v)
                extra = tr.get("extra") if isinstance(tr.get("extra"), dict) else {}
                extra = dict(extra)
                if tags:
                    extra["tags"] = tags
                tr["extra"] = extra
            except Exception:
                pass
            repaired_report["track"] = tr
            repaired_report["report_type"] = repaired_report.get("report_type") or "single"
        except Exception:
            pass

        self._post_repair_report = repaired_report
        self.last_result = repaired_report
        self._repair_compare_live = True
        self._ab_repaired_path = active_path or self._ab_repaired_path
        self._analysis_mode = "analyze"

        try:
            tname = Path(
                str((repaired_report.get("track") or {}).get("audio", {}).get("file_name") or title)
            ).stem
            self.recent.add({
                "title": f"{tname[:22]} (repaired)"[:28],
                "score": repaired_report.get("score"),
                "date": "just now",
                "path": active_path or audio_path,
                "data": {
                    "score": repaired_report.get("score"),
                    "rating": repaired_report.get("rating"),
                    "summary": repaired_report.get("summary"),
                    "report_type": repaired_report.get("report_type"),
                    "run_id": repaired_report.get("run_id"),
                    "track": repaired_report.get("track"),
                    "findings": repaired_report.get("findings"),
                    "repairs": repaired_report.get("repairs"),
                    "path": active_path or audio_path,
                },
            })
            self._rebuild_history_sidebar()
            self._refresh_favorites_bar()
        except Exception:
            pass

        # --- Force Home UI to repaired stats (no silent failure) ---
        try:
            self._navigate(0)
        except Exception:
            pass
        try:
            if hasattr(self, "empty_state"):
                self.empty_state.hide()
            if hasattr(self, "home_results"):
                self.home_results.show()
            if hasattr(self, "home_hero"):
                self.home_hero.hide()
        except Exception:
            pass
        try:
            self._refresh_dashboard()
        except Exception as exc:
            print("repair dashboard refresh:", exc)
        # Force cover + metadata panel onto the repaired file (tags copied pre-analyze)
        try:
            if active_path and hasattr(self, "track_meta"):
                from nodaw.audio.tags_media import read_tags

                tags = read_tags(active_path)
                for k, v in self._snapshot_ui_tags().items():
                    tags.setdefault(k, v)
                self.track_meta.load_path(active_path, tags)
                # Prefer tagged title for the header
                t_title = (tags.get("title") or "").strip()
                if t_title and hasattr(self, "home_track_name"):
                    self.home_track_name.setText(f"{t_title} (repaired)"[:48])
                elif hasattr(self, "home_track_name"):
                    self.home_track_name.setText(f"{Path(active_path).stem} (repaired)"[:48])
            if hasattr(self, "home_verdict"):
                summ = repaired_report.get("summary") or repaired_report.get("rating") or ""
                if summ:
                    self.home_verdict.setText(str(summ))
        except Exception as exc:
            print("repair meta UI:", exc)
        try:
            sc = repaired_report.get("score")
            if sc is not None and hasattr(self, "score_ring"):
                rating = repaired_report.get("rating") or score_rating(int(sc))
                self.score_ring.set_score(int(sc), rating)
            if sc is not None:
                self._set_repair_enabled(int(sc))
        except Exception:
            pass
        try:
            # Directly re-apply metrics tiles from repaired report
            track = repaired_report.get("track") if isinstance(repaired_report.get("track"), dict) else {}
            m = track.get("metrics") if isinstance(track.get("metrics"), dict) else {}
            lm = m.get("loudness") if isinstance(m.get("loudness"), dict) else {}
            self._apply_home_metrics(repaired_report, track, m, lm)
        except Exception as exc:
            print("repair apply metrics:", exc)
        try:
            self._populate_report(self.last_result)
            if hasattr(self, "viewer_track_name") and active_path:
                self.viewer_track_name.setText(f"{Path(active_path).name} (repaired)")
        except Exception as exc:
            print("repair report populate:", exc)
        try:
            if active_path and Path(active_path).is_file():
                self._home_audio_path = None
                self._home_load_audio(Path(active_path))
                if hasattr(self, "spectrum_panel"):
                    self.spectrum_panel.set_audio_path(Path(active_path), auto_compute=True)
        except Exception:
            pass
        try:
            self._apply_repair_compare_tiles()
        except Exception:
            pass
        try:
            # Paint now so the user sees new scores before the dialog
            if hasattr(self, "score_ring"):
                self.score_ring.update()
            if hasattr(self, "home_results"):
                self.home_results.update()
            self.update()
            QApplication.processEvents()
        except Exception:
            pass

        dlg = RepairCompleteDialog(
            self,
            active_path or audio_path,
            score_before=before,
            score_after=after,
            accepted=True,
            score_dropped=score_dropped,
        )
        dlg.exec()
        self._handle_repair_dialog_action(dlg, active_path or audio_path)
        # Re-assert after modal (some platforms repaint incorrectly)
        try:
            self.last_result = self._post_repair_report or self.last_result
            if hasattr(self, "empty_state"):
                self.empty_state.hide()
            if hasattr(self, "home_results"):
                self.home_results.show()
            self._refresh_dashboard()
            self._apply_repair_compare_tiles()
            sc = (self.last_result or {}).get("score")
            if sc is not None and hasattr(self, "score_ring"):
                self.score_ring.set_score(
                    int(sc),
                    (self.last_result or {}).get("rating") or score_rating(int(sc)),
                )
            if active_path and hasattr(self, "home_track_name"):
                self.home_track_name.setText(f"{Path(active_path).stem} (repaired)"[:48])
            self._navigate(0)
            QApplication.processEvents()
        except Exception as exc:
            print("repair post-dialog refresh:", exc)

    @staticmethod
    def _metric_snapshot(report: dict | None) -> dict[str, Any]:
        """Flatten metric values used by Home tiles for Original/Repaired hover."""
        report = report or {}
        track = report.get("track") if isinstance(report.get("track"), dict) else {}
        m = track.get("metrics") if isinstance(track.get("metrics"), dict) else {}
        lm = m.get("loudness") if isinstance(m.get("loudness"), dict) else {}
        extra = track.get("extra") if isinstance(track.get("extra"), dict) else {}
        lib = extra.get("librosa") if isinstance(extra.get("librosa"), dict) else {}
        faults = extra.get("technical_faults") if isinstance(extra.get("technical_faults"), dict) else {}
        phase = m.get("phase_correlation")
        if phase is None:
            phase = faults.get("phase_correlation")
        clip = m.get("clipped_samples_estimate")
        if clip is None:
            clip = faults.get("clipped_samples")
        bright = lib.get("spectral_centroid_hz") or lib.get("brightness_score")
        return {
            "lufs": lm.get("integrated_lufs"),
            "tp": lm.get("true_peak_dbtp"),
            "lra": lm.get("loudness_range_lu"),
            "peak": m.get("peak_dbfs"),
            "rms": m.get("rms_dbfs"),
            "crest": m.get("crest_factor"),
            "dr": m.get("dynamic_range_db"),
            "width": m.get("stereo_width_percent"),
            "phase": phase,
            "noise": m.get("noise_floor_dbfs"),
            "tempo": lib.get("tempo_bpm"),
            "centroid": bright,
            "clip": clip,
            "silence": faults.get("silence_ratio"),
            "score": report.get("score"),
        }

    def _apply_repair_compare_tiles(self):
        """Wire Original vs Repaired hover on metric tiles after a repair."""
        if not getattr(self, "_repair_compare_live", False):
            return
        pre = self._pre_repair_report
        post = self._post_repair_report or self.last_result
        if not pre or not post:
            return
        o = self._metric_snapshot(pre)
        r = self._metric_snapshot(post)
        for key, tile in list(getattr(self, "home_metric_tiles", {}).items()):
            if hasattr(tile, "set_compare"):
                tile.set_compare(o.get(key), r.get(key), key)
        for key, tile in list(getattr(self, "home_extra_tiles", {}).items()):
            if hasattr(tile, "set_compare"):
                tile.set_compare(o.get(key), r.get(key), key)

    def _handle_repair_dialog_action(self, dlg: RepairCompleteDialog, out: str | None):
        action = getattr(dlg, "action_chosen", "done")
        if action == "studio" and out and Path(out).is_file():
            self.open_studio_player(out, self._post_repair_report or self.last_result or {})
        elif action == "compare":
            self._open_ab_compare_page()
        elif action == "folder" and out:
            _open_path(Path(out).parent)

    def _open_ab_compare_page(self):
        """Open Reference Match with exhaustive A/B panel (no separate page)."""
        orig = self._pre_repair_report or {}
        rep = self._post_repair_report or self.last_result or {}
        op = self._ab_original_path
        rp = self._ab_repaired_path
        if not op and orig:
            p = resolve_audio_path(orig)
            op = str(p) if p else None
        if not rp and rep:
            p = resolve_audio_path(rep)
            rp = str(p) if p else None
        if op and Path(str(op)).is_file():
            self._set_ref_slot("mix", str(op), "Original (your mix)")
        if rp and Path(str(rp)).is_file():
            self._set_ref_slot("ref", str(rp), "Repaired (comparison)")
        self.main_area.setCurrentIndex(2)
        self._update_nav_styles(2)
        if hasattr(self, "ref_ab_panel") and orig and rep:
            self.ref_ab_panel.set_labels("ORIGINAL", "REPAIRED")
            self.ref_ab_panel.set_comparison(orig, rep, op, rp)
            self.ref_ab_panel.show()
            self.ref_results.show()
            self.ref_status.setText("A/B repair comparison loaded under Reference Match.")
        elif op and rp:
            self.ref_status.setText("Paths ready - click Compare Tracks for full analysis.")
        else:
            self._prepare_reference_match()

    # == A/B Test ==============================================

    def _ab_setup(self, original: str | None, repaired: str):
        """Activate dual-deck A/B panel: shared playhead, HQ crossfade switch."""
        orig_name = Path(original or "original").stem[:20] if original else "Original"
        rep_name = Path(repaired).stem[:20]
        self.ab_btn_a.setText(f"A: {orig_name}")
        self.ab_btn_b.setText(f"B: {rep_name}")
        self.ab_btn_a.setChecked(True)
        self._ab_active = "a"
        self._ab_original_path = original
        self._ab_repaired_path = repaired
        try:
            self._ab_dual.stop()
        except Exception:
            pass
        if original and Path(original).is_file() and repaired and Path(repaired).is_file():
            self._ab_dual.setSources(
                str(Path(original).resolve()),
                str(Path(repaired).resolve()),
            )
        self._ab_dual.setSide("a")
        self._ab_dual.setVolume(1.0)
        self.ab_play_btn.setText("Play")
        self.ab_status.setText(
            "A/B ready · HiFi dual-deck · shared clock · crossfade switch (no quality loss)"
        )
        self.ab_panel.show()
        try:
            if hasattr(self, "_wf_canvas") and hasattr(self._wf_canvas, "set_lookahead"):
                self._wf_canvas.set_lookahead(self._ab_lookahead_sec)
        except Exception:
            pass

    def _ab_select(self, side: str):
        """Instant A/B: cosine crossfade — both decks stay sample-locked."""
        self._ab_active = side
        self.ab_btn_a.setChecked(side == "a")
        self.ab_btn_b.setChecked(side == "b")
        try:
            self._ab_dual.setSide(side)
        except Exception:
            pass
        path = self._ab_original_path if side == "a" else self._ab_repaired_path
        label = "A ORIGINAL" if side == "a" else "B REPAIRED"
        if path:
            self.ab_status.setText(
                f"{label} · {Path(path).name} · HiFi · lookahead {self._ab_lookahead_sec:.2f}s"
            )
        if self._telemetry:
            try:
                self._telemetry.ab_switch(side)
            except Exception:
                pass

    def _ab_toggle_play(self):
        """Play/pause the dual HiFi stream (both decks, one device)."""
        playing = self._ab_dual.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        if playing:
            self._ab_dual.pause()
            self._ab_sync_timer.stop()
            self.ab_play_btn.setText("Play")
            self.ab_status.setText("Paused (both decks locked).")
            return
        oa = self._ab_original_path
        rb = self._ab_repaired_path
        if not oa or not Path(str(oa)).is_file():
            self.ab_status.setText("Original path missing — analyze + repair first.")
            return
        if not rb or not Path(str(rb)).is_file():
            self.ab_status.setText("Repaired path missing — run repair first.")
            return
        # Exclusive: stop Home / Reference / Studio so only mini A/B plays
        self._stop_all_audio(except_source="mini_ab")
        self._ab_dual.setSources(str(Path(oa).resolve()), str(Path(rb).resolve()))
        self._ab_dual.setSide(self._ab_active)
        self._ab_dual.setVolume(1.0)
        self._ab_dual.play()
        self._ab_sync_timer.start()
        self.ab_play_btn.setText("Pause")
        self.ab_status.setText(
            f"Playing HiFi dual · hearing {'A' if self._ab_active == 'a' else 'B'} "
            f"· lookahead {self._ab_lookahead_sec:.2f}s · no resample downgrade"
        )

    def _ab_sync_decks(self):
        """Shared playhead paint only — DualHiFiPlayer is already sample-locked."""
        try:
            if self._ab_dual.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
                return
            pos = int(self._ab_dual.position())
            if hasattr(self, "waveform_panel") and hasattr(self.waveform_panel, "set_position_ms"):
                dur = int(self._ab_dual.duration() or 0)
                self.waveform_panel.set_position_ms(pos, dur)
        except Exception:
            pass

    def _ab_update_position(self, position: int):
        try:
            if hasattr(self, "waveform_panel") and hasattr(self.waveform_panel, "set_position_ms"):
                self.waveform_panel.set_position_ms(
                    position, int(self._ab_dual.duration() or 0)
                )
        except Exception:
            pass

    def _ab_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.ab_play_btn.setText("Play")
            self._ab_sync_timer.stop()

    def _open_latest_html(self):
        reports = list((PROJECT_ROOT / "reports" / "html").glob("*.html"))
        if reports:
            os.startfile(str(max(reports, key=lambda p: p.stat().st_mtime)))
        else:
            QMessageBox.information(self, "No reports", "Run an analysis first.")

    def _on_export(self, fmt: str):
        """Open the latest report in the requested format."""
        ext_map = {"html": "html", "json": "json", "txt": "txt"}
        ext = ext_map.get(fmt, "html")
        reports = list((PROJECT_ROOT / "reports" / ext).glob(f"*.{ext}"))
        if reports:
            os.startfile(str(max(reports, key=lambda p: p.stat().st_mtime)))
        else:
            QMessageBox.information(self, "No reports", f"No {fmt.upper()} reports found. Run an analysis first.")

    # == Update Views =========================================

    def _show_dashboard(self):
        self._navigate(0)
        self._refresh_dashboard()

    def _clear_metric_shells(self):
        """Reset all home metrics/charts to intentional placeholders."""
        for tile in getattr(self, "home_metric_tiles", {}).values():
            tile.clear()
        for tile in getattr(self, "home_extra_tiles", {}).values():
            tile.clear()
        if hasattr(self, "waveform_panel") and hasattr(self.waveform_panel, "clear"):
            self.waveform_panel.clear()
        elif hasattr(self, "_wf_canvas"):
            self._wf_canvas.clear()
            if hasattr(self.waveform_panel, "set_live"):
                self.waveform_panel.set_live(False)
        try:
            if hasattr(self, "_home_player"):
                self._home_player.stop()
                self._home_audio_path = None
        except Exception:
            pass
        if hasattr(self, "spectrum_panel") and hasattr(self.spectrum_panel, "clear"):
            self.spectrum_panel.clear()
        elif hasattr(self, "_sp_canvas"):
            self._sp_canvas.clear()
            self.spectrum_panel.set_live(False)
        if hasattr(self, "home_deep_body"):
            self.home_deep_body.setText(
                "-  Sample rate  ·  Bit depth  ·  Rolloff  ·  Bandwidth  ·  ZCR  ·  "
                "Onset  ·  Energy L/M/H  ·  Mono fit  ·  DC"
            )
            self.home_deep_body.setStyleSheet(
                f"font-size: {Type.CAPTION}px; font-family: {Type.MONO}; color: {Color.MUTED};"
            )

    def _load_track_meta_panel(self, report: dict | None):
        """Load cover art + tags into the Home metadata panel."""
        if not hasattr(self, "track_meta"):
            return
        if not report:
            self.track_meta.clear()
            return
        track = report.get("track") if isinstance(report.get("track"), dict) else {}
        audio = track.get("audio") if isinstance(track.get("audio"), dict) else {}
        path = None
        for key in ("path", "file_path", "source_path"):
            raw = audio.get(key) if audio else None
            if raw and Path(str(raw)).is_file():
                path = Path(str(raw))
                break
        if path is None:
            try:
                path = self._current_audio_path()
            except Exception:
                path = None
        tags = {}
        extra = track.get("extra") if isinstance(track.get("extra"), dict) else {}
        if isinstance(extra.get("tags"), dict):
            tags = dict(extra["tags"])
        self.track_meta.load_path(path, tags)

    def _on_metadata_saved(self, path: str):
        """After metadata save, refresh title from tags when present."""
        try:
            if hasattr(self, "track_meta") and self.track_meta._fields.get("title"):
                title = self.track_meta._fields["title"].text().strip()
                if title and hasattr(self, "home_track_name"):
                    self.home_track_name.setText(title[:48])
        except Exception:
            pass

    def _apply_home_metrics(self, report: dict, track: dict, m: dict, lm: dict):
        """Fill scorecards + charts with color-banded values."""
        self.home_metric_tiles["lufs"].set_value(lm.get("integrated_lufs"), "lufs")
        self.home_metric_tiles["tp"].set_value(lm.get("true_peak_dbtp"), "tp")
        self.home_metric_tiles["lra"].set_value(lm.get("loudness_range_lu"), "lra")
        self.home_metric_tiles["peak"].set_value(m.get("peak_dbfs"), "peak")
        self.home_metric_tiles["rms"].set_value(m.get("rms_dbfs"), "rms")
        self.home_metric_tiles["crest"].set_value(m.get("crest_factor"), "crest")

        extra = track.get("extra") if isinstance(track.get("extra"), dict) else {}
        lib = extra.get("librosa") if isinstance(extra.get("librosa"), dict) else {}
        faults = extra.get("technical_faults") if isinstance(extra.get("technical_faults"), dict) else {}

        self.home_extra_tiles["dr"].set_value(m.get("dynamic_range_db"), "dr")
        self.home_extra_tiles["width"].set_value(m.get("stereo_width_percent"), "width")
        phase = m.get("phase_correlation")
        if phase is None:
            phase = faults.get("phase_correlation")
        self.home_extra_tiles["phase"].set_value(phase, "phase")
        self.home_extra_tiles["noise"].set_value(m.get("noise_floor_dbfs"), "noise")
        self.home_extra_tiles["tempo"].set_value(lib.get("tempo_bpm"), "tempo")
        bright = lib.get("spectral_centroid_hz") or lib.get("brightness_score")
        self.home_extra_tiles["centroid"].set_value(bright, "centroid")
        clip = m.get("clipped_samples_estimate")
        if clip is None:
            clip = faults.get("clipped_samples")
        self.home_extra_tiles["clip"].set_value(clip, "clip")
        self.home_extra_tiles["silence"].set_value(faults.get("silence_ratio"), "silence")

        # Original / Repaired hover compare when this report is a post-repair analysis
        if getattr(self, "_repair_compare_live", False) and self._pre_repair_report:
            try:
                self._apply_repair_compare_tiles()
            except Exception:
                pass
        else:
            for tile in list(getattr(self, "home_metric_tiles", {}).values()):
                if hasattr(tile, "clear_compare"):
                    tile.clear_compare()
            for tile in list(getattr(self, "home_extra_tiles", {}).values()):
                if hasattr(tile, "clear_compare"):
                    tile.clear_compare()

        # Deep readout: library-backed fields already in the report
        audio = track.get("audio") or {}
        eb = lib.get("energy_balance") if isinstance(lib.get("energy_balance"), dict) else {}
        parts = [
            f"SR {audio.get('sample_rate_hz', '-')} Hz",
            f"Depth {audio.get('bit_depth', '-')}-bit",
            f"Ch {audio.get('channels', '-')}",
            f"Rolloff {lib.get('spectral_rolloff_hz', '-')} Hz",
            f"BW {lib.get('spectral_bandwidth_hz', '-')} Hz",
            f"ZCR {lib.get('zero_crossing_rate', '-')}",
            f"Onset {lib.get('onset_strength_mean', '-')}",
            f"Energy L/M/H {eb.get('low', '-')}/{eb.get('mid', '-')}/{eb.get('high', '-')}",
            f"Mono {faults.get('mono_compatibility', '-')}",
            f"DC {faults.get('dc_offset', '-')}",
            f"Codec {audio.get('codec_name', '-')}",
        ]
        self.home_deep_body.setText("  ·  ".join(str(p) for p in parts))
        self.home_deep_body.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-family: {Type.MONO}; color: {Color.TEXT};"
        )

        # Waveform + spectrum (report peaks or load from path via current libs)
        path = None
        for key in ("path", "file_path", "source_path"):
            raw = audio.get(key) if isinstance(audio, dict) else None
            if raw and Path(str(raw)).is_file():
                path = Path(str(raw))
                break

        peaks = m.get("waveform") if isinstance(m.get("waveform"), list) else None
        if peaks and len(peaks) > 8:
            try:
                arr = [abs(float(x)) for x in peaks]
                mx = max(arr) or 1.0
                peaks = [v / mx for v in arr]
            except Exception:
                peaks = None
        if not peaks and path:
            peaks = load_waveform_peaks(path, n_bins=360)
        dur = None
        try:
            if audio.get("duration_seconds") is not None:
                dur = float(audio["duration_seconds"])
        except Exception:
            dur = None
        if peaks:
            if hasattr(self.waveform_panel, "set_peaks"):
                self.waveform_panel.set_peaks(peaks, dur)
            else:
                self._wf_canvas.set_peaks(peaks, dur)
            self.waveform_panel.set_live(True)
            # Load into home transport player
            if path and path.is_file():
                self._home_load_audio(path)
        else:
            if hasattr(self.waveform_panel, "clear"):
                self.waveform_panel.clear()
            else:
                self._wf_canvas.clear()
                self.waveform_panel.set_live(False)

        bands = m.get("spectral_balance_db") if isinstance(m.get("spectral_balance_db"), dict) else {}
        if not bands or not any(v is not None for v in bands.values()):
            if path:
                bands = load_spectrum_bands(path)
            else:
                # Derive coarse bands from energy_balance if present
                if eb:
                    bands = {
                        "SUB": None,
                        "BASS": eb.get("low"),
                        "LOW MID": eb.get("mid"),
                        "MID": eb.get("mid"),
                        "PRES": eb.get("high"),
                        "HIGH": eb.get("high"),
                        "AIR": None,
                    }
        # Normalize keys to short labels if engine used long names
        if bands:
            nice = {}
            mapping = {
                "sub_bass": "SUB", "bass": "BASS", "low_mid": "LOW MID",
                "mid": "MID", "presence": "PRES", "high": "HIGH", "air": "AIR",
            }
            for k, v in bands.items():
                nice[mapping.get(str(k).lower(), str(k).upper()[:7])] = v
            self.spectrum_panel.set_bands(nice)
            self.spectrum_panel.set_live(True)
        else:
            self.spectrum_panel.set_bands({})
            if not path:
                self.spectrum_panel.set_live(False)
        # Spectrogram first on load (HD job starts immediately; Balance remains available)
        if path and path.is_file():
            try:
                self.spectrum_panel.set_mode("spectrogram")
                self.spectrum_panel.set_audio_path(path, auto_compute=True)
            except Exception:
                pass
        else:
            try:
                self.spectrum_panel.set_audio_path(None)
            except Exception:
                pass

    def _refresh_dashboard(self):
        # Always keep results shell visible (placeholders on first load)
        self.home_results.show()

        if not self.last_result:
            if hasattr(self, "empty_state"):
                self.empty_state.hide()
            if hasattr(self, "home_hero"):
                self.home_hero.show()
            if hasattr(self, "home_drop"):
                self.home_drop.setMinimumHeight(158)
                self.home_drop.setMaximumHeight(188)
                self.home_drop.show()
            self.home_track_name.setText("AWAITING MIX")
            self.home_verdict.setText("Drop or browse a file - metrics fill when analysis completes.")
            self.home_badge.setText("  STANDBY  ")
            self.home_badge.setStyleSheet(f"""
                background: {Color.with_alpha(Color.MUTED, 0.12)};
                color: {Color.MUTED};
                border: 1px solid {Color.with_alpha(Color.MUTED, 0.25)};
                border-radius: {Radius.PILL}px;
                padding: 6px 14px;
                font-size: 11px; font-weight: {Type.WEIGHTS['semibold']};
            """)
            self.home_badge.show()
            self.home_score_label.setText("OVERALL MIX SCORE")
            self.score_ring.set_score(None)
            self._clear_metric_shells()
            self.recs_card.set_items(None)
            self._set_repair_enabled(None)
            try:
                if hasattr(self, "track_meta"):
                    self.track_meta.clear()
            except Exception:
                pass
            self.home_scorecards_wrap.show()
            self.home_extra_wrap.show()
            self.home_charts_wrap.show()
            self.home_deep_wrap.show()
            self._refresh_recent()
            return

        if hasattr(self, "empty_state"):
            self.empty_state.hide()
        if hasattr(self, "home_hero"):
            self.home_hero.hide()
        if hasattr(self, "home_drop"):
            # Slightly compact after analysis; keep luxury strip intact
            self.home_drop.setMinimumHeight(140)
            self.home_drop.setMaximumHeight(170)
            self.home_drop.show()

        report = self.last_result
        is_doctor = report.get("report_type") == "doctor" or getattr(self, "_analysis_mode", "") == "doctor"
        score = report.get("score")
        rating = report.get("rating", "") or score_rating(score)
        summary = report.get("summary", "")
        track = report.get("track") if isinstance(report.get("track"), dict) else {}
        track = track or {}
        batch_tracks = report.get("tracks") or []

        if is_doctor:
            fname = "System Doctor"
            m, lm = {}, {}
        elif not track and batch_tracks:
            fname = f"Batch ({len(batch_tracks)} files)"
            m, lm = {}, {}
        else:
            audio = track.get("audio") or {}
            m = track.get("metrics") or {}
            lm = (m.get("loudness") or {}) if isinstance(m, dict) else {}
            fname = (audio.get("file_name") if isinstance(audio, dict) else None) or "Your Mix"

        if is_doctor:
            self.home_track_name.setText("SYSTEM DOCTOR")
        elif fname and not str(fname).startswith("Batch"):
            self.home_track_name.setText(Path(str(fname)).stem)
        else:
            self.home_track_name.setText(str(fname))
        self.home_verdict.setText(summary or rating)
        # Cover art + editable metadata for the analyzed file
        try:
            self._load_track_meta_panel(report if not is_doctor else None)
        except Exception:
            pass

        if is_doctor:
            if score is not None and int(score) >= 100:
                badge_text, badge_ok = "  ALL CHECKS PASSED  ", "success"
            elif score is not None and int(score) >= 70:
                badge_text, badge_ok = "  ACTION REQUIRED  ", "warn"
            else:
                badge_text, badge_ok = "  DIAGNOSTICS FAILED  ", "err"
        elif score is not None and int(score) >= 90:
            badge_text, badge_ok = "  RELEASE READY  ", "success"
        elif score is not None and int(score) >= 75:
            badge_text, badge_ok = "  GOOD - MINOR CORRECTIONS  ", "accent"
        elif score is not None:
            badge_text, badge_ok = "  NEEDS WORK  ", "warn"
        else:
            badge_text, badge_ok = "  STANDBY  ", "muted"

        if badge_ok == "success":
            c = Color.SUCCESS
        elif badge_ok == "accent":
            c = Color.ACCENT_SOFT
        elif badge_ok == "err":
            c = Color.ERROR
        elif badge_ok == "muted":
            c = Color.MUTED
        else:
            c = Color.WARNING
        self.home_badge.setText(badge_text)
        self.home_badge.setStyleSheet(f"""
            background: {Color.with_alpha(c, 0.12)};
            color: {c};
            border: 1px solid {Color.with_alpha(c, 0.3)};
            border-radius: {Radius.PILL}px;
            padding: 6px 14px;
            font-size: 11px; font-weight: {Type.WEIGHTS['semibold']};
        """)
        self.home_badge.show()

        self.home_score_label.setText("SYSTEM HEALTH" if is_doctor else "OVERALL MIX SCORE")
        self.score_ring.set_score(int(score) if score is not None else None, rating)
        self.metrics_bar.update_metrics(track if not is_doctor else None)
        if not is_doctor:
            try:
                self._set_repair_enabled(int(score) if score is not None else None)
            except (TypeError, ValueError):
                self._set_repair_enabled(None)
        else:
            self._set_repair_enabled(None)

        if is_doctor:
            self.home_scorecards_wrap.hide()
            self.home_extra_wrap.hide()
            self.home_charts_wrap.hide()
            self.home_deep_wrap.hide()
            ops = report.get("operations") or []
            lines = []
            for op in ops:
                name = op.get("operation") or op.get("check") or "Check"
                status = str(op.get("status", "")).lower()
                detail = op.get("detail") or ""
                mark = "✓" if status == "pass" else "✗"
                lines.append(f"{mark}  {name}" + (f"  -  {detail}" if detail else ""))
            self.recs_card.set_items(
                lines or ["No diagnostic checks returned."],
                header="Diagnostic Checks",
            )
        else:
            self.home_scorecards_wrap.show()
            self.home_extra_wrap.show()
            self.home_charts_wrap.show()
            self.home_deep_wrap.show()
            self._apply_home_metrics(report, track, m if isinstance(m, dict) else {}, lm if isinstance(lm, dict) else {})

            repairs = report.get("repairs", []) or []
            # Rebuild live FFmpeg commands from current path + detection (stale reports
            # often have empty/wrong commands; terminal buttons need the real suggested cmd)
            try:
                repairs = self._live_repair_dicts(report) or repairs
            except Exception as exc:
                print("live repairs:", exc)
            ref = report.get("reference_match") or {}
            findings = report.get("findings") or []

            if repairs and not (score is not None and int(score) >= 100):
                self.recs_card.set_repairs(repairs[:6])
            else:
                recs = list(ref.get("recommendations", []) or [])
                recs += [f.get("title", "") for f in findings[:3] if f.get("title")]
                if score is not None and int(score) >= 100:
                    recs = ["Technical score is maxed. Automated repair not needed."] + recs
                self.recs_card.set_items(recs[:4] or ["No specific actions needed."])
            try:
                self._update_auto_repair_plan(report)
            except Exception:
                pass

        self._refresh_recent()

    def _refresh_recent(self):
        items = []
        if self.last_result and self.last_result.get("report_type") != "doctor":
            track = self.last_result.get("track") if isinstance(self.last_result.get("track"), dict) else {}
            audio = (track or {}).get("audio") or {}
            fname = audio.get("file_name") if isinstance(audio, dict) else None
            try:
                title = Path(str(fname or "Mix")).stem
            except Exception:
                title = "Mix"
            items.append({
                "title": title[:24],
                "score": str(self.last_result.get("score", "-")),
                "date": "just now",
                "data": self.last_result,
                "path": (audio or {}).get("path") if isinstance(audio, dict) else None,
            })
        # Prefer stored recent entries (with path/data for Studio Player)
        for it in self.recent.items:
            if it.get("data") or it.get("path"):
                items.append(it)
            if len(items) >= 4:
                break
        # Fill remaining slots without dup titles
        if len(items) < 4:
            seen = {str(i.get("title")) for i in items}
            for it in self.recent.items:
                if str(it.get("title")) in seen:
                    continue
                items.append(it)
                seen.add(str(it.get("title")))
                if len(items) >= 4:
                    break

        for i, card in enumerate(self.recent_cards):
            if i < len(items):
                item = items[i]
                score = item.get("score", "-") or "-"
                sc_str = f"{score}/100" if score != "-" else "-"
                payload = item.get("data") or item
                if item.get("path") and isinstance(payload, dict) and not payload.get("path"):
                    payload = dict(payload)
                    payload["path"] = item.get("path")
                card._data = payload if isinstance(payload, dict) else {"path": item.get("path")}
                card.show()
                labels = card.findChildren(QLabel)
                if len(labels) >= 1:
                    labels[0].setText(item.get("title", "Mix"))
                if len(labels) >= 2:
                    labels[1].setText(f"{sc_str}  ·  {item.get('date', '')}")
            else:
                card.hide()

    def _open_recent_item(self, data: dict):
        """Restore dashboard stats for a recent card (same as history restore)."""
        report = data if isinstance(data, dict) else {}
        # If this is a full report snapshot, restore dashboard
        if report.get("track") or report.get("score") is not None or report.get("data"):
            payload = report.get("data") if isinstance(report.get("data"), dict) else report
            # If data is nested under history-style item without track, still try restore
            if isinstance(payload, dict) and (payload.get("track") or payload.get("score") is not None):
                self._restore_analysis_report(payload)
                return
        path = resolve_audio_path(report)
        if path is None:
            raw = report.get("path")
            if raw and Path(str(raw)).is_file():
                path = Path(str(raw))
        if path is None or not path.is_file():
            QMessageBox.information(
                self,
                "Analysis",
                "No saved analysis or audio path for this item.",
            )
            return
        win = StudioPlayerWindow(self, path, report if report.get("track") else report)
        win.aboutToPlay.connect(lambda: self._stop_all_audio(except_source="studio"))
        win.show()
        try:
            win.place_beside_main()
        except Exception:
            pass
        self._studio_windows.append(win)
        self._studio_windows = [w for w in self._studio_windows if w.isVisible() or w is win]

    def open_studio_player(self, path: str | Path, report: dict | None = None):
        """Public helper to open Studio Player for any path."""
        win = StudioPlayerWindow(self, path, report or {})
        try:
            win.aboutToPlay.connect(lambda: self._stop_all_audio(except_source="studio"))
        except Exception:
            pass
        win.show()
        try:
            win.place_beside_main()
        except Exception:
            pass
        self._studio_windows.append(win)

    # -- History sidebar / favorites ---------------------------------

    def _rebuild_history_sidebar(self) -> None:
        """Populate left HISTORY list: click restore · star favorite · trash delete."""
        if not hasattr(self, "_history_list_layout"):
            return
        lay = self._history_list_layout
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        items = []
        try:
            items = self.history.all_items()
        except Exception:
            items = []

        if not items:
            empty = QLabel("No analyses yet")
            empty.setStyleSheet(
                f"font-size: 11px; color: {Color.MUTED}; background: transparent; padding: 4px 2px;"
            )
            lay.addWidget(empty)
            return

        for it in items[:24]:
            eid = str(it.get("id") or "")
            title = str(it.get("title") or "Mix")[:22]
            score = it.get("score")
            sc = f"{int(score)}" if score is not None else "—"
            fav = bool(it.get("favorite"))
            date = str(it.get("date") or "")[:10]

            row = QWidget()
            row.setStyleSheet("background: transparent;")
            row.setCursor(Qt.PointingHandCursor)
            hl = QHBoxLayout(row)
            hl.setContentsMargins(2, 1, 2, 1)
            hl.setSpacing(4)

            name_btn = QPushButton(f"{title}\n{sc}/100 · {date}")
            name_btn.setCursor(Qt.PointingHandCursor)
            name_btn.setToolTip("Restore this analysis on the dashboard")
            name_btn.setStyleSheet(
                f"""
                QPushButton {{
                    text-align: left;
                    background: {Color.with_alpha(Color.SURFACE, 0.55)};
                    border: 1px solid {Color.with_alpha(Color.LINE, 0.6)};
                    border-radius: 6px;
                    padding: 6px 8px;
                    color: {Color.TEXT};
                    font-size: 11px;
                }}
                QPushButton:hover {{
                    border-color: {Color.ACCENT};
                    background: {Color.with_alpha(Color.ACCENT, 0.10)};
                }}
                """
            )
            name_btn.clicked.connect(
                lambda checked=False, _id=eid: self._restore_history_entry(_id)
            )
            hl.addWidget(name_btn, 1)

            star = QToolButton()
            star.setText("★" if fav else "☆")
            star.setToolTip("Pin to Favorites on dashboard" if not fav else "Unpin from Favorites")
            star.setCursor(Qt.PointingHandCursor)
            star.setFixedSize(26, 26)
            star.setStyleSheet(
                f"""
                QToolButton {{
                    background: transparent;
                    border: none;
                    color: {Color.ACCENT if fav else Color.MUTED};
                    font-size: 14px;
                }}
                QToolButton:hover {{ color: {Color.ACCENT_SOFT}; }}
                """
            )
            star.clicked.connect(
                lambda checked=False, _id=eid: self._toggle_history_favorite(_id)
            )
            hl.addWidget(star, 0)

            trash = QToolButton()
            trash.setText("🗑")
            trash.setToolTip("Remove from history")
            trash.setCursor(Qt.PointingHandCursor)
            trash.setFixedSize(26, 26)
            trash.setStyleSheet(
                f"""
                QToolButton {{
                    background: transparent;
                    border: none;
                    color: {Color.MUTED};
                    font-size: 12px;
                }}
                QToolButton:hover {{ color: {Color.ERROR}; }}
                """
            )
            trash.clicked.connect(
                lambda checked=False, _id=eid: self._delete_history_entry(_id)
            )
            hl.addWidget(trash, 0)

            lay.addWidget(row)

    def _restore_history_entry(self, entry_id: str) -> None:
        try:
            item = self.history.get(entry_id)
        except Exception:
            item = None
        if not item:
            return
        try:
            report = self.history.report_from_item(item)
        except Exception:
            report = item.get("data") if isinstance(item.get("data"), dict) else {}
        self._restore_analysis_report(report)

    def _restore_analysis_report(self, report: dict) -> None:
        """Bring a saved analysis back onto the dashboard with stats/UI."""
        if not isinstance(report, dict) or not report:
            return
        self.last_result = report
        self._analysis_mode = str(report.get("report_type") or "analyze")
        self._repair_compare_live = False
        try:
            self._navigate(0)
        except Exception:
            pass
        try:
            self._refresh_dashboard()
        except Exception:
            pass
        try:
            if report.get("report_type") == "reference":
                self._populate_reference(report)
            else:
                self._populate_report(report)
        except Exception:
            pass
        # Load home audio if path exists
        try:
            path = resolve_audio_path(report)
            if path and path.is_file() and hasattr(self, "_home_load_audio"):
                self._home_load_audio(path)
        except Exception:
            pass

    def _delete_history_entry(self, entry_id: str) -> None:
        try:
            self.history.delete(entry_id)
            self.recent._sync_items()
        except Exception:
            pass
        self._rebuild_history_sidebar()
        self._refresh_favorites_bar()
        try:
            self._refresh_recent()
        except Exception:
            pass

    def _toggle_history_favorite(self, entry_id: str) -> None:
        try:
            self.history.toggle_favorite(entry_id)
        except Exception:
            pass
        self._rebuild_history_sidebar()
        self._refresh_favorites_bar()

    def _refresh_favorites_bar(self) -> None:
        """Dashboard quick bar of starred history items."""
        if not hasattr(self, "_favorites_bar"):
            return
        bar = self._favorites_bar
        while bar.count():
            item = bar.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        favs = []
        try:
            favs = self.history.favorites()
        except Exception:
            favs = []

        if hasattr(self, "_favorites_empty"):
            self._favorites_empty.setVisible(not bool(favs))

        for it in favs[:8]:
            eid = str(it.get("id") or "")
            title = str(it.get("title") or "Mix")[:18]
            score = it.get("score")
            label = f"★ {title}"
            if score is not None:
                label = f"★ {title}  {int(score)}"
            btn = QPushButton(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setToolTip("Restore this favorite analysis")
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: {Color.with_alpha(Color.ACCENT, 0.12)};
                    border: 1px solid {Color.with_alpha(Color.ACCENT, 0.35)};
                    border-radius: {Radius.PILL}px;
                    padding: 6px 12px;
                    color: {Color.ACCENT_SOFT};
                    font-size: 11px;
                    font-weight: {Type.WEIGHTS.get('semibold', 600)};
                }}
                QPushButton:hover {{
                    background: {Color.with_alpha(Color.ACCENT, 0.22)};
                    border-color: {Color.ACCENT};
                }}
                """
            )
            btn.clicked.connect(
                lambda checked=False, _id=eid: self._restore_history_entry(_id)
            )
            bar.addWidget(btn)
        bar.addStretch(1)

    def _populate_report(self, report: dict):
        # Clear advanced sections
        while self.sections_area.count():
            item = self.sections_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        score = report.get("score")
        rating = report.get("rating", "") or score_rating(score)
        summary = report.get("summary", "")
        track = report.get("track") or {}
        audio = track.get("audio", {}) or {}
        m = track.get("metrics", {}) or {}
        lm = m.get("loudness", {}) or {}

        # Header
        fname = audio.get("file_name", "Mix")
        self.viewer_track_name.setText(fname)
        self.viewer_meta_line.setText(
            f"Analyzed · CoProducer Core Analyzer v{__version__}"
        )

        # Chips
        while self.viewer_chips.count():
            item = self.viewer_chips.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        chips = []
        if audio.get("duration_seconds") is not None:
            chips.append(f"Duration  {audio.get('duration_seconds')}s")
        if audio.get("sample_rate_hz"):
            chips.append(f"Sample Rate  {audio.get('sample_rate_hz')} Hz")
        if audio.get("channels"):
            chips.append(f"Channels  {audio.get('channels')}")
        if audio.get("bit_depth"):
            chips.append(f"Bit Depth  {audio.get('bit_depth')}-bit")
        if audio.get("codec_name"):
            chips.append(f"Codec  {audio.get('codec_name')}")
        if audio.get("bit_rate_bps"):
            chips.append(f"Bitrate  {int(audio.get('bit_rate_bps')/1000)} kbps")
        for c in chips:
            self.viewer_chips.addWidget(self._make_chip(c))
        self.viewer_chips.addStretch()

        # Overall score
        if score is not None:
            self.viewer_score_num.setText(str(int(score)))
            self.viewer_score_bar.setValue(int(score))
            self.viewer_quality.setText(rating)
            sc = score_color(int(score))
            self.viewer_score_num.setStyleSheet(
                f"font-size: 56px; font-weight: {Type.WEIGHTS['bold']}; "
                f"color: {sc}; background: transparent;"
            )
        else:
            self.viewer_score_num.setText("-")
            self.viewer_score_bar.setValue(0)
            self.viewer_quality.setText("")

        # Technical scorecards (mockup row)
        def fmt(v, digits=1):
            if v is None:
                return "-"
            try:
                return f"{float(v):.{digits}f}"
            except (TypeError, ValueError):
                return str(v)

        def _set_sc(key: str, val, kind: str):
            lbl = self.scorecards.get(key)
            if not lbl:
                return
            if val is None:
                lbl.setText("-")
                lbl.setStyleSheet(
                    f"font-size: 22px; font-weight: {Type.WEIGHTS['bold']}; "
                    f"color: {Color.MUTED}; background: transparent;"
                )
                return
            text = fmt(val)
            col = value_color(kind, val)
            lbl.setText(text)
            lbl.setStyleSheet(
                f"font-size: 22px; font-weight: {Type.WEIGHTS['bold']}; "
                f"font-family: {Type.DISPLAY}; color: {col}; background: transparent;"
            )

        _set_sc("lufs", lm.get("integrated_lufs"), "lufs")
        _set_sc("tp", lm.get("true_peak_dbtp"), "tp")
        _set_sc("lra", lm.get("loudness_range_lu"), "lra")
        _set_sc("peak", m.get("peak_dbfs"), "peak")
        _set_sc("rms", m.get("rms_dbfs"), "rms")
        _set_sc("crest", m.get("crest_factor"), "crest")

        # Sync report charts from same helpers as Home
        try:
            path = None
            for key in ("path", "file_path", "source_path"):
                raw = audio.get(key)
                if raw and Path(str(raw)).is_file():
                    path = Path(str(raw))
                    break
            peaks = m.get("waveform") if isinstance(m.get("waveform"), list) else None
            if peaks and len(peaks) > 8:
                arr = [abs(float(x)) for x in peaks]
                mx = max(arr) or 1.0
                peaks = [v / mx for v in arr]
            elif path:
                peaks = load_waveform_peaks(path, n_bins=360)
            if peaks:
                self._report_wf_canvas.set_peaks(peaks)
                self.report_waveform.set_live(True)
            else:
                self._report_wf_canvas.clear()
                self.report_waveform.set_live(False)
            bands = m.get("spectral_balance_db") if isinstance(m.get("spectral_balance_db"), dict) else {}
            if (not bands or not any(v is not None for v in bands.values())) and path:
                bands = load_spectrum_bands(path)
            if bands:
                mapping = {
                    "sub_bass": "SUB", "bass": "BASS", "low_mid": "LOW MID",
                    "mid": "MID", "presence": "PRES", "high": "HIGH", "air": "AIR",
                }
                nice = {mapping.get(str(k).lower(), str(k).upper()[:7]): v for k, v in bands.items()}
                self.report_spectrum.set_bands(nice)
                self.report_spectrum.set_live(True)
            else:
                self.report_spectrum.set_bands({})
                if not path:
                    self.report_spectrum.set_live(False)
            if path and path.is_file():
                self.report_spectrum.set_audio_path(path, auto_compute=True)
            else:
                self.report_spectrum.set_audio_path(None)
        except Exception:
            pass

        # Recommendations — same live FFmpeg commands as dashboard (absolute paths)
        ref = report.get("reference_match") or {}
        findings = report.get("findings") or []
        repairs = report.get("repairs", []) or []
        try:
            repairs = self._live_repair_dicts(report) or repairs
        except Exception as exc:
            print("live repairs (reports):", exc)
        recs = list(ref.get("recommendations", []) or [])
        recs += [f.get("title", "") for f in findings[:3] if f.get("title")]

        if repairs:
            # Terminal button runs the displayed ffmpeg line only (no silent dual _run_repair)
            try:
                self.viewer_recs.repairClicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.viewer_recs.set_repairs(repairs[:4])
        else:
            self.viewer_recs.set_items(recs[:4] or ["No specific actions needed."])

        # Reference match block
        sim = ref.get("similarity_score")
        if sim is not None:
            self.ref_sim_big.setText(f"{int(sim)}%")
            self.ref_sim_bar.setValue(int(sim))
            diffs = ref.get("differences") or []
            lines = []
            for d in diffs[:6]:
                met = d.get("metric", "")
                delta = d.get("delta", d.get("difference", ""))
                sev = d.get("severity", "")
                mark = "✓" if sev == "pass" else "•"
                lines.append(f"{mark}  {met}   {delta}")
            self.ref_diffs_label.setText("\n".join(lines) if lines else "No comparison metrics.")
        else:
            self.ref_sim_big.setText("-")
            self.ref_sim_bar.setValue(0)
            self.ref_diffs_label.setText("Run Reference Match to compare against a professional track.")

        # Technical summary
        extra = track.get("extra") if isinstance(track.get("extra"), dict) else {}
        faults = extra.get("technical_faults") or {}
        lib = extra.get("librosa") or {}
        tech_parts = [
            f"Clipped Samples  {m.get('clipped_samples_estimate', '-')}",
            f"Noise Floor  {fmt(m.get('noise_floor_dbfs'))} dBFS",
            f"Dynamic Range  {fmt(m.get('dynamic_range_db'))} dB",
            f"Phase Correlation  {fmt(m.get('phase_correlation'), 2)}",
            f"Stereo Width  {fmt(m.get('stereo_width_percent'), 1)}%",
        ]
        if faults.get("dc_offset") is not None:
            tech_parts.insert(0, f"DC Offset  {faults.get('dc_offset')}")
        if faults.get("silence_ratio") is not None:
            tech_parts.append(f"Silence Ratio  {faults.get('silence_ratio')}")
        if lib.get("tempo_bpm") is not None:
            tech_parts.append(f"Tempo  {lib.get('tempo_bpm')} BPM")
        self.tech_summary_label.setText("\n".join(tech_parts))

        # Analysis info
        info_lines = [
            f"Engine  CoProducer Core Analyzer",
            f"Version  v{report.get('version', __version__)}",
            f"Mode  {report.get('report_type', '-')}",
            f"Run ID  {report.get('run_id', '-')}",
            f"Generated  {report.get('generated_at', '-')}",
        ]
        self.analysis_info_label.setText("\n".join(info_lines))

        # Advanced collapsible
        if findings:
            self._add_section(f"Findings ({len(findings)})", self._build_findings_widget(findings), expanded=False)
        if track:
            self._add_section("Full Technical Analysis", self._build_technical_widget(track), expanded=False)
        streaming = report.get("streaming_analysis") or {}
        if streaming.get("platforms"):
            self._add_section("Streaming Readiness", self._build_streaming_widget(streaming), expanded=False)
        self._add_section("Advanced Details", self._build_advanced_widget(report), expanded=False)

    def _build_overview_widget(self, summary: str, score: int | None) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: transparent;")
        l = QVBoxLayout(w)
        l.setContentsMargins(Space.XL, Space.SM, Space.XL, Space.LG)
        l.setSpacing(Space.SM)

        lbl = QLabel(summary or "Analysis complete.")
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size: {Type.BODY}px; color: {Color.TEXT}; line-height: 1.5; background: transparent; border: none;")
        l.addWidget(lbl)

        badge = StatusBadge(score_rating(score) if score else "No Score",
                            "success" if score and score >= 75 else "warning" if score and score >= 40 else "error",
                            size="lg")
        l.addWidget(badge)
        return w

    def _populate_reference(self, report: dict):
        # Stay on Reference Match page and force results visible (no stuck opacity)
        try:
            self.main_area.setCurrentIndex(2)
            self._update_nav_styles(2)
        except Exception:
            pass
        # Clear any leftover fade/opacity effect that can hide this panel forever
        try:
            prev = getattr(self.ref_results, "_fade_anim", None)
            if prev is not None:
                prev.stop()
            self.ref_results.setGraphicsEffect(None)
        except Exception:
            pass
        self.ref_results.setVisible(True)
        self.ref_results.show()
        try:
            fade_in(self.ref_results)
        except Exception:
            self.ref_results.setGraphicsEffect(None)
            self.ref_results.show()
        if hasattr(self, "ref_status"):
            self.ref_status.setText("Comparison complete. Building full A/B readout...")

        track = report.get("track") or {}
        ref_track = report.get("reference_track") or {}
        a_name = Path(track.get("audio", {}).get("file_name", "Your Mix")).stem
        b_name = Path(ref_track.get("audio", {}).get("file_name", "Reference")).stem
        a_audio = track.get("audio", {}) or {}
        b_audio = ref_track.get("audio", {}) or {}
        a_info = f"{a_audio.get('sample_rate_hz', '-')} Hz · {a_audio.get('bit_depth', '-')}-bit"
        b_info = f"{b_audio.get('sample_rate_hz', '-')} Hz · {b_audio.get('bit_depth', '-')}-bit"
        self.ref_track_a.set_track(a_name, a_info)
        self.ref_track_b.set_track(b_name, b_info)

        # Keep zone labels in sync with what was compared
        if a_audio.get("path"):
            self._set_ref_slot("mix", str(a_audio.get("path")), "Your mix")
        if b_audio.get("path"):
            self._set_ref_slot("ref", str(b_audio.get("path")), "Reference")

        ref_match = report.get("reference_match") or {}
        sim = ref_match.get("similarity_score", "-")
        try:
            sim_i = int(sim) if sim is not None and sim != "-" else None
        except (TypeError, ValueError):
            sim_i = None
        c = score_color(sim_i)
        self.ref_sim_score.setText(str(sim if sim is not None else "-"))
        self.ref_sim_score.setStyleSheet(
            f"font-size: {Type.DISPLAY_XL}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {c}; background: transparent;"
        )

        # Clear engine diffs
        while self.ref_diffs.count():
            item = self.ref_diffs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        # Full side-by-side variables for every detectable metric
        while self.ref_var_rows.count():
            item = self.ref_var_rows.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        ma = (track.get("metrics") or {}) if isinstance(track.get("metrics"), dict) else {}
        mb = (ref_track.get("metrics") or {}) if isinstance(ref_track.get("metrics"), dict) else {}
        la = (ma.get("loudness") or {}) if isinstance(ma.get("loudness"), dict) else {}
        lb = (mb.get("loudness") or {}) if isinstance(mb.get("loudness"), dict) else {}
        ea = (track.get("extra") or {}) if isinstance(track.get("extra"), dict) else {}
        eb = (ref_track.get("extra") or {}) if isinstance(ref_track.get("extra"), dict) else {}
        lib_a = (ea.get("librosa") or {}) if isinstance(ea.get("librosa"), dict) else {}
        lib_b = (eb.get("librosa") or {}) if isinstance(eb.get("librosa"), dict) else {}
        fa = (ea.get("technical_faults") or {}) if isinstance(ea.get("technical_faults"), dict) else {}
        fb = (eb.get("technical_faults") or {}) if isinstance(eb.get("technical_faults"), dict) else {}

        pairs = [
            ("lufs", "LOUDNESS (LUFS)", la.get("integrated_lufs"), lb.get("integrated_lufs")),
            ("tp", "TRUE PEAK", la.get("true_peak_dbtp"), lb.get("true_peak_dbtp")),
            ("lra", "LRA", la.get("loudness_range_lu"), lb.get("loudness_range_lu")),
            ("peak", "PEAK dBFS", ma.get("peak_dbfs"), mb.get("peak_dbfs")),
            ("rms", "RMS dBFS", ma.get("rms_dbfs"), mb.get("rms_dbfs")),
            ("crest", "CREST", ma.get("crest_factor"), mb.get("crest_factor")),
            ("dr", "DYNAMIC RANGE", ma.get("dynamic_range_db"), mb.get("dynamic_range_db")),
            ("width", "STEREO WIDTH %", ma.get("stereo_width_percent"), mb.get("stereo_width_percent")),
            ("phase", "PHASE CORR", ma.get("phase_correlation"), mb.get("phase_correlation")),
            ("noise", "NOISE FLOOR", ma.get("noise_floor_dbfs"), mb.get("noise_floor_dbfs")),
            ("clip", "CLIPPING", ma.get("clipped_samples_estimate") if ma.get("clipped_samples_estimate") is not None else fa.get("clipped_samples"),
             mb.get("clipped_samples_estimate") if mb.get("clipped_samples_estimate") is not None else fb.get("clipped_samples")),
            ("tempo", "TEMPO BPM", lib_a.get("tempo_bpm"), lib_b.get("tempo_bpm")),
            ("centroid", "BRIGHTNESS Hz", lib_a.get("spectral_centroid_hz"), lib_b.get("spectral_centroid_hz")),
            ("sr", "SAMPLE RATE", a_audio.get("sample_rate_hz"), b_audio.get("sample_rate_hz")),
            ("bit_depth", "BIT DEPTH", a_audio.get("bit_depth"), b_audio.get("bit_depth")),
            ("channels", "CHANNELS", a_audio.get("channels"), b_audio.get("channels")),
        ]
        for kind, label, va, vb in pairs:
            row = MetricCompareRow(label)
            row.set_pair(va, vb, kind)
            self.ref_var_rows.addWidget(row)

        for d in (ref_match.get("differences", []) or []):
            self.ref_diffs.addWidget(DiffCard(
                d.get("metric", ""),
                d.get("delta"),
                d.get("user_value", "-"),
                d.get("reference_value", "-"),
                d.get("severity", "pass"),
            ))

        recs = ref_match.get("recommendations", [])
        self.ref_recs_body.setText("\n\n".join(recs) if recs else "Track is reasonably close to reference.")

        # Exhaustive A/B panel — resolve absolute paths for both decks
        def _audio_path(audio: dict, fallback) -> str | None:
            for key in ("path", "file_path", "source_path", "absolute_path"):
                raw = (audio or {}).get(key) if isinstance(audio, dict) else None
                if raw and Path(str(raw)).is_file():
                    return str(Path(str(raw)).resolve())
            if fallback and Path(str(fallback)).is_file():
                return str(Path(str(fallback)).resolve())
            return None

        path_a = _audio_path(a_audio, self._ref_mix)
        path_b = _audio_path(b_audio, self._ref_track)
        # Per-track scores when present (not the overall similarity score on report)
        track_score_a = track.get("score") if isinstance(track, dict) else None
        track_score_b = ref_track.get("score") if isinstance(ref_track, dict) else None
        report_a = {
            "score": track_score_a if track_score_a is not None else report.get("score"),
            "rating": track.get("rating") if isinstance(track, dict) else report.get("rating"),
            "summary": (track.get("summary") if isinstance(track, dict) else None)
            or report.get("summary")
            or "",
            "track": track,
        }
        report_b = {
            "score": track_score_b,
            "rating": (ref_track.get("rating") if isinstance(ref_track, dict) else "") or "",
            "summary": (ref_track.get("summary") if isinstance(ref_track, dict) else None)
            or "Reference side",
            "track": ref_track,
        }
        labels = ("YOUR MIX", "REFERENCE")
        # Original vs repaired pair when both exist and match slots
        if self._pre_repair_report and self._post_repair_report:
            try:
                if path_a and self._ab_original_path and Path(str(path_a)).resolve() == Path(str(self._ab_original_path)).resolve():
                    report_a = self._pre_repair_report
                    path_a = self._ab_original_path or path_a
                if path_b and self._ab_repaired_path and Path(str(path_b)).resolve() == Path(str(self._ab_repaired_path)).resolve():
                    report_b = self._post_repair_report
                    path_b = self._ab_repaired_path or path_b
                    labels = ("ORIGINAL", "REPAIRED")
            except Exception:
                pass

        if hasattr(self, "ref_ab_panel"):
            self.ref_ab_panel.setGraphicsEffect(None)
            self.ref_ab_panel.setVisible(True)
            self.ref_ab_panel.show()
            self.ref_ab_panel.setMinimumHeight(720)
            try:
                self.ref_ab_panel.set_labels(labels[0], labels[1])
            except Exception:
                pass
            try:
                QApplication.processEvents()
                self.ref_ab_panel.set_comparison(report_a, report_b, path_a, path_b)
            except Exception as exc:
                print("ref_ab_panel set_comparison:", exc)
            try:
                self.ref_ab_panel.updateGeometry()
                self.ref_results.updateGeometry()
                self.ref_results.show()
            except Exception:
                pass

        if hasattr(self, "compare_btn"):
            self.compare_btn.setEnabled(True)
        if hasattr(self, "ref_status"):
            self.ref_status.setText("Comparison complete. Full A/B readout below.")
        # Final visibility guarantee after heavy A/B layout
        self.ref_results.setGraphicsEffect(None)
        self.ref_results.setVisible(True)
        self.ref_results.show()


    # == Secret producer menu (title-bar logo) ==================

    def _open_producer_menu(self, anchor: QWidget):
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        def act(label: str, slot, tip: str = ""):
            a = QAction(label, self)
            if tip:
                a.setToolTip(tip)
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        act("Open repairs folder", self._menu_open_repairs,
            "exports/repairs - FFmpeg repair outputs")
        act("Open reports folder", self._menu_open_reports,
            "HTML / JSON / TXT analysis reports")
        act("Open latest HTML report", self._open_latest_html)
        act("Reveal last mix in Explorer", self._menu_reveal_last_mix)
        act("Open Studio Player…", self._menu_open_studio,
            "Inspect / play / trim / convert any audio file")
        act("Batch analyze folder…", self._menu_open_batch,
            "Analyze every audio file in a folder → scored table + batch report")
        menu.addSeparator()
        act("Run System Doctor", self._run_doctor)
        act("FFmpeg / FFprobe status", self._menu_ffmpeg_status)
        act("Open project root", lambda: _open_path(PROJECT_ROOT))
        act("Open logs folder", self._menu_open_logs)
        menu.addSeparator()
        act("License status…", self._menu_license_status,
            "Activated email, source and license code")
        act("Deactivate license…", self._menu_deactivate_license,
            "Remove the local activation from this machine")
        menu.addSeparator()

        # Brand skins submenu (from NoDAW / MyAIPlug brand docs)
        skin_menu = menu.addMenu("Brand skin")
        skin_menu.setToolTipsVisible(True)
        current = current_skin_id()
        for skin in list_skins():
            a = QAction(f"{skin['name']}", self)
            a.setCheckable(True)
            a.setChecked(skin["id"] == current)
            a.setToolTip(f"{skin.get('group', '')}: {skin.get('blurb', '')}")
            sid = skin["id"]
            a.triggered.connect(lambda checked=False, s=sid: self._set_skin(s))
            skin_menu.addAction(a)

        menu.addSeparator()
        aot = QAction("Always on top", self)
        aot.setCheckable(True)
        aot.setChecked(self._always_on_top)
        aot.triggered.connect(self._menu_toggle_on_top)
        menu.addAction(aot)
        act("Reset window size", self._menu_reset_geometry)
        menu.addSeparator()
        act(f"About CoProducer v{__version__}", self._menu_about)

        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _skin_settings_path(self) -> Path:
        return PROJECT_ROOT / "config" / "ui_skin.json"

    def _load_skin_pref(self) -> str:
        path = self._skin_settings_path()
        try:
            if path.is_file():
                data = json.loads(path.read_text(encoding="utf-8"))
                sid = data.get("skin_id") or DEFAULT_SKIN
                if get_skin(sid):
                    return sid
        except Exception:
            pass
        return DEFAULT_SKIN

    def _save_skin_pref(self, skin_id: str) -> None:
        path = self._skin_settings_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps({"skin_id": skin_id}, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _sync_title_skin_label(self) -> None:
        skin = get_skin(current_skin_id())
        if hasattr(self, "title_bar") and hasattr(self.title_bar, "title_lbl"):
            self.title_bar.title_lbl.setText(
                f"{PRODUCT_NAME}  ·  {skin['name']}"
            )

    def _set_skin(self, skin_id: str) -> None:
        """Live skin switch - rebuild chrome so all tokens apply; keep analysis."""
        if skin_id == current_skin_id():
            return
        # Preserve full session state across full UI rebuild
        saved = self.last_result
        mode = getattr(self, "_analysis_mode", "analyze")
        ref_mix, ref_track = self._ref_mix, self._ref_track
        pre_rep = getattr(self, "_pre_repair_report", None)
        post_rep = getattr(self, "_post_repair_report", None)
        ab_o = getattr(self, "_ab_original_path", None)
        ab_r = getattr(self, "_ab_repaired_path", None)
        repair_live = getattr(self, "_repair_compare_live", False)
        last_rin = getattr(self, "_last_repair_in", None)
        last_rout = getattr(self, "_last_repair_out", None)
        home_eq = getattr(self, "_home_eq_path", None)
        home_clean = getattr(self, "_home_source_clean", None)

        self._skin_id = apply_skin(skin_id)
        self._save_skin_pref(self._skin_id)
        self._apply_theme()
        QApplication.instance().setStyleSheet(dialog_stylesheet())

        old = self.centralWidget()
        self.setCentralWidget(self._build_ui())
        if old is not None:
            old.deleteLater()

        self.last_result = saved
        self._analysis_mode = mode
        self._ref_mix, self._ref_track = ref_mix, ref_track
        self._pre_repair_report = pre_rep
        self._post_repair_report = post_rep
        self._ab_original_path = ab_o
        self._ab_repaired_path = ab_r
        self._repair_compare_live = repair_live
        self._last_repair_in = last_rin
        self._last_repair_out = last_rout
        self._home_eq_path = home_eq
        self._home_source_clean = home_clean

        # Re-wire dual A/B if UI rebuilt (players live on window, not chrome)
        try:
            self._show_dashboard()
        except Exception:
            pass
        try:
            self._rebuild_history_sidebar()
            self._refresh_favorites_bar()
        except Exception:
            pass
        if saved:
            try:
                self._restore_analysis_report(saved)
            except Exception:
                try:
                    self._refresh_dashboard()
                except Exception:
                    pass
            # Restore reference paths into drop zones if present
            try:
                if ref_mix and hasattr(self, "ref_mix_zone"):
                    self._set_ref_slot("mix", ref_mix)
                if ref_track and hasattr(self, "ref_track_zone"):
                    self._set_ref_slot("ref", ref_track)
            except Exception:
                pass
        else:
            try:
                self._refresh_recent()
            except Exception:
                pass
        self._sync_title_skin_label()

    def _menu_open_studio(self):
        path = resolve_audio_path(self.last_result) if self.last_result else None
        if path is None or not path.is_file():
            picked, _ = QFileDialog.getOpenFileName(
                self, "Open in Studio Player",
                filter="Audio (*.wav *.mp3 *.flac *.m4a *.aac *.ogg *.aiff *.aif)",
            )
            if not picked:
                return
            path = Path(picked)
        self.open_studio_player(path, self.last_result if self.last_result else {})

    def _menu_open_batch(self):
        from nodaw.ui.batch_window import BatchAnalyzeDialog

        dlg = BatchAnalyzeDialog(self)
        dlg.exec()

    def _menu_open_repairs(self):
        p = PROJECT_ROOT / "exports" / "repairs"
        p.mkdir(parents=True, exist_ok=True)
        _open_path(p)

    def _menu_open_reports(self):
        p = PROJECT_ROOT / "reports"
        p.mkdir(parents=True, exist_ok=True)
        _open_path(p)

    def _menu_open_logs(self):
        p = PROJECT_ROOT / "logs"
        p.mkdir(parents=True, exist_ok=True)
        _open_path(p)

    def _menu_reveal_last_mix(self):
        track = (self.last_result or {}).get("track") if isinstance(self.last_result, dict) else None
        audio = (track or {}).get("audio") if isinstance(track, dict) else {}
        # Prefer path fields if present
        for key in ("path", "file_path", "source_path"):
            raw = (audio or {}).get(key) if isinstance(audio, dict) else None
            if raw and Path(str(raw)).exists():
                folder = Path(str(raw)).parent
                # Explorer select
                subprocess.Popen(["explorer", "/select,", str(Path(raw))])
                return
        # Fall back: open input/song
        song = PROJECT_ROOT / "input" / "song"
        if song.is_dir():
            _open_path(song)
        else:
            QMessageBox.information(self, "Reveal", "No analyzed mix path found yet.")

    def _menu_ffmpeg_status(self):
        lines = []
        for tool in ("ffmpeg", "ffprobe"):
            try:
                r = subprocess.run(
                    [tool, "-version"], capture_output=True, text=True, timeout=8
                )
                first = (r.stdout or r.stderr or "").splitlines()
                lines.append(first[0] if first else f"{tool}: unknown")
            except Exception as exc:
                lines.append(f"{tool}: not available ({exc})")
        QMessageBox.information(self, "FFmpeg status", "\n".join(lines))

    def _menu_toggle_on_top(self, checked: bool = False):
        self._always_on_top = bool(checked)
        f = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        if self._always_on_top:
            f |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(f)
        self.show()

    def _menu_reset_geometry(self):
        screen = QGuiApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            w = min(max(int(geo.width() * 0.68), 1120), int(geo.width() * 0.82))
            h = min(max(int(geo.height() * 0.94), 900), geo.height() - 8)
            self.showNormal()
            self.resize(w, h)
            self.move(geo.x() + (geo.width() - w) // 2, geo.y() + max(0, (geo.height() - h) // 2))

    def _menu_about(self):
        QMessageBox.information(
            self,
            "About",
            f"{PRODUCT_NAME}\n{APP_NAME}\nv{__version__}\n\n"
            f"100% local · offline mix review\n{TAGLINE}",
        )

    def _menu_license_status(self):
        st = app_license.get_license_status()
        if not st.is_valid:
            QMessageBox.information(self, "CoProducer license", "Not activated on this machine.")
            return
        lines = [
            f"Activated for: {st.email or 'unknown'}",
            f"Source: {st.source or 'unknown'}",
            f"License: {st.license_key or '-'}",
        ]
        if st.purchase_date:
            lines.append(f"Purchase date: {st.purchase_date}")
        if st.error:
            lines.append(f"Note: {st.error}")
        QMessageBox.information(self, "CoProducer license", "\n".join(lines))

    def _menu_deactivate_license(self):
        st = app_license.get_license_status()
        if not st.is_valid:
            QMessageBox.information(self, "CoProducer license", "No active license to deactivate.")
            return
        answer = QMessageBox.question(
            self,
            "Deactivate CoProducer",
            f"Remove the license for {st.email or 'this machine'}?\n\n"
            "You will need your license code again to activate.\n"
            "CoProducer will close after deactivation.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        app_license.deactivate()
        QMessageBox.information(
            self,
            "CoProducer license",
            "License removed. CoProducer will now close. Relaunch to activate again.",
        )
        QApplication.quit()


# == Entry Point ==============================================

class LicenseActivateDialog(QDialog):
    """License gate — email + access password / license key (uniform with StemSplit)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CoProducer v1 — Activate")
        self.setModal(True)
        self.setMinimumWidth(440)
        from PySide6.QtWidgets import QLineEdit, QFormLayout

        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        title = QLabel("Activate CoProducer")
        title.setStyleSheet("font-size: 18px; font-weight: 600;")
        lay.addWidget(title)
        blurb = QLabel(
            "Enter the email you purchased with and your access password or license key.\n"
            "Usage stats (session time, tracks analyzed, repairs) are stored locally."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet("color: #9ca3af;")
        lay.addWidget(blurb)
        form = QFormLayout()
        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("you@studio.com")
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("access password or license key")
        form.addRow("Email", self.email_edit)
        form.addRow("Key", self.key_edit)
        lay.addLayout(form)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.btn_activate = QPushButton("Activate")
        self.btn_activate.setObjectName("Primary")
        self.btn_activate.clicked.connect(self._activate)
        self.btn_quit = QPushButton("Quit")
        self.btn_quit.clicked.connect(self.reject)
        row.addWidget(self.btn_quit)
        row.addStretch()
        row.addWidget(self.btn_activate)
        lay.addLayout(row)

    def _activate(self):
        st = app_license.activate(self.email_edit.text(), self.key_edit.text())
        if st.activated:
            self.status.setText(st.message)
            self.accept()
        else:
            self.status.setText(st.message or "Activation failed.")


def _write_crash_log(exc: BaseException) -> Path:
    """Persist startup failures so a flashing console is not the only clue."""
    import traceback

    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "gui_crash.log"
    text = (
        f"CoProducer desktop crash\n"
        f"python={sys.version}\n"
        f"executable={sys.executable}\n"
        f"cwd={os.getcwd()}\n"
        f"project={PROJECT_ROOT}\n\n"
        f"{traceback.format_exc()}\n"
    )
    try:
        path.write_text(text, encoding="utf-8")
    except Exception:
        pass
    try:
        sys.stderr.write(text)
        sys.stderr.flush()
    except Exception:
        pass
    return path


def main():
    # Console already hidden at import time; re-assert in case something reattached
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
                ctypes.windll.kernel32.FreeConsole()
        except Exception:
            pass

    # Avoid immediate silent exit when launched without a console (shortcuts / Start-Process)
    try:
        app = QApplication(sys.argv)
    except Exception as exc:
        log = _write_crash_log(exc)
        raise SystemExit(f"QApplication failed: {exc}\nSee {log}") from exc

    app.setStyle("Fusion")
    # Load preferred skin before any widgets paint
    try:
        pref = PROJECT_ROOT / "config" / "ui_skin.json"
        if pref.is_file():
            sid = json.loads(pref.read_text(encoding="utf-8")).get("skin_id")
            if sid:
                apply_skin(sid)
    except Exception:
        apply_skin(DEFAULT_SKIN)

    app.setFont(pick_ui_font(10, QFont.Weight.Normal))
    app.setStyleSheet(dialog_stylesheet())

    # License gate (bypass: COPRODUCER_DEV_BYPASS_KEY / COPRODUCER_BETA_BYPASS=1)
    st = app_license.get_license_status()
    if not st.activated:
        dlg = LicenseActivateDialog()
        if dlg.exec() != QDialog.DialogCode.Accepted:
            raise SystemExit(0)
        st = app_license.get_license_status()
        if not st.activated:
            raise SystemExit(0)

    try:
        win = CoProducerWindow()
        # Ensure telemetry bound to activated email
        if st.email:
            if win._telemetry is None:
                win._telemetry = TelemetryStore(PROJECT_ROOT, st.email)
            else:
                win._telemetry.set_email(st.email)
        win.show()
        win.raise_()
        win.activateWindow()
    except Exception as exc:
        log = _write_crash_log(exc)
        try:
            QMessageBox.critical(
                None,
                "CoProducer failed to start",
                f"{exc}\n\nDetails written to:\n{log}",
            )
        except Exception:
            pass
        raise SystemExit(1) from exc

    code = app.exec()
    try:
        if win._telemetry:
            win._telemetry.session_end()
            win._telemetry.write_owner_report()
    except Exception:
        pass
    sys.exit(code)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        log = _write_crash_log(exc)
        # Last-resort console message when no Qt dialog is possible
        print(f"FATAL: {exc}\nSee {log}", file=sys.stderr)
        raise SystemExit(1) from exc
