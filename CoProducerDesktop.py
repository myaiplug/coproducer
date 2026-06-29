# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
CoProducer - AI Production Assistant (Desktop)
Phase 3.2 Premium PySide6 UI

This is a presentation layer only.
All analysis logic lives in the frozen engine.

Design System: app/nodaw/ui/
    theme.py       — color, typography, spacing, elevation tokens
    components.py  — reusable Card, ScoreDisplay, DropZone, Badge, etc.
    animations.py  — fade, score count utilities
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

import subprocess

from PySide6.QtCore import Qt, QThread, Signal, QObject
from PySide6.QtGui import QColor, QFont, QIcon, QPalette
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget,
)

# == Engine (frozen) ==========================================
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "app"))

try:
    from nodaw.core.engine import WorkflowRunner
    from nodaw import __version__, APP_NAME
except Exception as e:
    print("FATAL: Could not import CoProducer engine.", e)
    sys.exit(1)

# == Design System ============================================
from nodaw.ui.theme import (
    Color, Type, Space, Radius, Duration, Easing, Elevation,
    score_color, score_rating,
)
from nodaw.ui.components import (
    Card, MetricCard, RecommendationCard, DropZone,
    ScoreDisplay, VerdictBadge, StatusBadge, CollapsibleSection,
    FindingCard, EmptyState, LoadingBar, RecentCard,
    ReferenceTrackCard, DiffCard, PlatformRow,
    CircularScoreRing, BottomMetricsBar, WaveformPanel,
    SpectrumPanel, ExportCard,
)
from nodaw.ui.animations import fade_in

PRODUCT_NAME = "CoProducer"
TAGLINE = "AI Production Assistant"


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
            logger = type("DummyLogger", (), {"info": lambda *a: None, "error": lambda *a: None})()
            runner = WorkflowRunner(self.root, logger, generate_previews=False)

            self.progress.emit("Analyzing...")
            if self.mode == "analyze":
                path = self.inputs.get("song")
                report = runner.single(Path(path) if path else None)
            elif self.mode == "reference":
                song = self.inputs.get("song")
                ref = self.inputs.get("reference")
                report = runner.reference(Path(song) if song else None, Path(ref) if ref else None)
            else:
                path = self.inputs.get("song") or self.inputs.get("folder")
                report = runner.single(Path(path) if path else None)

            self.finished.emit(report)
        except Exception as exc:
            self.error.emit(str(exc))


# == Recent Manager ===========================================

class RecentManager:
    """Recent analyses storage for the dashboard."""
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.items: list[dict] = []
        if self.path.exists():
            try:
                self.items = json.loads(self.path.read_text())[:8]
            except Exception:
                self.items = []

    def add(self, item: dict):
        self.items.insert(0, item)
        self.items = self.items[:8]
        self._save()

    def _save(self):
        self.path.write_text(json.dumps(self.items, indent=2))


# == Main Window ==============================================

class CoProducerWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{PRODUCT_NAME} - {TAGLINE}")
        self.resize(1400, 900)
        self.setMinimumSize(1100, 700)

        icon_path = PROJECT_ROOT / "assets" / "icon.png"
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self._apply_theme()
        self.recent = RecentManager(PROJECT_ROOT / "reports" / "recent.json")

        self.setCentralWidget(self._build_ui())

        self.worker: Optional[AnalysisWorker] = None
        self.thread: Optional[QThread] = None
        self.last_result: Optional[dict[str, Any]] = None
        self._analysis_mode: str = "analyze"
        self._ref_mix: Optional[str] = None
        self._ref_track: Optional[str] = None

        self._show_dashboard()

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
            }}
            QMainWindow {{ background: {Color.BG}; }}
            QScrollBar:vertical {{
                background: {Color.BG}; width: 8px; border: none;
            }}
            QScrollBar::handle:vertical {{
                background: {Color.LINE}; border-radius: 4px; min-height: 40px;
            }}
            QScrollBar::handle:vertical:hover {{ background: {Color.MUTED}; }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
            QPushButton {{
                background: {Color.GLASS};
                border: 1px solid {Color.LINE};
                padding: 10px 20px;
                border-radius: {Radius.BUTTON}px;
                font-weight: {Type.WEIGHTS['medium']};
                color: {Color.TEXT};
            }}
            QPushButton:hover {{
                background: {Color.HOVER};
                border-color: {Color.ACCENT};
            }}
            QPushButton#Primary {{
                background: {Color.ACCENT};
                color: white;
                border: none;
                font-weight: {Type.WEIGHTS['semibold']};
            }}
            QPushButton#Primary:hover {{ background: {Color.with_alpha(Color.ACCENT, 0.85)}; }}
        """)

    # == Layout ===============================================

    def _build_ui(self) -> QWidget:
        root = QWidget()
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        sidebar = self._build_sidebar()
        root_layout.addWidget(sidebar, 0)

        self.main_area = QStackedWidget()
        self.dashboard = self._build_dashboard()       # index 0
        self.report_viewer = self._build_report_viewer() # index 1
        self.ref_screen = self._build_reference_screen() # index 2

        self.main_area.addWidget(self.dashboard)
        self.main_area.addWidget(self.report_viewer)
        self.main_area.addWidget(self.ref_screen)

        root_layout.addWidget(self.main_area, 1)
        return root

    # == Sidebar =============================================

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setStyleSheet(f"background: {Color.SURFACE}; border-right: 1px solid {Color.LINE};")
        side.setFixedWidth(210)

        lay = QVBoxLayout(side)
        lay.setContentsMargins(Space.XL, Space.XXL, Space.XL, Space.LG)
        lay.setSpacing(Space.XS)

        brand = QLabel("CoProducer")
        brand.setStyleSheet(f"font-size: {Type.H2}px; font-weight: {Type.WEIGHTS['bold']}; color: white; letter-spacing: -0.5px; background: transparent;")
        lay.addWidget(brand)

        tag = QLabel("AI Production Assistant")
        tag.setStyleSheet(f"font-size: {Type.CAPTION - 1}px; color: {Color.MUTED}; margin-bottom: {Space.XXL}px; background: transparent;")
        lay.addWidget(tag)

        self._nav_btns = {}
        for text, index in [("Dashboard", 0), ("Reference Match", 2), ("Reports", 1)]:
            b = self._nav_button(text, index)
            self._nav_btns[index] = b
            lay.addWidget(b)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {Color.LINE}; border: none; margin: {Space.SM}px 0;")
        lay.addWidget(sep)

        hist_btn = self._nav_button("History", 0, "history")
        self._nav_btns["history"] = hist_btn
        lay.addWidget(hist_btn)

        lay.addStretch()

        doc = QPushButton("Run Doctor")
        doc.setCursor(Qt.PointingHandCursor)
        doc.setStyleSheet(f"""
            QPushButton {{
                text-align: left; padding: 10px {Space.MD}px; font-size: {Type.CAPTION}px;
                border: none; background: transparent; border-radius: {Radius.MD}px;
                color: {Color.MUTED};
            }}
            QPushButton:hover {{ background: {Color.ELEVATED}; color: {Color.ACCENT}; }}
        """)
        doc.clicked.connect(self._run_doctor)
        lay.addWidget(doc)

        return side

    def _nav_button(self, text: str, index: int, key=None) -> QPushButton:
        b = QPushButton(text)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet(f"""
            QPushButton {{
                text-align: left; padding: 12px {Space.MD}px; font-size: {Type.BODY}px;
                border: none; background: transparent; border-radius: {Radius.MD}px;
                color: {Color.MUTED};
            }}
            QPushButton:hover {{ background: {Color.ELEVATED}; color: {Color.TEXT}; }}
        """)
        b.clicked.connect(lambda: self._navigate(index))
        return b

    def _navigate(self, index: int):
        self.main_area.setCurrentIndex(index)
        active_style = f"""
            QPushButton {{
                text-align: left; padding: 12px {Space.MD}px; font-size: {Type.BODY}px;
                border: none; background: transparent; border-radius: {Radius.MD}px;
                color: white; font-weight: {Type.WEIGHTS['semibold']};
            }}
            QPushButton:hover {{ background: {Color.ELEVATED}; color: white; }}
        """
        inactive_style = f"""
            QPushButton {{
                text-align: left; padding: 12px {Space.MD}px; font-size: {Type.BODY}px;
                border: none; background: transparent; border-radius: {Radius.MD}px;
                color: {Color.MUTED};
            }}
            QPushButton:hover {{ background: {Color.ELEVATED}; color: {Color.TEXT}; }}
        """
        for idx, btn in self._nav_btns.items():
            if isinstance(idx, int):
                btn.setStyleSheet(active_style if idx == index else inactive_style)

    # == Dashboard ============================================

    def _build_dashboard(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        outer_lay = QVBoxLayout(inner)
        outer_lay.setContentsMargins(Space.XXL, Space.XL, Space.XXL, Space.XL)
        outer_lay.setSpacing(Space.LG)

        # Loading indicator
        self.loading_bar = LoadingBar()
        outer_lay.addWidget(self.loading_bar)
        self.loading_label = QLabel("")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent;")
        self.loading_label.hide()
        outer_lay.addWidget(self.loading_label)

        # Main 2-column content
        content_row = QHBoxLayout()
        content_row.setSpacing(Space.XL)

        # ========================
        # Left Column (score + waveform + spectrum + metrics)
        # ========================
        left_col = QVBoxLayout()
        left_col.setSpacing(Space.LG)

        self.score_ring = CircularScoreRing(size=240)
        left_col.addWidget(self.score_ring, 0, Qt.AlignCenter)

        self.waveform_panel = WaveformPanel()
        left_col.addWidget(self.waveform_panel)

        self.spectrum_panel = SpectrumPanel()
        left_col.addWidget(self.spectrum_panel)

        self.metrics_bar = BottomMetricsBar()
        left_col.addWidget(self.metrics_bar)

        left_widget = QWidget()
        left_widget.setStyleSheet("background: transparent;")
        left_widget.setLayout(left_col)

        # ========================
        # Right Column (drop zone + recs + export + ab test)
        # ========================
        right_col = QVBoxLayout()
        right_col.setSpacing(Space.LG)

        # Drop zone (compact)
        drop = DropZone()
        drop.setMinimumHeight(140)
        drop.filesDropped.connect(lambda fs: self._handle_drop(fs, "analyze"))
        right_col.addWidget(drop)

        # Recommendations card
        self.recs_card = RecommendationCard()
        right_col.addWidget(self.recs_card)

        # A/B test panel (hidden until repair)
        self.ab_panel = QFrame()
        self.ab_panel.setStyleSheet(f"background: {Color.ELEVATED}; border: 1px solid {Color.LINE}; border-radius: {Radius.XL}px;")
        self.ab_panel.hide()
        ab_lay = QVBoxLayout(self.ab_panel)
        ab_lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        ab_lay.setSpacing(Space.SM)

        ab_hdr = QLabel("A/B Test: Original vs Repaired")
        ab_hdr.setStyleSheet(f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 0.3px;")
        ab_lay.addWidget(ab_hdr)

        ab_ctrl = QHBoxLayout()
        ab_ctrl.setSpacing(Space.SM)
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
                    font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['medium']}; color: {Color.TEXT};
                }}
                QPushButton:checked {{
                    background: {Color.with_alpha(Color.ACCENT, 0.15)}; border-color: {Color.ACCENT}; color: {Color.ACCENT};
                }}
                QPushButton:hover {{ background: {Color.HOVER}; }}
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
        self.ab_status.setStyleSheet(f"font-size: {Type.CAPTION - 1}px; color: {Color.MUTED}; background: transparent; border: none;")
        ab_lay.addWidget(self.ab_status)
        right_col.addWidget(self.ab_panel)

        # Export card
        self.export_card = ExportCard()
        self.export_card.exportRequested.connect(self._on_export)
        right_col.addWidget(self.export_card)

        right_col.addStretch()

        right_widget = QWidget()
        right_widget.setStyleSheet("background: transparent;")
        right_widget.setLayout(right_col)
        right_widget.setFixedWidth(320)

        # Assemble content row
        content_row.addWidget(left_widget, 1)
        content_row.addWidget(right_widget, 0)
        outer_lay.addLayout(content_row)

        # Recent analyses strip (full width)
        outer_lay.addWidget(self._section_label("Recent Analyses"))
        recent_row = QHBoxLayout()
        recent_row.setSpacing(Space.LG)
        self.recent_cards: list[RecentCard] = []
        for _ in range(4):
            card = RecentCard("--", "--", "")
            card.hide()
            recent_row.addWidget(card)
            self.recent_cards.append(card)
        outer_lay.addLayout(recent_row)

        # Batch shortcut
        batch = QPushButton(" Analyze Folder (Batch)")
        batch.setCursor(Qt.PointingHandCursor)
        batch.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: 1px dashed {Color.LINE};
                padding: {Space.MD}px {Space.XL}px; border-radius: {Radius.XL}px;
                font-size: {Type.CAPTION}px; color: {Color.MUTED};
            }}
            QPushButton:hover {{ background: {Color.HOVER}; color: {Color.ACCENT}; border-color: {Color.ACCENT}; }}
        """)
        batch.clicked.connect(lambda: self._run_analysis("analyze", {}))
        outer_lay.addWidget(batch)

        outer_lay.addStretch()
        scroll.setWidget(inner)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

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
        self.ref_mix_zone.filesDropped.connect(lambda fs: self._on_ref_drop(fs, "mix"))
        self.ref_ref_zone = DropZone("Reference Track", "Drop or click to browse")
        self.ref_ref_zone.filesDropped.connect(lambda fs: self._on_ref_drop(fs, "ref"))
        zones.addWidget(self.ref_mix_zone)
        zones.addWidget(self.ref_ref_zone)
        lay.addLayout(zones)

        # Compare button
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.compare_btn = QPushButton("Compare to Reference")
        self.compare_btn.setObjectName("Primary")
        self.compare_btn.setMinimumWidth(220)
        self.compare_btn.setMinimumHeight(44)
        self.compare_btn.setCursor(Qt.PointingHandCursor)
        self.compare_btn.clicked.connect(lambda: self._pick_and_run("reference"))
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
        self.ref_sim_card.setGraphicsEffect(None)  # our card has its own
        scl = QVBoxLayout(self.ref_sim_card)
        scl.setContentsMargins(Space.XXL, Space.XXL, Space.XXL, Space.XXL)
        scl.setAlignment(Qt.AlignCenter)

        vs_row = QHBoxLayout()
        vs_row.setSpacing(Space.XXL)
        vs_row.setAlignment(Qt.AlignCenter)

        self.ref_track_a = ReferenceTrackCard("Track A")
        self.ref_track_b = ReferenceTrackCard("Track B")

        vs_center = QVBoxLayout()
        vs_center.setAlignment(Qt.AlignCenter)
        vs_center.setSpacing(Space.XS)
        self.ref_sim_score = QLabel("—")
        self.ref_sim_score.setAlignment(Qt.AlignCenter)
        self.ref_sim_score.setStyleSheet(f"font-size: {Type.DISPLAY_XL}px; font-weight: {Type.WEIGHTS['bold']}; color: {Color.ACCENT}; background: transparent;")
        vs_center.addWidget(self.ref_sim_score)
        vs_label = QLabel("Similarity")
        vs_label.setAlignment(Qt.AlignCenter)
        vs_label.setStyleSheet(f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; color: {Color.MUTED}; background: transparent; letter-spacing: 0.5px;")
        vs_center.addWidget(vs_label)
        vs_center_w = QWidget()
        vs_center_w.setLayout(vs_center)

        vs_row.addWidget(self.ref_track_a, 1)
        vs_row.addWidget(vs_center_w, 0)
        vs_row.addWidget(self.ref_track_b, 1)
        scl.addLayout(vs_row)

        # Differences
        self.ref_diffs = QVBoxLayout()
        self.ref_diffs.setSpacing(Space.SM)

        rl.addWidget(self.ref_sim_card)
        rl.addLayout(self.ref_diffs)

        # Recommendations
        self.ref_recs = Card("elevated")
        rr_lay = QVBoxLayout(self.ref_recs)
        rr_lay.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        rr_lay.setSpacing(Space.SM)
        rr_lay.addWidget(self._section_label("Recommendations"))
        self.ref_recs_body = QLabel("")
        self.ref_recs_body.setWordWrap(True)
        self.ref_recs_body.setStyleSheet(f"font-size: {Type.BODY}px; color: {Color.TEXT}; background: transparent; border: none; line-height: 1.5;")
        rr_lay.addWidget(self.ref_recs_body)
        rl.addWidget(self.ref_recs)

        rl.addStretch()
        lay.addWidget(self.ref_results)
        lay.addStretch()

        scroll.setWidget(inner)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

    def _on_ref_drop(self, files: list[str], target: str):
        if not files:
            return
        path = files[0]
        if target == "mix":
            self._ref_mix = path
            self.ref_mix_zone.title.setText(Path(path).name[:32])
        elif target == "ref":
            self._ref_track = path
            self.ref_ref_zone.title.setText(Path(path).name[:32])

    # == Report Viewer ========================================

    def _build_report_viewer(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("background: transparent; border: none;")

        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(Space.MASSIVE, Space.HUGE, Space.MASSIVE, Space.HUGE)
        lay.setSpacing(Space.XL)

        # Header
        header = QHBoxLayout()
        header.setSpacing(Space.MD)
        header.addWidget(self._hero_title("Report"))
        self.viewer_track_name = QLabel("")
        self.viewer_track_name.setStyleSheet(f"font-size: {Type.SUBTITLE}px; color: {Color.MUTED}; font-weight: {Type.WEIGHTS['regular']}; background: transparent;")
        header.addWidget(self.viewer_track_name)
        header.addStretch()

        open_full = QPushButton("Open Full HTML")
        open_full.setCursor(Qt.PointingHandCursor)
        open_full.clicked.connect(self._open_latest_html)
        header.addWidget(open_full)

        back = QPushButton("← Dashboard")
        back.setCursor(Qt.PointingHandCursor)
        back.clicked.connect(lambda: self._navigate(0))
        header.addWidget(back)
        lay.addLayout(header)

        # Score + recs
        top = QHBoxLayout()
        top.setSpacing(Space.XL)
        self.viewer_score = ScoreDisplay()
        top.addWidget(self.viewer_score, 2)
        self.viewer_recs = RecommendationCard()
        top.addWidget(self.viewer_recs, 3)
        lay.addLayout(top)

        # Collapsible sections
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
            ("Integrated LUFS", f"{lm.get('integrated_lufs', '—')} LUFS"),
            ("True Peak", f"{lm.get('true_peak_dbtp', '—')} dBTP"),
            ("Dynamic Range", f"{m.get('dynamic_range_db', '—')} dB"),
            ("Crest Factor", f"{m.get('crest_factor', '—')}x"),
            ("Peak", f"{m.get('peak_dbfs', '—')} dBFS"),
            ("RMS", f"{m.get('rms_dbfs', '—')} dBFS"),
            ("Clipping", str(m.get('clipped_samples_estimate', '—'))),
        ]
        for label, value in rows:
            l.addWidget(MetricCard(label, value))

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {Color.LINE}; border: none; margin: {Space.SM}px {Space.XL}px;")
        l.addWidget(sep)

        audio = track.get("audio", {}) or {}
        extra = [
            ("Sample Rate", f"{audio.get('sample_rate_hz', '—')} Hz"),
            ("Bit Depth", f"{audio.get('bit_depth', '—')}-bit"),
            ("Channels", str(audio.get('channels', '—'))),
            ("Format", str(audio.get('format_name', '—'))),
            ("Codec", str(audio.get('codec_name', '—'))),
            ("Duration", f"{audio.get('duration_seconds', '—')}s"),
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

        sim = ref.get("similarity_score", "—")
        l.addWidget(MetricCard("Similarity Score", f"{sim}/100"))

        diffs = ref.get("differences", []) or []
        for d in diffs:
            l.addWidget(MetricCard(
                d.get("metric", ""),
                f"{d.get('user_value', '—')} vs {d.get('reference_value', '—')}",
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
        l.addWidget(MetricCard("Source Class", src.get("source_class", "—")))
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
            val = report.get(key, "—") or "—"
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
            song = self._ref_mix
            ref = self._ref_track
            if not song:
                song, _ = QFileDialog.getOpenFileName(self, "Select Your Mix")
            if not ref:
                ref, _ = QFileDialog.getOpenFileName(self, "Select Reference Track")
            if song and ref:
                self._run_analysis("reference", {"song": song, "reference": ref})

    def _run_analysis(self, mode: str, inputs: dict):
        self._analysis_mode = mode
        self._show_loading(True)

        self.thread = QThread()
        self.worker = AnalysisWorker(PROJECT_ROOT, mode, inputs)
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.run)
        self.worker.finished.connect(self._on_analysis_done)
        self.worker.error.connect(self._on_analysis_error)
        self.worker.progress.connect(lambda m: self.loading_label.setText(m))

        self.worker.finished.connect(self.thread.quit)
        self.worker.finished.connect(self.worker.deleteLater)
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
        self.last_result = report
        title = Path(report.get("track", {}).get("audio", {}).get("file_name", "Mix")).stem
        self.recent.add({
            "title": title[:28],
            "score": report.get("score"),
            "date": "just now",
        })
        self._refresh_dashboard()

        if self._analysis_mode == "reference":
            self._populate_reference(report)
            self._navigate(2)
        else:
            self._populate_report(report)
            self._navigate(1)

    def _on_analysis_error(self, msg: str):
        QMessageBox.critical(self, "Analysis Error", msg)
        self._show_dashboard()

    def _run_doctor(self):
        self._run_analysis("doctor", {})

    def _run_repair(self, command: str):
        """Execute an FFmpeg repair command in a background thread."""
        self._last_repair_command = command
        self.loading_label.setText("Running repair...")
        self.loading_label.show()
        self.loading_bar.start()

        def worker():
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True,
                    shell=True, timeout=300
                )
                return result
            except subprocess.TimeoutExpired:
                return None

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

        self._repair_thread = QThread()
        self._repair_worker = RepairRunner()
        self._repair_worker.moveToThread(self._repair_thread)
        self._repair_thread.started.connect(lambda: self._repair_worker.run(command))
        self._repair_worker.done.connect(self._on_repair_done)
        self._repair_worker.done.connect(self._repair_thread.quit)
        self._repair_worker.done.connect(self._repair_worker.deleteLater)
        self._repair_thread.finished.connect(self._repair_thread.deleteLater)
        self._repair_thread.start()

    def _parse_repair_paths(self, command: str) -> tuple[str | None, str | None]:
        """Extract input and output paths from an FFmpeg command."""
        parts = command.split()
        inp = None
        out = None
        i_flag = False
        for p in parts:
            stripped = p.strip("\"'")
            if p == "-i":
                i_flag = True
            elif i_flag and not p.startswith("-"):
                inp = stripped
                i_flag = False
            elif not p.startswith("-") and (p.endswith(".wav") or p.endswith(".mp3") or p.endswith(".flac") or p.endswith(".m4a")):
                out = stripped
        return inp, out

    def _on_repair_done(self, result):
        self.loading_bar.stop()
        self.loading_label.hide()
        if result is None:
            QMessageBox.warning(self, "Repair", "Repair command timed out (300s limit).")
        elif result.returncode == 0:
            cmd = getattr(self, '_last_repair_command', '')
            inp, out = self._parse_repair_paths(cmd)
            if out and Path(out).is_file():
                self._ab_original_path = inp
                self._ab_repaired_path = out
                self._ab_setup(inp, out)
                out_folder = Path(out).parent
                if out_folder.is_dir():
                    os.startfile(str(out_folder))
            QMessageBox.information(self, "Repair Complete", "Repair completed successfully.\nOutput folder opened.")
        else:
            err = (result.stderr or "").strip()[:500]
            QMessageBox.critical(self, "Repair Failed", f"Exit code {result.returncode}:\n{err}")

    # == A/B Test ==============================================

    def _ab_setup(self, original: str | None, repaired: str):
        """Activate A/B test panel after a repair."""
        orig_name = Path(original or "original").stem[:20] if original else "Original"
        rep_name = Path(repaired).stem[:20]
        self.ab_btn_a.setText(f"A: {orig_name}")
        self.ab_btn_b.setText(f"B: {rep_name}")
        self.ab_btn_a.setChecked(True)
        self._ab_active = "a"
        self._player.stop()
        self.ab_play_btn.setText("Play")
        self.ab_status.setText("Ready. Press Play to hear the original.")
        self.ab_panel.show()

    def _ab_select(self, side: str):
        self._ab_active = side
        self.ab_btn_a.setChecked(side == "a")
        self.ab_btn_b.setChecked(side == "b")
        self._player.stop()
        self.ab_play_btn.setText("Play")
        path = self._ab_original_path if side == "a" else self._ab_repaired_path
        if path and Path(path).is_file():
            self._player.setSource(Path(path).as_posix())
            self.ab_status.setText(f"Loaded: {Path(path).name}")

    def _ab_toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self.ab_play_btn.setText("Play")
        else:
            path = self._ab_original_path if self._ab_active == "a" else self._ab_repaired_path
            if path and Path(path).is_file():
                self._player.setSource(Path(path).as_posix())
                self._player.play()
                self.ab_play_btn.setText("Pause")
                name = Path(path).stem[:40]
                self.ab_status.setText(f"Playing: {name}")

    def _ab_update_position(self, position: int):
        pass

    def _ab_state_changed(self, state):
        if state == QMediaPlayer.PlaybackState.StoppedState:
            self.ab_play_btn.setText("Play")

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

    def _refresh_dashboard(self):
        if not self.last_result:
            self.score_ring.set_score(None)
            self.recs_card.set_items(None)
            self._refresh_recent()
            return

        score = self.last_result.get("score")
        rating = self.last_result.get("rating", "")
        summary = self.last_result.get("summary", "")

        self.score_ring.set_score(score, rating)
        self.metrics_bar.update_metrics(self.last_result.get("track"))

        # Show repairs with one-click FFmpeg commands when available
        repairs = self.last_result.get("repairs", []) or []
        ref = self.last_result.get("reference_match") or {}
        findings = self.last_result.get("findings") or []

        if repairs:
            try:
                self.recs_card.repairClicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.recs_card.repairClicked.connect(self._run_repair)
            self.recs_card.set_repairs(repairs[:3])
        else:
            recs = ref.get("recommendations", [])
            recs += [f.get("title", "") for f in findings[:3]]
            self.recs_card.set_items(recs[:4] or ["No specific actions needed."])

        self._refresh_recent()

    def _refresh_recent(self):
        items = []
        if self.last_result:
            title = Path(self.last_result.get("track", {}).get("audio", {}).get("file_name", "Mix")).stem
            items.append({
                "title": title[:24],
                "score": str(self.last_result.get("score", "—")),
                "date": "just now",
                "data": self.last_result,
            })
        items.extend(self.recent.items[:3])

        for i, card in enumerate(self.recent_cards):
            if i < len(items):
                item = items[i]
                score = item.get("score", "—") or "—"
                sc_str = f"{score}/100" if score != "—" else "—"
                card._data = item.get("data", {})
                card.show()
                labels = card.findChildren(QLabel)
                if len(labels) >= 1:
                    labels[0].setText(item.get("title", "Mix"))
                if len(labels) >= 2:
                    labels[1].setText(f"{sc_str}  ·  {item.get('date', '')}")
            else:
                card.hide()

    def _populate_report(self, report: dict):
        # Clear sections
        while self.sections_area.count():
            item = self.sections_area.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        score = report.get("score")
        rating = report.get("rating", "")
        summary = report.get("summary", "")
        track = report.get("track") or {}
        audio = track.get("audio", {}) or {}
        self.viewer_track_name.setText(audio.get("file_name", ""))

        self.viewer_score.set_score(score, rating, summary)

        ref = report.get("reference_match") or {}
        findings = report.get("findings") or []
        repairs = report.get("repairs", []) or []
        recs = ref.get("recommendations", [])
        recs += [f.get("title", "") for f in findings[:3]]

        if repairs:
            try:
                self.viewer_recs.repairClicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            self.viewer_recs.repairClicked.connect(self._run_repair)
            self.viewer_recs.set_repairs(repairs[:4])
        else:
            self.viewer_recs.set_items(recs[:4] or ["No specific actions needed."])

        if summary:
            self._add_section("Overview", self._build_overview_widget(summary, score), expanded=True)
        if findings:
            self._add_section(f"Findings ({len(findings)})", self._build_findings_widget(findings), expanded=True)
        if ref:
            self._add_section("Reference Match", self._build_reference_widget(ref), expanded=bool(ref.get("differences")))
        if track:
            self._add_section("Technical Analysis", self._build_technical_widget(track), expanded=False)

        streaming = report.get("streaming_analysis") or {}
        if streaming.get("platforms"):
            self._add_section("Streaming Readiness", self._build_streaming_widget(streaming), expanded=False)

        codec = report.get("codec_analysis") or {}
        if codec:
            self._add_section("Codec Analysis", self._build_codec_widget(codec), expanded=False)

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
        self.ref_results.show()
        fade_in(self.ref_results)

        track = report.get("track") or {}
        ref_track = report.get("reference_track") or {}
        a_name = Path(track.get("audio", {}).get("file_name", "Track A")).stem
        b_name = Path(ref_track.get("audio", {}).get("file_name", "Track B")).stem
        a_audio = track.get("audio", {}) or {}
        b_audio = ref_track.get("audio", {}) or {}
        a_info = f"{a_audio.get('sample_rate_hz', '—')} Hz · {a_audio.get('bit_depth', '—')}-bit"
        b_info = f"{b_audio.get('sample_rate_hz', '—')} Hz · {b_audio.get('bit_depth', '—')}-bit"
        self.ref_track_a.set_track(a_name, a_info)
        self.ref_track_b.set_track(b_name, b_info)

        ref_match = report.get("reference_match") or {}
        sim = ref_match.get("similarity_score", "—")
        c = score_color(sim if isinstance(sim, int) else None)
        self.ref_sim_score.setText(str(sim))
        self.ref_sim_score.setStyleSheet(f"font-size: {Type.DISPLAY_XL}px; font-weight: {Type.WEIGHTS['bold']}; color: {c}; background: transparent;")

        # Clear diffs
        while self.ref_diffs.count():
            item = self.ref_diffs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        for d in (ref_match.get("differences", []) or []):
            self.ref_diffs.addWidget(DiffCard(
                d.get("metric", ""),
                d.get("delta"),
                d.get("user_value", "—"),
                d.get("reference_value", "—"),
                d.get("severity", "pass"),
            ))

        recs = ref_match.get("recommendations", [])
        self.ref_recs_body.setText("\n\n".join(recs) if recs else "Track is reasonably close to reference.")


# == Entry Point ==============================================

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont(Type.FAMILY.split(",")[0].strip('"'), 10)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    app.setFont(font)

    win = CoProducerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
