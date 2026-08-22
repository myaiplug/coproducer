"""
Creative 3-band EQ knobs for Home player + Studio editor.

- Custom painted rotary knobs (theme-aware glow rings)
- LOW / MID / HIGH gain in dB (−12 … +12)
- Pedalboard bake for real audible preview (not a fake UI)

Design language: glass dials, arc value fill, band-colored halos that
react to gain (boost blooms, cut dims).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import Color, Radius, Space, Type


class CreativeEqKnob(QWidget):
    """
    Single rotary gain knob.

    Drag vertically or angularly. Double-click resets to 0 dB.
    """

    valueChanged = Signal(float)  # dB

    def __init__(
        self,
        band: str = "MID",
        subtitle: str = "1 kHz",
        *,
        color_role: str = "mid",  # low | mid | high
        parent=None,
    ):
        super().__init__(parent)
        self._band = band
        self._subtitle = subtitle
        self._role = color_role
        self._db = 0.0
        self._min_db = -12.0
        self._max_db = 12.0
        self._dragging = False
        self._drag_y0 = 0.0
        self._drag_db0 = 0.0
        self.setFixedSize(88, 108)
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.setToolTip(f"{band} shelf/peak  ·  drag ↑ boost  ↓ cut  ·  double-click zero")

    def value(self) -> float:
        return self._db

    def setValue(self, db: float, *, emit: bool = True):
        v = max(self._min_db, min(self._max_db, float(db)))
        if abs(v - self._db) < 0.01:
            return
        self._db = v
        self.update()
        if emit:
            self.valueChanged.emit(self._db)

    def _band_color(self) -> QColor:
        c0, c1, c2 = Color.wave_stops()
        if self._role == "low":
            # warm lean: dim → gold if present
            return QColor(Color.GOLD if hasattr(Color, "GOLD") else c2)
        if self._role == "high":
            return QColor(c0)  # soft / air
        return QColor(c1)  # mid = primary accent

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, 42.0
        r = 32.0
        band_c = self._band_color()

        # Outer glow bloom scales with |gain|
        bloom = min(1.0, abs(self._db) / 12.0)
        glow = QRadialGradient(cx, cy, r + 14)
        gc = QColor(band_c)
        gc.setAlpha(int(20 + 90 * bloom))
        glow.setColorAt(0.0, gc)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setBrush(glow)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), r + 14, r + 14)

        # Glass dial body
        body = QRadialGradient(cx - 8, cy - 10, r * 1.6)
        body.setColorAt(0.0, QColor(Color.with_alpha(Color.ELEVATED, 1.0)))
        body.setColorAt(0.7, QColor(Color.SURFACE))
        body.setColorAt(1.0, QColor(Color.BG))
        p.setBrush(body)
        p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.9)), 1.2))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # Track arc (−12 … +12 over 240°)
        p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.85)), 4.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        rect = QRectF(cx - r + 6, cy - r + 6, 2 * (r - 6), 2 * (r - 6))
        # Qt drawArc: 1/16°, 0 = 3 o'clock, positive = CCW
        track_start = 210 * 16
        track_span = -240 * 16
        p.drawArc(rect, track_start, track_span)

        # Value arc from 0 toward current
        t = self._db / 12.0  # −1..1
        if abs(t) > 0.01:
            p.setPen(
                QPen(
                    QColor(band_c),
                    4.5,
                    Qt.PenStyle.SolidLine,
                    Qt.PenCapStyle.RoundCap,
                )
            )
            if t >= 0:
                # from 0dB (90° in our map: mid of arc = top = 90° Qt from 3 o'clock is 90)
                # Arc mid at top: angle 90°. Left end −12 = 210°, right end +12 = −30°=330°
                # 0 dB at 90°: start 90, span = -t * 120
                zero_a = 90 * 16
                p.drawArc(rect, zero_a, int(-t * 120 * 16))
            else:
                zero_a = 90 * 16
                p.drawArc(rect, zero_a, int((-t) * 120 * 16))  # positive span toward left

        # Indicator needle
        needle_ang = math.radians(90 - (self._db / 12.0) * 120.0)
        nx = cx + math.cos(needle_ang) * (r - 12)
        ny = cy - math.sin(needle_ang) * (r - 12)
        p.setPen(QPen(QColor(Color.WHITE), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(nx, ny))
        p.setBrush(QColor(band_c))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)

        # Labels
        p.setPen(QColor(Color.TEXT))
        font = QFont()
        font.setPointSize(9)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        p.setFont(font)
        p.drawText(QRectF(0, cy + r + 2, w, 16), Qt.AlignHCenter, self._band)

        p.setPen(QColor(Color.MUTED))
        font.setPointSize(7)
        font.setBold(False)
        p.setFont(font)
        p.drawText(QRectF(0, cy + r + 16, w, 14), Qt.AlignHCenter, self._subtitle)

        # dB readout
        p.setPen(QColor(band_c))
        font.setPointSize(8)
        font.setBold(True)
        p.setFont(font)
        sign = "+" if self._db > 0.05 else ""
        p.drawText(QRectF(0, cy - 7, w, 14), Qt.AlignHCenter, f"{sign}{self._db:.1f}")

        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_y0 = event.position().y()
            self._drag_db0 = self._db
            event.accept()

    def mouseMoveEvent(self, event):
        if self._dragging:
            dy = self._drag_y0 - event.position().y()  # up = boost
            self.setValue(self._drag_db0 + dy * 0.12)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._dragging = False

    def mouseDoubleClickEvent(self, event):
        self.setValue(0.0)
        event.accept()

    def wheelEvent(self, event):
        step = 0.5 if event.angleDelta().y() > 0 else -0.5
        self.setValue(self._db + step)
        event.accept()


class PowerLightButton(QWidget):
    """Clickable power LED — lit when EQ is engaged, dark when dry."""

    toggled = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._on = False
        self.setFixedSize(44, 44)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("EQ power — click to engage / bypass Tone Sculpt")

    def isOn(self) -> bool:
        return self._on

    def setOn(self, on: bool, *, emit: bool = False):
        on = bool(on)
        if on == self._on:
            if emit:
                self.toggled.emit(self._on)
            return
        self._on = on
        self.update()
        if emit:
            self.toggled.emit(self._on)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setOn(not self._on, emit=True)
            event.accept()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        r = 15.0
        accent = QColor(Color.ACCENT)
        soft = QColor(Color.ACCENT_SOFT)

        # Outer bezel
        p.setPen(QPen(QColor(Color.LINE), 1.5))
        p.setBrush(QColor(Color.BG))
        p.drawEllipse(QPointF(cx, cy), r + 4, r + 4)

        if self._on:
            # Bloom
            bloom = QRadialGradient(cx, cy, r + 10)
            c = QColor(accent)
            c.setAlpha(120)
            bloom.setColorAt(0.0, c)
            c2 = QColor(accent)
            c2.setAlpha(0)
            bloom.setColorAt(1.0, c2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bloom)
            p.drawEllipse(QPointF(cx, cy), r + 10, r + 10)
            # Lit core
            core = QRadialGradient(cx - 3, cy - 3, r)
            core.setColorAt(0.0, QColor(Color.WHITE))
            core.setColorAt(0.35, soft)
            core.setColorAt(1.0, accent)
            p.setBrush(core)
            p.drawEllipse(QPointF(cx, cy), r - 2, r - 2)
            # Power glyph
            p.setPen(QPen(QColor(Color.BG), 2.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        else:
            # Dark glass
            core = QRadialGradient(cx - 2, cy - 2, r)
            core.setColorAt(0.0, QColor(Color.ELEVATED))
            core.setColorAt(1.0, QColor(Color.BG))
            p.setPen(QPen(QColor(Color.with_alpha(Color.MUTED, 0.5)), 1))
            p.setBrush(core)
            p.drawEllipse(QPointF(cx, cy), r - 2, r - 2)
            p.setPen(QPen(QColor(Color.MUTED), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))

        # IEC-style power symbol (arc + stem) — gap at 12 o'clock so the
        # stem passes cleanly through it (0° = 3 o'clock, positive = CCW).
        p.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(cx - 6, cy - 6, 12, 12)
        p.drawArc(rect, 120 * 16, 300 * 16)
        p.drawLine(QPointF(cx, cy - 7), QPointF(cx, cy + 1))
        p.end()


class CreativeEqStrip(QFrame):
    """
    LOW / MID / HIGH knobs + power light (engage EQ) + download EQ'd file.
    Multi-band frequency-response curve sits behind the knobs.
    """

    gainsChanged = Signal(float, float, float)
    applyRequested = Signal()  # host bakes EQ into player when power is ON
    downloadRequested = Signal()  # host exports EQ'd file

    def __init__(self, parent=None, *, compact: bool = False):
        super().__init__(parent)
        self.setObjectName("CreativeEqStrip")
        self.setStyleSheet(f"""
            QFrame#CreativeEqStrip {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {Color.with_alpha(Color.ELEVATED, 1.0)},
                    stop:1 {Color.with_alpha(Color.SURFACE, 1.0)});
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.28)};
                border-radius: {Radius.XL}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
        lay.setSpacing(4)

        hdr = QHBoxLayout()
        title = QLabel("TONE SCULPT  ·  creative EQ")
        title.setStyleSheet(
            f"font-size: 9px; font-weight: 700; letter-spacing: 1.4px; color: {Color.MUTED};"
        )
        hdr.addWidget(title)
        hdr.addStretch()
        self._hint = QLabel("power off · dry signal")
        self._hint.setStyleSheet(f"font-size: 9px; color: {Color.MUTED};")
        hdr.addWidget(self._hint)
        lay.addLayout(hdr)

        # Stage: curve + knobs straddle (half on graph, half under)
        from .eq_visual import EqOverlayStage

        self._stage = EqOverlayStage(
            self,
            db_range=12.0,
            title="MULTI-BAND RESPONSE  ·  20 Hz – 20 kHz",
            curve_height=132 if compact else 148,
            overlap=52 if compact else 58,
            knobs_height=108 if compact else 120,
        )
        self.eq_curve = self._stage.curve
        # Curve drag adjusts gains (freq fixed for tone sculpt shelves)
        self.eq_curve.bandDragged.connect(self._on_curve_drag)
        self.eq_curve.bandReleased.connect(self._on_curve_release)

        row = QHBoxLayout(self._stage.overlay)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(Space.SM if compact else Space.MD)
        self.knob_low = CreativeEqKnob("LOW", "120 Hz shelf", color_role="low")
        self.knob_mid = CreativeEqKnob("MID", "1 kHz body", color_role="mid")
        self.knob_high = CreativeEqKnob("HIGH", "8 kHz air", color_role="high")
        # Soft glass so the curve reads through the dials
        for k in (self.knob_low, self.knob_mid, self.knob_high):
            k.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            k.setStyleSheet("background: transparent;")
            k.valueChanged.connect(self._on_knob)
            row.addWidget(k, 0, Qt.AlignHCenter | Qt.AlignVCenter)

        row.addStretch(1)

        # Power light + download column
        side = QVBoxLayout()
        side.setSpacing(6)
        side.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
        self.power = PowerLightButton()
        self.power.toggled.connect(self._on_power)
        side.addWidget(self.power, 0, Qt.AlignHCenter)
        pwr_lbl = QLabel("EQ")
        pwr_lbl.setAlignment(Qt.AlignCenter)
        pwr_lbl.setStyleSheet(
            f"font-size: 8px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};"
            f" background: transparent;"
        )
        side.addWidget(pwr_lbl)
        self.btn_download = QPushButton("↓")
        self.btn_download.setFixedSize(36, 28)
        self.btn_download.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_download.setToolTip("Download EQ'd version (WAV)")
        self.btn_download.setStyleSheet(f"""
            QPushButton {{
                background: {Color.with_alpha(Color.ELEVATED, 0.92)}; color: {Color.TEXT};
                border: 1px solid {Color.LINE}; border-radius: 8px;
                font-weight: 800; font-size: 14px;
            }}
            QPushButton:hover {{
                border-color: {Color.ACCENT}; color: {Color.ACCENT};
                background: {Color.HOVER};
            }}
        """)
        self.btn_download.clicked.connect(self.downloadRequested.emit)
        side.addWidget(self.btn_download, 0, Qt.AlignHCenter)
        dl_lbl = QLabel("SAVE")
        dl_lbl.setAlignment(Qt.AlignCenter)
        dl_lbl.setStyleSheet(
            f"font-size: 8px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};"
            f" background: transparent;"
        )
        side.addWidget(dl_lbl)
        row.addLayout(side)
        lay.addWidget(self._stage)

        actions = QHBoxLayout()
        self.btn_reset = QPushButton("Zero")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.setFixedHeight(28)
        self.btn_reset.setToolTip("Reset all bands to 0 dB")
        self.btn_reset.clicked.connect(self.reset)
        actions.addWidget(self.btn_reset)
        actions.addStretch()
        lay.addLayout(actions)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        # Bake (home) still settles; live studio listens to gainsChanged immediately
        self._debounce.setInterval(120)
        self._debounce.timeout.connect(self._after_knob_settle)

        self._syncing_curve = False
        self._sync_curve()
        self.eq_curve.set_powered(False)

    def is_powered(self) -> bool:
        return self.power.isOn()

    def gains(self) -> tuple[float, float, float]:
        return (
            self.knob_low.value(),
            self.knob_mid.value(),
            self.knob_high.value(),
        )

    def set_status_hint(self, text: str):
        self._hint.setText(text)

    def _tone_bands(self) -> list[dict]:
        lo, mid, hi = self.gains()
        return [
            {
                "label": "LOW",
                "type": "lowshelf",
                "freq": 120.0,
                "gain_db": lo,
                "q": 0.7,
                "on": True,
                "color_role": "low",
            },
            {
                "label": "MID",
                "type": "peaking",
                "freq": 1000.0,
                "gain_db": mid,
                "q": 1.0,
                "on": True,
                "color_role": "mid",
            },
            {
                "label": "HIGH",
                "type": "highshelf",
                "freq": 8000.0,
                "gain_db": hi,
                "q": 0.7,
                "on": True,
                "color_role": "high",
            },
        ]

    def _sync_curve(self) -> None:
        if self._syncing_curve:
            return
        self.eq_curve.set_bands(self._tone_bands())
        self.eq_curve.set_powered(self.power.isOn())

    def _on_curve_drag(self, idx: int, freq: float, gain_db: float) -> None:
        """Handle drag on response nodes — gains only (tone freqs are fixed)."""
        self._syncing_curve = True
        knobs = (self.knob_low, self.knob_mid, self.knob_high)
        if 0 <= idx < 3:
            knobs[idx].setValue(gain_db, emit=False)
            knobs[idx].update()
        self._syncing_curve = False
        # Update other handles via set_bands but keep dragged node position
        bands = self._tone_bands()
        if 0 <= idx < len(bands):
            bands[idx]["gain_db"] = gain_db
        self.eq_curve.set_bands(bands)
        lo, mid, hi = self.gains()
        self.gainsChanged.emit(lo, mid, hi)
        if self.power.isOn():
            self._hint.setText(f"on  L{lo:+.0f} M{mid:+.0f} H{hi:+.0f} dB")
        self._debounce.start()

    def _on_curve_release(self, *_):
        self._sync_curve()

    def reset(self):
        self.knob_low.setValue(0.0, emit=False)
        self.knob_mid.setValue(0.0, emit=False)
        self.knob_high.setValue(0.0, emit=False)
        self.knob_low.update()
        self.knob_mid.update()
        self.knob_high.update()
        self._sync_curve()
        self.gainsChanged.emit(0.0, 0.0, 0.0)
        if self.power.isOn():
            self.applyRequested.emit()
        self._hint.setText("powered · flat" if self.power.isOn() else "power off · dry signal")

    def _on_power(self, on: bool):
        lo, mid, hi = self.gains()
        self.gainsChanged.emit(lo, mid, hi)
        self.eq_curve.set_powered(on)
        self._sync_curve()
        # Always notify host — ON applies EQ, OFF restores dry
        self.applyRequested.emit()
        if on:
            self._hint.setText("powered on · hearing sculpt")
        else:
            self._hint.setText("power off · dry signal")

    def _on_knob(self, *_):
        # Live path (Studio LiveFX) needs every tick — emit gains immediately.
        # Home bake still uses debounced applyRequested so we don't re-render WAV
        # on every pixel of drag.
        lo, mid, hi = self.gains()
        self.gainsChanged.emit(lo, mid, hi)
        if not self._syncing_curve:
            self._sync_curve()
        if self.power.isOn():
            self._hint.setText(f"on  L{lo:+.0f} M{mid:+.0f} H{hi:+.0f} dB")
        self._debounce.start()

    def _after_knob_settle(self):
        lo, mid, hi = self.gains()
        # Bake / offline reapply after knob settles (home player)
        if self.power.isOn():
            self.applyRequested.emit()
            self._hint.setText(f"on  L{lo:+.0f} M{mid:+.0f} H{hi:+.0f} dB")
        else:
            self._hint.setText("power off · flip the light to hear")


def apply_eq_pedalboard(
    source: Path | str,
    dest: Path | str,
    low_db: float,
    mid_db: float,
    high_db: float,
) -> dict[str, Any]:
    """
    Bake 3-band EQ with Pedalboard → dest WAV (24-bit).
    LOW = low shelf 120 Hz, MID = peak 1 kHz, HIGH = high shelf 8 kHz.
    """
    src = Path(source)
    out = Path(dest)
    result: dict[str, Any] = {"ok": False, "dest": str(out), "error": None}
    if not src.is_file():
        result["error"] = "missing source"
        return result
    try:
        import pedalboard as pb
        import soundfile as sf
        from pedalboard.io import AudioFile
    except Exception as exc:
        result["error"] = f"pedalboard: {exc}"
        return result
    try:
        with AudioFile(str(src)) as f:
            audio = f.read(f.frames)
            sr = int(f.samplerate)
        plugins = []
        if abs(low_db) >= 0.05:
            plugins.append(
                pb.LowShelfFilter(cutoff_frequency_hz=120.0, gain_db=float(low_db), q=0.7)
            )
        if abs(mid_db) >= 0.05:
            plugins.append(
                pb.PeakFilter(cutoff_frequency_hz=1000.0, gain_db=float(mid_db), q=0.9)
            )
        if abs(high_db) >= 0.05:
            plugins.append(
                pb.HighShelfFilter(cutoff_frequency_hz=8000.0, gain_db=float(high_db), q=0.7)
            )
        if plugins:
            board = pb.Pedalboard(plugins)
            rendered = board(audio, sr)
        else:
            rendered = audio
        rendered = np.asarray(rendered, dtype=np.float32)
        out.parent.mkdir(parents=True, exist_ok=True)
        # (ch, n) → (n, ch)
        if rendered.ndim == 1:
            sf.write(str(out), rendered, sr, subtype="PCM_24")
        else:
            sf.write(str(out), rendered.T, sr, subtype="PCM_24")
        result["ok"] = out.is_file()
        if not result["ok"]:
            result["error"] = "write failed"
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result
