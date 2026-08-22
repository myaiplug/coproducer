"""
Studio FX panel for CoProducer — modern card layout.

  ANALYSIS   artifact hunt · auto-dials de-click / de-DC / dropout edges
  CHAIN      order chips · bypass · wet/dry · LIVE badge · render / save
  BLEEDFIX   auto-dials gate from noise-floor analysis · knobs remain overridable
  EQ         6-band parametric (type · freq · gain · Q · dynamic)
  PRESETS    JSON effects · ffmpeg batch tools

Every knob change emits `liveChanged` (debounced ~24 ms) — the player pushes
the snapshot straight into the realtime engine, so audio never restarts.
`renderRequested` only fires from the explicit Render button (ffmpeg bats,
full bake) and `saveRequested` from Save.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import Color, Radius, Space, Type
from .eq_knobs import PowerLightButton

CHIP_ACCENT = Color.ACCENT
CHIP_AMBER = Color.GOLD if hasattr(Color, "GOLD") else Color.ACCENT
CHIP_ORANGE = "#ff8c50"
CHIP_CYAN = "#5bd6e8"
CHIP_GREEN = "#3ecf8e"
CHIP_MUTED = Color.MUTED


# ---------------------------------------------------------------------------
# Generic rotary FX knob
# ---------------------------------------------------------------------------

class FxKnob(QWidget):
    """Drag-up/down rotary knob. Double-click resets to default."""

    valueChanged = Signal(float)

    def __init__(
        self,
        label: str,
        *,
        minimum: float,
        maximum: float,
        default: float,
        log: bool = False,
        fmt: str = "{:.1f}",
        unit: str = "",
        color_role: str = "mid",
        compact: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self._label = label
        self._min = float(minimum)
        self._max = float(maximum)
        self._default = float(default)
        self._log = log
        self._fmt = fmt
        self._unit = unit
        self._role = color_role
        self._value = float(default)
        self._dragging = False
        self._drag_y0 = 0.0
        self._drag_v0 = float(default)
        self._compact = bool(compact)
        if compact:
            self.setFixedSize(54, 78)
        else:
            self.setFixedSize(76, 100)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip(f"{label} · drag to set · double-click reset")

    # -- value mapping ------------------------------------------------------

    def value(self) -> float:
        return self._value

    def setValue(self, v: float, *, emit: bool = True):
        v = max(self._min, min(self._max, float(v)))
        if abs(v - self._value) < 1e-9:
            return
        self._value = v
        self.update()
        if emit:
            self.valueChanged.emit(self._value)

    def reset(self, *, emit: bool = True):
        self.setValue(self._default, emit=emit)

    def _norm(self) -> float:
        if self._log and self._min > 0 and self._max > 0:
            lo, hi = math.log10(self._min), math.log10(self._max)
            return (math.log10(self._value) - lo) / (hi - lo)
        return (self._value - self._min) / (self._max - self._min)

    def _from_norm(self, n: float) -> float:
        n = max(0.0, min(1.0, n))
        if self._log and self._min > 0 and self._max > 0:
            lo, hi = math.log10(self._min), math.log10(self._max)
            return 10 ** (lo + n * (hi - lo))
        return self._min + n * (self._max - self._min)

    def _display(self) -> str:
        try:
            return self._fmt.format(self._value) + self._unit
        except Exception:
            return f"{self._value:.2f}{self._unit}"

    # -- paint --------------------------------------------------------------

    def _band_color(self) -> QColor:
        c0, c1, c2 = Color.wave_stops()
        if self._role == "low":
            return QColor(Color.GOLD if hasattr(Color, "GOLD") else c2)
        if self._role == "high":
            return QColor(c0)
        if self._role == "warn":
            return QColor(255, 140, 80)
        return QColor(c1)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx = w / 2.0
        cy = 28.0 if self._compact else 40.0
        r = 18.0 if self._compact else 27.0
        band_c = self._band_color()
        n = self._norm()
        t = (n - 0.5) * 2.0

        bloom = QRadialGradient(cx, cy, r + 12)
        gc = QColor(band_c)
        gc.setAlpha(int(18 + 70 * abs(t)))
        bloom.setColorAt(0.0, gc)
        bloom.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(bloom)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r + 10, r + 10)

        body = QRadialGradient(cx - 7, cy - 9, r * 1.7)
        body.setColorAt(0.0, QColor(Color.with_alpha(Color.ELEVATED, 1.0)))
        body.setColorAt(0.7, QColor(Color.SURFACE))
        body.setColorAt(1.0, QColor(Color.BG))
        p.setBrush(body)
        p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.9)), 1.2))
        p.drawEllipse(QPointF(cx, cy), r, r)

        rect = QRectF(cx - r + 5, cy - r + 5, 2 * (r - 5), 2 * (r - 5))
        p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.8)), 3.0 if self._compact else 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawArc(rect, 210 * 16, -240 * 16)

        if abs(t) > 0.015:
            p.setPen(QPen(band_c, 3.2 if self._compact else 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, int(-t * 120 * 16))

        needle_ang = math.radians(90 - t * 120.0)
        nx = cx + math.cos(needle_ang) * (r - 8)
        ny = cy - math.sin(needle_ang) * (r - 10)
        p.setPen(QPen(QColor(Color.WHITE), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(nx, ny))
        p.setBrush(band_c)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 4.0, 4.0)

        p.setPen(QColor(Color.TEXT))
        font = QFont()
        font.setPointSize(8)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        p.setFont(font)
        p.drawText(QRectF(0, cy + r + 1, w, 15), Qt.AlignHCenter, self._label)

        p.setPen(band_c)
        font.setPointSize(8)
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(0, cy - 6, w, 13), Qt.AlignHCenter, self._display())

        p.end()

    # -- mouse --------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_y0 = event.position().y()
            self._drag_v0 = self._value
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            dy = self._drag_y0 - event.position().y()
            dn = dy * 0.0045
            if self._log:
                n0 = (math.log10(self._drag_v0) - math.log10(self._min)) / (math.log10(self._max) - math.log10(self._min)) if self._min > 0 else 0.0
                self.setValue(self._from_norm(n0 + dn))
            else:
                self.setValue(self._drag_v0 + dn * (self._max - self._min))
            event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def mouseDoubleClickEvent(self, event):
        self.reset()
        event.accept()

    def wheelEvent(self, event):
        step = (self._max - self._min) * 0.02
        self.setValue(self._value + (step if event.angleDelta().y() > 0 else -step))
        event.accept()


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------

class _ScanWorker(QObject):
    progress = Signal(str)
    finished = Signal(object)

    def __init__(self, project_root: Path):
        super().__init__()
        self.project_root = project_root

    def run(self):
        from nodaw.audio import vst_scan

        def progress(msg: str):
            self.progress.emit(msg)

        result = vst_scan.scan_drives(progress=progress)
        vst_scan.save_library(self.project_root, result)
        self.finished.emit(result)


class _HuntWorker(QObject):
    finished = Signal(object)

    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def run(self):
        from nodaw.audio.studio_fx import hunt_artifacts

        self.finished.emit(hunt_artifacts(self.path))


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------

def _chip(
    text: str,
    *,
    checkable: bool = False,
    checked: bool = True,
    color: str = CHIP_ACCENT,
    tooltip: str = "",
) -> QPushButton:
    b = QPushButton(text)
    b.setCheckable(checkable)
    if checkable:
        b.setChecked(checked)
    b.setCursor(Qt.PointingHandCursor)
    b.setFixedHeight(26)
    b.setMinimumWidth(64)
    if tooltip:
        b.setToolTip(tooltip)
    on_bg = QColor(color)
    off_bg = QColor(Color.ELEVATED)
    b.setStyleSheet(f"""
        QPushButton {{
            background: {off_bg.name()};
            color: {Color.MUTED};
            border: 1px solid {Color.LINE};
            border-radius: 13px;
            padding: 0 12px;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 0.4px;
        }}
        QPushButton:hover {{ border-color: {color}; color: {Color.TEXT}; }}
        QPushButton:checked {{
            background: rgba({on_bg.red()}, {on_bg.green()}, {on_bg.blue()}, 60);
            color: {color};
            border: 1px solid {color};
        }}
    """)
    return b


class StudioFxPanel(QFrame):
    renderRequested = Signal()
    saveRequested = Signal()
    liveChanged = Signal()
    artifactMarkersReady = Signal(object)  # list of hit dicts for waveform overlay

    def __init__(self, project_root: Path, parent=None):
        super().__init__(parent)
        self._project_root = Path(project_root)
        self._hunt = None
        self._json_effects = []
        self._threads: list[QThread] = []
        self._scan_btn = None
        self._live_active = False
        self._bleed_auto: dict[str, Any] = {}
        self._artifact_auto: dict[str, Any] = {}
        self.setObjectName("StudioFxPanel")
        self.setStyleSheet(f"""
            QFrame#StudioFxPanel {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {Color.with_alpha(Color.ELEVATED, 1.0)},
                    stop:1 {Color.with_alpha(Color.SURFACE, 1.0)});
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.3)};
                border-radius: {Radius.XL}px;
            }}
        """)
        self._build()

    # ------------------------------------------------------------------ ui

    def _card(self, title: str, hint: str = "") -> tuple[QVBoxLayout, QHBoxLayout]:
        lay = QVBoxLayout()
        lay.setContentsMargins(Space.LG, 4, Space.LG, 10)
        lay.setSpacing(6)
        hdr = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(
            f"font-size: 9px; font-weight: 700; letter-spacing: 1.4px; color: {Color.ACCENT_SOFT};"
        )
        hdr.addWidget(t)
        if hint:
            h = QLabel(hint)
            h.setStyleSheet(f"font-size: 9px; color: {Color.MUTED};")
            hdr.addWidget(h)
        hdr.addStretch()
        lay.addLayout(hdr)
        return lay, hdr

    def _knob_row(self, knobs: list[FxKnob]) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(2)
        row.addStretch()
        for k in knobs:
            row.addWidget(k)
        row.addStretch()
        return row

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        root.setSpacing(8)

        # ---- ANALYSIS ----
        alay, ahdr = self._card("ANALYSIS", "auto-dials de-click · de-DC · dropout edges")
        self._hunt_chips: list[QPushButton] = []
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self._hunt_chips_lay = chips
        ahdr.addLayout(chips)
        self._btn_scan = QPushButton("Scan + auto")
        self._btn_scan.setCursor(Qt.PointingHandCursor)
        self._btn_scan.setFixedHeight(26)
        self._btn_scan.setToolTip(
            "Hunt clicks / dropouts / DC and auto-enable only the repairs that help"
        )
        self._btn_scan.clicked.connect(self.scan_now)
        ahdr.addWidget(self._btn_scan)
        self._art_status = QLabel("not scanned yet")
        self._art_status.setStyleSheet(f"font-size: 10px; font-weight: 600; color: {Color.TEXT};")
        alay.addWidget(self._art_status)
        toggles = QHBoxLayout()
        toggles.setSpacing(8)
        # Auto-enabled only when scan finds real issues (dry otherwise)
        self.cb_declick = _chip("DE-CLICK", checkable=True, checked=False, color=CHIP_AMBER)
        self.cb_dedc = _chip("DE-DC", checkable=True, checked=False, color=CHIP_CYAN)
        self.cb_deedge = _chip("DROPOUT EDGES", checkable=True, checked=False, color=CHIP_ORANGE)
        for cb in (self.cb_declick, self.cb_dedc, self.cb_deedge):
            cb.toggled.connect(self._schedule)
            toggles.addWidget(cb)
        toggles.addStretch()
        alay.addLayout(toggles)
        # RX-class algorithm selector
        algo_row = QHBoxLayout()
        algo_row.setSpacing(8)
        al = QLabel("ALGO")
        al.setStyleSheet(f"font-size: 9px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};")
        algo_row.addWidget(al)
        self._declick_algo = QComboBox()
        self._declick_algo.addItem("Auto (pick best)", "auto")
        self._declick_algo.addItem("Multi-band (LR4)", "multi_band")
        self._declick_algo.addItem("Spectral / pixel", "spectral")
        self._declick_algo.addItem("Single-band", "single")
        self._declick_algo.setCurrentIndex(0)
        self._declick_algo.setToolTip(
            "De-click engine: multi-band = per-band detect/repair; spectral = STFT pixel inpaint"
        )
        self._declick_algo.currentIndexChanged.connect(self._schedule)
        algo_row.addWidget(self._declick_algo, 1)
        self._freq_skew = FxKnob(
            "FREQ SKEW", minimum=0, maximum=100, default=50, fmt="{:.0f}", unit="%", color_role="mid"
        )
        self._freq_skew.setToolTip("Spectral repair: 0 = full-band, 100 = emphasize highs (mouth clicks)")
        self._freq_skew.valueChanged.connect(self._schedule)
        algo_row.addWidget(self._freq_skew)
        alay.addLayout(algo_row)
        root.addLayout(alay)

        # ---- CHAIN ----
        clay, chdr = self._card("FX CHAIN", "order of processing")
        self._chain_chips: dict[str, QPushButton] = {}
        chain = QHBoxLayout()
        chain.setSpacing(6)
        stages = (
            ("ARTIFACTS", CHIP_CYAN),
            ("BLEEDFIX", CHIP_ORANGE),
            ("EQ", CHIP_ACCENT),
            ("TONE", CHIP_ACCENT),
            ("PRESETS", CHIP_GREEN),
        )
        for i, (stage, color) in enumerate(stages):
            # Artifacts + BleedFix start OFF until auto-dial finds something useful
            default_on = stage not in ("ARTIFACTS", "BLEEDFIX")
            c = _chip(stage, checkable=True, checked=default_on, color=color)
            c.setToolTip("stage on / off (off = dry passthrough for that stage)")
            c.toggled.connect(self._on_stage_toggled)
            self._chain_chips[stage.lower()] = c
            chain.addWidget(c)
            if i < len(stages) - 1:
                arrow = QLabel("→")
                arrow.setStyleSheet(f"font-size: 11px; color: {Color.MUTED}; background: transparent;")
                chain.addWidget(arrow)
        chain.addStretch()
        clay.addLayout(chain)

        mrow = QHBoxLayout()
        mrow.setSpacing(Space.MD)
        mwrap = QVBoxLayout()
        mwrap.setSpacing(0)
        mwrap.setAlignment(Qt.AlignHCenter)
        self.master_power = PowerLightButton()
        self.master_power.setOn(True)
        self.master_power.setToolTip("Master FX bypass — off plays dry signal")
        self.master_power.toggled.connect(self._schedule)
        mwrap.addWidget(self.master_power, 0, Qt.AlignHCenter)
        ml = QLabel("BYPASS")
        ml.setAlignment(Qt.AlignCenter)
        ml.setStyleSheet(f"font-size: 8px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};")
        mwrap.addWidget(ml)
        mrow.addLayout(mwrap)

        self.k_wet = FxKnob("WET/DRY", minimum=0, maximum=100, default=100, fmt="{:.0f}", unit="%", color_role="mid")
        self.k_wet.valueChanged.connect(self._schedule)
        mrow.addWidget(self.k_wet)

        mrow.addStretch()
        self._live_badge = QLabel("● LIVE")
        self._live_badge.setStyleSheet(
            f"font-size: 10px; font-weight: 800; letter-spacing: 1.2px; color: {Color.MUTED}; "
            f"background: {Color.ELEVATED}; border: 1px solid {Color.LINE}; border-radius: 10px; "
            f"padding: 4px 10px;"
        )
        self._live_badge.setToolTip("Knobs apply in real time while playing")
        mrow.addWidget(self._live_badge)
        mrow.addSpacing(8)
        self._btn_render = QPushButton("Render FX → player")
        self._btn_render.setCursor(Qt.PointingHandCursor)
        self._btn_render.setFixedHeight(32)
        self._btn_render.setToolTip("Bake the full chain (incl. batch tools) to a WAV and play it")
        self._btn_render.clicked.connect(self.renderRequested.emit)
        mrow.addWidget(self._btn_render)
        self._btn_save = QPushButton("Save…")
        self._btn_save.setCursor(Qt.PointingHandCursor)
        self._btn_save.setFixedHeight(32)
        self._btn_save.setToolTip("Export the processed file (WAV / FLAC / MP3)")
        self._btn_save.clicked.connect(self.saveRequested.emit)
        mrow.addWidget(self._btn_save)
        clay.addLayout(mrow)
        root.addLayout(clay)

        # ---- BLEEDFIX ----
        blay, bhdr = self._card("BLEEDFIX", "auto-dials floor · thr · ratio · times")
        self._bleed_profile = QComboBox()
        self._bleed_profile.addItem("Safe", "safe")
        self._bleed_profile.addItem("Balanced", "balanced")
        self._bleed_profile.addItem("Aggressive", "aggressive")
        self._bleed_profile.setCurrentIndex(1)
        self._bleed_profile.setFixedHeight(26)
        self._bleed_profile.setToolTip("Auto-dial profile: how hard BleedFix may duck quiet regions")
        bhdr.addWidget(self._bleed_profile)
        self._btn_bleed_auto = QPushButton("Auto-dial")
        self._btn_bleed_auto.setCursor(Qt.PointingHandCursor)
        self._btn_bleed_auto.setFixedHeight(26)
        self._btn_bleed_auto.setToolTip(
            "Analyze noise floor vs program and set the most effective gate settings"
        )
        self._btn_bleed_auto.clicked.connect(self.auto_dial_bleedfix)
        bhdr.addWidget(self._btn_bleed_auto)
        self.bleed_power = PowerLightButton()
        # OFF until auto finds a useful floor/program gap
        self.bleed_power.setOn(False)
        self.bleed_power.setToolTip("BleedFix power (off = dry passthrough)")
        self.bleed_power.toggled.connect(self._schedule)
        bw = QVBoxLayout()
        bw.setSpacing(0)
        bw.setAlignment(Qt.AlignHCenter)
        bw.addWidget(self.bleed_power, 0, Qt.AlignHCenter)
        lbl = QLabel("FX")
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(f"font-size: 8px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};")
        bw.addWidget(lbl)
        self._bleed_knobs = {
            "thresh": FxKnob("THRESH", minimum=-80, maximum=-20, default=-46, fmt="{:.0f}", unit=" dB", color_role="warn"),
            "ratio": FxKnob("RATIO", minimum=2, maximum=20, default=8, fmt="{:.0f}", unit=":1", color_role="warn"),
            "attack": FxKnob("ATTACK", minimum=0.1, maximum=50, default=10, fmt="{:.0f}", unit=" ms", color_role="warn"),
            "release": FxKnob("RELEASE", minimum=10, maximum=600, default=160, fmt="{:.0f}", unit=" ms", color_role="warn"),
        }
        for k in self._bleed_knobs.values():
            k.valueChanged.connect(self._schedule)
        brow = self._knob_row(list(self._bleed_knobs.values()))
        inner = QHBoxLayout()
        inner.addLayout(bw)
        inner.addLayout(brow, 1)
        blay.addLayout(inner)
        self._bleed_status = QLabel("auto idle · power off until dialed")
        self._bleed_status.setStyleSheet(f"font-size: 10px; color: {Color.MUTED};")
        self._bleed_status.setWordWrap(True)
        blay.addWidget(self._bleed_status)
        root.addLayout(blay)

        # ---- 6-band Parametric EQ (graph + knobs straddle) ----
        elay, _ = self._card(
            "6-BAND PARAMETRIC EQ",
            "drag nodes · wheel=Q · type · dynamic",
        )
        self._eq_bands: list[dict[str, Any]] = []
        # B1..B6 defaults: LS · low · low-mid · mid · high-mid · HS
        band_specs = [
            ("B1", 80.0, 0.0, 0.7, "low", "lowshelf"),
            ("B2", 200.0, 0.0, 1.0, "low", "peaking"),
            ("B3", 500.0, 0.0, 1.0, "mid", "peaking"),
            ("B4", 2000.0, 0.0, 1.0, "mid", "peaking"),
            ("B5", 6000.0, 0.0, 1.0, "high", "peaking"),
            ("B6", 12000.0, 0.0, 0.7, "high", "highshelf"),
        ]
        from .eq_visual import EqOverlayStage

        self._eq_stage = EqOverlayStage(
            self,
            db_range=18.0,
            title="6-BAND  ·  DRAG F/G  ·  WHEEL Q  ·  20 Hz – 20 kHz",
            curve_height=158,
            overlap=58,
            knobs_height=210,
        )
        self._eq_curve = self._eq_stage.curve
        self._eq_curve.bandDragged.connect(self._on_eq_curve_drag)
        self._eq_curve.bandQChanged.connect(self._on_eq_curve_q)
        self._eq_curve.bandReleased.connect(lambda *_: self._schedule())
        self._eq_curve.bandSelected.connect(self._on_eq_band_selected)

        eq_row = QHBoxLayout(self._eq_stage.overlay)
        eq_row.setContentsMargins(4, 4, 4, 2)
        eq_row.setSpacing(4)
        type_items = [
            ("Peak", "peaking"),
            ("Low S", "lowshelf"),
            ("High S", "highshelf"),
            ("Notch", "notch"),
            ("HPF", "highpass"),
            ("LPF", "lowpass"),
        ]
        for band, freq, gain, q, role, typ in band_specs:
            col_w = QWidget()
            col_w.setStyleSheet("background: transparent;")
            col = QVBoxLayout(col_w)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(1)
            pwr = PowerLightButton()
            pwr.setFixedSize(36, 36)
            pwr.setToolTip(f"{band} power")
            pwr.toggled.connect(self._on_eq_band_changed)
            col.addWidget(pwr, 0, Qt.AlignHCenter)
            b_lbl = QLabel(band)
            b_lbl.setAlignment(Qt.AlignCenter)
            b_lbl.setStyleSheet(
                f"font-size: 8px; font-weight: 800; letter-spacing: 0.6px; color: {Color.MUTED};"
                f" background: transparent;"
            )
            col.addWidget(b_lbl)
            type_c = QComboBox()
            type_c.setFixedHeight(22)
            type_c.setFixedWidth(64)
            type_c.setStyleSheet(
                f"QComboBox {{ font-size: 9px; background: {Color.ELEVATED}; color: {Color.TEXT};"
                f" border: 1px solid {Color.LINE}; border-radius: 4px; padding: 0 2px; }}"
            )
            for lab, data in type_items:
                type_c.addItem(lab, data)
            # default type
            for i in range(type_c.count()):
                if type_c.itemData(i) == typ:
                    type_c.setCurrentIndex(i)
                    break
            type_c.currentIndexChanged.connect(self._on_eq_band_changed)
            col.addWidget(type_c, 0, Qt.AlignHCenter)
            kf = FxKnob(
                "FREQ", minimum=20, maximum=20000, default=freq, log=True,
                fmt="{:.0f}", unit="Hz", color_role=role, compact=True,
            )
            kg = FxKnob(
                "GAIN", minimum=-18, maximum=18, default=gain,
                fmt="{:+.1f}", unit="dB", color_role=role, compact=True,
            )
            kq = FxKnob(
                "Q", minimum=0.2, maximum=12, default=q,
                fmt="{:.1f}", color_role=role, compact=True,
            )
            for k in (kf, kg, kq):
                k.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
                k.setStyleSheet("background: transparent;")
                k.valueChanged.connect(self._on_eq_band_changed)
            kn = QHBoxLayout()
            kn.setSpacing(0)
            kn.setContentsMargins(0, 0, 0, 0)
            kn.addWidget(kf)
            kn.addWidget(kg)
            kn.addWidget(kq)
            col.addLayout(kn)
            dyn = QPushButton("DYN")
            dyn.setCheckable(True)
            dyn.setChecked(False)
            dyn.setFixedHeight(20)
            dyn.setFixedWidth(48)
            dyn.setCursor(Qt.PointingHandCursor)
            dyn.setToolTip("Dynamic EQ — gain rides band energy (threshold/ratio below)")
            dyn.setStyleSheet(
                f"QPushButton {{ font-size: 8px; font-weight: 800; letter-spacing: 0.8px;"
                f" background: {Color.ELEVATED}; color: {Color.MUTED}; border: 1px solid {Color.LINE};"
                f" border-radius: 6px; }}"
                f"QPushButton:checked {{ color: {Color.WARNING}; border-color: {Color.WARNING};"
                f" background: {Color.with_alpha(Color.WARNING, 0.15)}; }}"
            )
            dyn.toggled.connect(self._on_eq_band_changed)
            col.addWidget(dyn, 0, Qt.AlignHCenter)
            self._eq_bands.append(
                {
                    "power": pwr,
                    "freq": kf,
                    "gain": kg,
                    "q": kq,
                    "type": type_c,
                    "dyn": dyn,
                    "role": role,
                    "label": band,
                    "widget": col_w,
                }
            )
            eq_row.addWidget(col_w, 1)
        elay.addWidget(self._eq_stage)

        # Global dynamic controls + output tilt
        dyn_row = QHBoxLayout()
        dyn_row.setSpacing(Space.SM)
        dlab = QLabel("DYNAMIC")
        dlab.setStyleSheet(
            f"font-size: 9px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};"
        )
        dyn_row.addWidget(dlab)
        self._eq_dyn_thresh = FxKnob(
            "THRESH", minimum=-60, maximum=-6, default=-24,
            fmt="{:.0f}", unit="dB", color_role="warn", compact=True,
        )
        self._eq_dyn_ratio = FxKnob(
            "RATIO", minimum=1.0, maximum=8.0, default=2.0,
            fmt="{:.1f}", unit=":1", color_role="warn", compact=True,
        )
        self._eq_dyn_range = FxKnob(
            "RANGE", minimum=1.0, maximum=18.0, default=10.0,
            fmt="{:.0f}", unit="dB", color_role="warn", compact=True,
        )
        self._eq_output = FxKnob(
            "OUT", minimum=-12, maximum=12, default=0.0,
            fmt="{:+.1f}", unit="dB", color_role="mid", compact=True,
        )
        for k in (self._eq_dyn_thresh, self._eq_dyn_ratio, self._eq_dyn_range, self._eq_output):
            k.valueChanged.connect(self._on_eq_band_changed)
            dyn_row.addWidget(k)
        dyn_row.addStretch()
        hint = QLabel("wheel on graph = Q  ·  DYN rides energy at band f0")
        hint.setStyleSheet(f"font-size: 9px; color: {Color.MUTED};")
        dyn_row.addWidget(hint)
        elay.addLayout(dyn_row)
        root.addLayout(elay)
        self._eq_curve_syncing = False
        self._eq_selected = 0
        self._sync_eq_curve()

        # ---- PRESETS / BATCH (no VST) ----
        vlay, vhdr = self._card("FX PRESETS", "JSON chains · batch tools")
        self._vault_status = QLabel("")
        self._vault_status.setStyleSheet(f"font-size: 9px; color: {Color.MUTED};")
        vhdr.addWidget(self._vault_status)
        self._btn_rescan = QPushButton("Scan drives…")
        self._btn_rescan.setCursor(Qt.PointingHandCursor)
        self._btn_rescan.setFixedHeight(26)
        self._btn_rescan.setToolTip("Find JSON effect catalogs and ffmpeg batch tools")
        self._btn_rescan.clicked.connect(self.rescan_drives)
        vhdr.addWidget(self._btn_rescan)

        row2 = QHBoxLayout()
        row2.setSpacing(Space.SM)
        l2 = QLabel("FX PRESETS")
        l2.setStyleSheet(f"font-size: 9px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};")
        row2.addWidget(l2)
        self._json_combo = QComboBox()
        self._json_combo.setMinimumWidth(300)
        self._json_combo.currentIndexChanged.connect(self._schedule)
        row2.addWidget(self._json_combo, 1)
        vlay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(Space.SM)
        l3 = QLabel("BATCH TOOLS")
        l3.setStyleSheet(f"font-size: 9px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};")
        row3.addWidget(l3)
        self._bat_combo = QComboBox()
        self._bat_combo.setMinimumWidth(300)
        self._bat_combo.currentIndexChanged.connect(self._schedule)
        row3.addWidget(self._bat_combo, 1)
        vlay.addLayout(row3)
        root.addLayout(vlay)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        # ~one UI frame — LiveFX needs knobs to feel immediate, not 90–300 ms late
        self._debounce.setInterval(24)
        self._debounce.timeout.connect(self._emit_live)

    # --------------------------------------------------------------- public

    def load_vault(self):
        """Load cached library; kick off a background scan when empty."""
        from nodaw.audio import vst_scan

        cached = vst_scan.load_library(self._project_root)
        if cached.plugins or cached.json_effects:
            self.set_vault(cached)
        self.rescan_drives()

    def rescan_drives(self):
        if self._scan_btn is not None:
            return
        if self._btn_rescan:
            self._btn_rescan.setEnabled(False)
        self._vault_status.setText("Scanning drives for plugins and FX presets…")
        thread = QThread(self)
        worker = _ScanWorker(self._project_root)
        worker.moveToThread(thread)
        worker.progress.connect(lambda msg: self._vault_status.setText(msg))
        worker.finished.connect(self._on_scan_done)
        thread.started.connect(worker.run)
        self._threads.append(thread)
        thread.start()

    def auto_scan(self, path: Path):
        """Artifact hunt on load — populates the ANALYSIS section."""
        path = Path(path)
        self._art_status.setText("hunting artifacts…")
        thread = QThread(self)
        worker = _HuntWorker(path)
        worker.moveToThread(thread)
        worker.finished.connect(self._on_hunt_done)
        thread.started.connect(worker.run)
        self._threads.append(thread)
        thread.start()

    def scan_now(self):
        src = getattr(self, "_current_path", None)
        if src and Path(src).exists():
            self.auto_scan(src)
        else:
            self._art_status.setText("load a file first")

    def set_current_path(self, path: Path):
        self._current_path = str(Path(path).resolve())

    def artifacts(self):
        return self._hunt

    def is_bypassed(self) -> bool:
        return not self.master_power.isOn()

    def set_live_active(self, active: bool):
        self._live_active = bool(active)
        color = CHIP_GREEN if active else Color.MUTED
        self._live_badge.setStyleSheet(
            f"font-size: 10px; font-weight: 800; letter-spacing: 1.2px; color: {color}; "
            f"background: {Color.ELEVATED}; border: 1px solid {color}; border-radius: 10px; "
            f"padding: 4px 10px;"
        )

    def settings(self) -> dict[str, Any]:
        eq_bands = []
        for b in self._eq_bands:
            eq_bands.append({
                "on": b["power"].isOn(),
                "freq": b["freq"].value(),
                "gain_db": b["gain"].value(),
                "q": b["q"].value(),
                "type": str(b["type"].currentData() or "peaking"),
                "dynamic": bool(b["dyn"].isChecked()),
                "dyn_threshold_db": float(self._eq_dyn_thresh.value()),
                "dyn_ratio": float(self._eq_dyn_ratio.value()),
                "dyn_range_db": float(self._eq_dyn_range.value()),
                "label": b.get("label"),
            })
        eq_output_db = float(self._eq_output.value())
        json_effect = None
        jdx = self._json_combo.currentIndex()
        if jdx > 0:
            json_effect = self._json_combo.itemData(jdx)
        bat_effect = None
        bdx = self._bat_combo.currentIndex()
        if bdx > 0:
            bat_effect = self._bat_combo.itemData(bdx)
        ba = self._bleed_auto or {}
        bleed = {
            "on": self.bleed_power.isOn(),
            "threshold_db": self._bleed_knobs["thresh"].value(),
            "ratio": self._bleed_knobs["ratio"].value(),
            "attack_ms": self._bleed_knobs["attack"].value(),
            "release_ms": self._bleed_knobs["release"].value(),
            "wet": 1.0,
            "mode": ba.get("mode", "fixed"),
            "margin_db": ba.get("margin_db", 8.0),
            "bands": ba.get("bands", 1),
            "hysteresis_db": ba.get("hysteresis_db", 2.5),
            "lookahead_ms": ba.get("lookahead_ms", 5.0),
            "spectral": bool(ba.get("spectral")),
            "profile": ba.get("profile") or self._bleed_profile.currentData() or "balanced",
            "est_bleed_reduction_db": ba.get("est_bleed_reduction_db", 0.0),
            "report": ba.get("report") or {},
        }
        return {
            "declick": self.cb_declick.isChecked(),
            "dedc": self.cb_dedc.isChecked(),
            "deedge": self.cb_deedge.isChecked(),
            "algorithm": (
                str((self._artifact_auto or {}).get("algorithm") or "auto")
                if (self._declick_algo.currentData() or "auto") == "auto"
                else str(self._declick_algo.currentData() or "auto")
            ),
            "freq_skew": float(self._freq_skew.value()) / 100.0,
            "min_confidence": float((self._artifact_auto or {}).get("min_confidence", 0.45)),
            "artifact_hits": list((self._artifact_auto or {}).get("hits") or getattr(self._hunt, "hits", []) or []),
            "bleed": bleed,
            "eq_bands": eq_bands,
            "eq_output_db": eq_output_db,
            "json_effect": json_effect,
            "bat_effect": bat_effect,
            "wet_dry": self.k_wet.value() / 100.0,
        }

    # -------------------------------------------------------------- slots

    def _schedule(self, *_):
        self._debounce.start()

    def _on_eq_band_changed(self, *_):
        if not getattr(self, "_eq_curve_syncing", False):
            self._sync_eq_curve()
        self._schedule()

    def _on_eq_band_selected(self, idx: int) -> None:
        self._eq_selected = int(idx)
        try:
            self._eq_curve.set_selected(idx)
        except Exception:
            pass

    def _sync_eq_curve(self) -> None:
        """Push parametric knob state into the multi-band graph."""
        if not hasattr(self, "_eq_curve"):
            return
        bands = []
        any_on = False
        roles = ("low", "mid", "high")
        for i, b in enumerate(self._eq_bands):
            on = bool(b["power"].isOn())
            any_on = any_on or on
            bands.append(
                {
                    "label": b.get("label") or f"B{i + 1}",
                    "type": str(b["type"].currentData() or "peaking"),
                    "freq": float(b["freq"].value()),
                    "gain_db": float(b["gain"].value()),
                    "q": float(b["q"].value()),
                    "on": on,
                    "dynamic": bool(b["dyn"].isChecked()),
                    "color_role": b.get("role") or roles[min(i // 2, 2)],
                }
            )
        self._eq_curve.set_bands(bands)
        self._eq_curve.set_selected(getattr(self, "_eq_selected", 0))
        # Graph active when any band lit, or when stage chip wants EQ
        stage_on = True
        try:
            stage_on = self._chain_chips.get("eq", None)
            if stage_on is not None:
                stage_on = stage_on.isChecked()
        except Exception:
            stage_on = True
        self._eq_curve.set_powered(bool(any_on or stage_on))

    def _on_eq_curve_drag(self, idx: int, freq: float, gain_db: float) -> None:
        """Drag handles on the graph → update knobs (freq + gain)."""
        if idx < 0 or idx >= len(self._eq_bands):
            return
        self._eq_curve_syncing = True
        self._eq_selected = idx
        b = self._eq_bands[idx]
        try:
            if not b["power"].isOn():
                b["power"].setOn(True, emit=False)
            b["freq"].setValue(float(freq), emit=False)
            # HPF/LPF don't use gain — still store for when type switches
            typ = str(b["type"].currentData() or "peaking")
            if typ not in ("highpass", "lowpass"):
                b["gain"].setValue(float(gain_db), emit=False)
        except Exception:
            pass
        self._eq_curve_syncing = False
        self._sync_eq_curve()
        self._schedule()

    def _on_eq_curve_q(self, idx: int, q: float) -> None:
        if idx < 0 or idx >= len(self._eq_bands):
            return
        self._eq_curve_syncing = True
        try:
            self._eq_bands[idx]["q"].setValue(float(q), emit=False)
        except Exception:
            pass
        self._eq_curve_syncing = False
        self._sync_eq_curve()
        self._schedule()

    def _emit_live(self):
        self._update_chain_chips()
        self.liveChanged.emit()

    def _on_stage_toggled(self, *_):
        """Stage chips drive the underlying power / repair toggles."""
        chips = self._chain_chips
        on = chips["artifacts"].isChecked()
        for cb in (self.cb_declick, self.cb_dedc, self.cb_deedge):
            cb.blockSignals(True)
            cb.setChecked(on)
            cb.blockSignals(False)
        try:
            self.bleed_power.setOn(chips["bleedfix"].isChecked())
        except Exception:
            pass
        on = chips["eq"].isChecked()
        for b in self._eq_bands:
            p = b["power"]
            p.blockSignals(True)
            p.setOn(on)
            p.blockSignals(False)
        self._sync_eq_curve()
        self._schedule()

    def _sync_stage_powers(self):
        """Stage chips mirror the actual power lights / selections."""
        chips = self._chain_chips
        states = {
            "artifacts": self.cb_declick.isChecked() or self.cb_dedc.isChecked() or self.cb_deedge.isChecked(),
            "bleedfix": self.bleed_power.isOn(),
            "eq": any(b["power"].isOn() for b in self._eq_bands),
            "tone": True,
            "presets": self._json_combo.currentIndex() > 0,
        }
        for name, on in states.items():
            c = chips.get(name)
            if c is None:
                continue
            c.blockSignals(True)
            c.setChecked(on)
            c.blockSignals(False)

    def _update_chain_chips(self):
        self._sync_stage_powers()

    def _on_hunt_done(self, hunt):
        """Scan finished → auto-dial Artifact Hunter + BleedFix."""
        self._hunt = hunt
        if hunt is None or hunt.error:
            self._art_status.setText(
                f"hunt failed: {getattr(hunt, 'error', '?')}" if hunt else "hunt failed"
            )
            return
        from nodaw.audio.studio_fx import suggest_artifact_settings

        auto = suggest_artifact_settings(hunt)
        self._artifact_auto = auto
        self.cb_declick.blockSignals(True)
        self.cb_deedge.blockSignals(True)
        self.cb_dedc.blockSignals(True)
        self.cb_declick.setChecked(bool(auto.get("declick")))
        self.cb_deedge.setChecked(bool(auto.get("deedge")))
        self.cb_dedc.setChecked(bool(auto.get("dedc")))
        self.cb_declick.blockSignals(False)
        self.cb_deedge.blockSignals(False)
        self.cb_dedc.blockSignals(False)
        # Sync algo combo to auto pick when set to Auto or after scan
        try:
            algo = str(auto.get("algorithm") or "auto")
            # Keep user on Auto display but store suggestion in auto dict
            if self._declick_algo.currentData() == "auto" and algo in ("multi_band", "spectral", "single"):
                pass  # bake uses algorithm field from settings → we inject suggestion in settings()
            skew = float(auto.get("freq_skew", 0.5)) * 100.0
            self._freq_skew.blockSignals(True)
            self._freq_skew.setValue(skew)
            self._freq_skew.blockSignals(False)
        except Exception:
            pass
        algo = str(auto.get("algorithm") or "auto")
        skew_pct = int(round(float(auto.get("freq_skew", 0.5)) * 100))
        self._art_status.setText(
            f"{hunt.summary}  ·  {auto.get('note', 'auto')}  ·  RX {algo} · skew {skew_pct}%  ·  live patches on play"
        )
        self._rebuild_hunt_chips()
        # Push markers to host waveform if available
        try:
            self.artifactMarkersReady.emit(list(auto.get("hits") or hunt.hits or []))
        except Exception:
            pass
        # Also auto-dial BleedFix from the same file
        self.auto_dial_bleedfix(silent=True)
        self._schedule()

    def auto_dial_bleedfix(self, silent: bool = False):
        """Analyze current path and set BleedFix knobs for max useful ducking."""
        from nodaw.audio.studio_fx import suggest_bleedfix_settings

        path = getattr(self, "_current_path", None)
        if path is None or not Path(str(path)).exists():
            if not silent:
                self._bleed_status.setText("auto · load a mix first")
            return
        profile = "balanced"
        try:
            profile = str(self._bleed_profile.currentData() or "balanced")
        except Exception:
            pass
        try:
            sug = suggest_bleedfix_settings(path, profile=profile)
        except Exception as exc:
            self._bleed_status.setText(f"auto failed: {exc}")
            return
        self._bleed_auto = sug
        # Apply knobs
        thr = float(sug.get("threshold_db", -46.0))
        ratio = float(sug.get("ratio", 8.0))
        atk = float(sug.get("attack_ms", 10.0))
        rel = float(sug.get("release_ms", 160.0))
        kn = self._bleed_knobs
        for k, v in (
            ("thresh", thr),
            ("ratio", ratio),
            ("attack", atk),
            ("release", rel),
        ):
            try:
                kn[k].blockSignals(True)
                kn[k].setValue(v)
                kn[k].blockSignals(False)
            except Exception:
                pass
        # Enable only when auto says it's useful
        on = bool(sug.get("on"))
        self.bleed_power.blockSignals(True)
        self.bleed_power.setOn(on)
        self.bleed_power.blockSignals(False)
        note = str(sug.get("note") or "auto")
        rep = sug.get("report") or {}
        if rep:
            extra = (
                f"  ·  quiet duck ~{float(sug.get('est_bleed_reduction_db') or 0):.1f} dB"
                f"  ·  gated {100 * float(sug.get('gated_fraction') or 0):.0f}%"
            )
            note = note + extra
        self._bleed_status.setText(note)
        if not silent:
            self._schedule()

    def _rebuild_hunt_chips(self):
        hunt = self._hunt
        if hunt is None:
            return
        for old in self._hunt_chips:
            try:
                self._hunt_chips_lay.removeWidget(old)
                old.deleteLater()
            except Exception:
                pass
        self._hunt_chips = []
        if hunt.clicks:
            self._hunt_chips.append(_chip(f"{len(hunt.clicks)} CLICK", color=CHIP_AMBER, tooltip="sample-jump clicks detected"))
        if hunt.dropout_edges:
            self._hunt_chips.append(_chip(f"{len(hunt.dropout_edges)} DROPOUT", color=CHIP_ORANGE, tooltip="silence-edge glitches detected"))
        if abs(hunt.dc_offset) > 0.004:
            self._hunt_chips.append(_chip(f"DC {hunt.dc_offset:+.4f}", color=CHIP_CYAN, tooltip="DC offset detected"))
        if hunt.clipped_estimate:
            self._hunt_chips.append(_chip(f"{hunt.clipped_estimate} CLIP", color=CHIP_MUTED, tooltip="clipped samples"))
        if not self._hunt_chips:
            self._hunt_chips.append(_chip("CLEAN", color=CHIP_GREEN, tooltip="no artifacts found"))
        for c in self._hunt_chips:
            self._hunt_chips_lay.addWidget(c)
        self._hunt_chips_lay.addStretch()

    def _on_scan_done(self, result):
        if self._btn_rescan:
            self._btn_rescan.setEnabled(True)
        self.set_vault(result)
        self._scan_btn = None

    def set_vault(self, result):
        """Load JSON presets + batch tools only (VST hosting removed)."""
        from nodaw.audio.studio_fx import load_nodaw_catalog, json_effect_to_chain

        self._json_effects = list(result.json_effects or [])

        self._json_combo.blockSignals(True)
        self._json_combo.clear()
        self._json_combo.addItem("— no JSON effect —", None)
        for eff in self._json_effects:
            if eff.kind == "nodaw_catalog":
                try:
                    for item in load_nodaw_catalog(eff.path):
                        label = item.get("label") or item.get("id") or "?"
                        self._json_combo.addItem(
                            f"{label}  ·  {Path(eff.path).stem}",
                            item,
                        )
                except Exception:
                    continue
            elif eff.kind == "engine_effect":
                try:
                    import json as _json

                    data = _json.loads(Path(eff.path).read_text(encoding="utf-8", errors="replace"))
                    plugins, note = json_effect_to_chain(data)
                    if plugins:
                        self._json_combo.addItem(
                            f"{data.get('name', eff.name)}  ≈  {Path(eff.path).parent.name}",
                            data,
                        )
                except Exception:
                    continue
        self._json_combo.blockSignals(False)

        self._bat_combo.blockSignals(True)
        self._bat_combo.clear()
        self._bat_combo.addItem("— no batch tool —", None)
        for eff in self._json_effects:
            if eff.kind == "bat_effect":
                self._bat_combo.addItem(
                    f"{eff.name}  ·  {eff.category or 'batch'}",
                    eff,
                )
        self._bat_combo.blockSignals(False)

        n_bats = sum(1 for e in self._json_effects if e.kind == "bat_effect")
        n_json = len(self._json_effects) - n_bats
        self._vault_status.setText(
            f"{n_json} preset FX · {n_bats} batch tools · {getattr(result, 'duration_s', 0)}s"
        )
        self._schedule()

    def close_threads(self):
        for t in self._threads:
            try:
                t.quit()
                t.wait(1200)
            except Exception:
                pass
        self._threads = []
