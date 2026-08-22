"""
CoProducer Studio Player - premium inspect + playback window.

Features:
  - Specs panel from analysis report / file probe
  - High-quality waveform with playhead + lookahead glow
  - Click-to-seek, drag selection region
  - Transport: play / pause / stop / rewind
  - Tools: trim selection (ffmpeg), convert, open folder
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF, QRect, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import IconWidget
from .theme import Color, Radius, Space, Type


def _fmt_time(ms: int | float) -> str:
    ms = max(0, int(ms))
    s, ms_r = divmod(ms, 1000)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}.{ms_r // 10:02d}"
    return f"{m:d}:{s:02d}.{ms_r // 10:02d}"


def _load_peaks(path: Path, n_bins: int = 1800) -> tuple[list[float], float]:
    """Return (peak envelope 0..1, duration_sec)."""
    duration = 0.0
    try:
        import soundfile as sf

        info = sf.info(str(path))
        duration = float(info.duration or 0)
        # Downsample by block reads for speed
        frames = info.frames
        if frames <= 0:
            return [0.0] * n_bins, duration
        block = max(1, frames // n_bins)
        peaks: list[float] = []
        with sf.SoundFile(str(path)) as f:
            while len(peaks) < n_bins:
                data = f.read(block, dtype="float32", always_2d=True)
                if data.size == 0:
                    break
                mono = data.mean(axis=1)
                peaks.append(float(np.max(np.abs(mono))))
        if not peaks:
            peaks = [0.0]
        # pad / trim
        if len(peaks) < n_bins:
            peaks.extend([0.0] * (n_bins - len(peaks)))
        peaks = peaks[:n_bins]
        mx = max(peaks) or 1.0
        return [p / mx for p in peaks], duration
    except Exception:
        pass
    # Fallback: librosa
    try:
        import librosa

        y, sr = librosa.load(str(path), sr=22050, mono=True)
        duration = float(len(y) / sr) if sr else 0.0
        if len(y) == 0:
            return [0.0] * n_bins, duration
        hop = max(1, len(y) // n_bins)
        peaks = []
        for i in range(n_bins):
            chunk = y[i * hop : (i + 1) * hop]
            peaks.append(float(np.max(np.abs(chunk))) if len(chunk) else 0.0)
        mx = max(peaks) or 1.0
        return [p / mx for p in peaks], duration
    except Exception:
        return [0.0] * n_bins, duration


# =============================================================================
# Interactive waveform canvas
# =============================================================================


class WaveformCanvas(QWidget):
    """Studio waveform: full color, glass selection, zoom, trim presets support."""

    seekRequested = Signal(float)  # seconds
    selectionChanged = Signal(object, object)  # start_s|None, end_s|None
    viewChanged = Signal(float, float)  # view_start, view_end

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._peaks: list[float] = []
        self._duration = 0.0  # seconds (full file)
        self._position = 0.0  # seconds
        self._sel_a: float | None = None
        self._sel_b: float | None = None
        self._drag_origin: float | None = None
        self._lookahead = 0.35
        self._hover_x: float | None = None
        # Zoom window into the full timeline (seconds)
        self._view_start = 0.0
        self._view_end = 0.0  # 0 → means full duration
        self._drag_mode: str | None = None  # "select" | "pan"
        self._pan_origin_x = 0.0
        self._pan_origin_view = 0.0
        # Artifact markers: [{start_s, end_s, kind, confidence}, ...]
        self._markers: list[dict] = []

    def set_peaks(self, peaks: list[float], duration: float):
        self._peaks = peaks or []
        self._duration = max(0.0, float(duration))
        if self._view_end <= 0 or self._view_end > self._duration:
            self._view_start = 0.0
            self._view_end = self._duration
        self.update()

    def set_markers(self, markers: list[dict] | None):
        """Overlay artifact hits on the waveform (Studio repair intelligence)."""
        self._markers = list(markers or [])
        self.update()

    def set_position(self, seconds: float):
        self._position = max(0.0, min(float(seconds), self._duration or 0.0))
        # Auto-follow playhead if outside view
        vs, ve = self.view_range()
        if self._position < vs or self._position > ve:
            span = ve - vs
            self._view_start = max(0.0, self._position - span * 0.35)
            self._view_end = min(self._duration, self._view_start + span)
            if self._view_end - self._view_start < span and self._duration > 0:
                self._view_start = max(0.0, self._view_end - span)
        self.update()

    def set_lookahead(self, seconds: float):
        self._lookahead = max(0.0, float(seconds))
        self.update()

    def view_range(self) -> tuple[float, float]:
        if self._duration <= 0:
            return 0.0, 0.0
        vs = max(0.0, self._view_start)
        ve = self._view_end if self._view_end > vs else self._duration
        ve = min(self._duration, max(ve, vs + 0.05))
        return vs, ve

    def set_view(self, start: float, end: float):
        if self._duration <= 0:
            return
        a, b = sorted((float(start), float(end)))
        span = max(0.05, b - a)
        a = max(0.0, min(a, self._duration - span))
        b = a + span
        self._view_start = a
        self._view_end = min(self._duration, b)
        self.viewChanged.emit(self._view_start, self._view_end)
        self.update()

    def zoom(self, factor: float, anchor: float | None = None):
        """factor < 1 zooms in, > 1 zooms out. Anchor = time under cursor."""
        if self._duration <= 0:
            return
        vs, ve = self.view_range()
        span = ve - vs
        mid = anchor if anchor is not None else (vs + ve) / 2.0
        new_span = max(0.08, min(self._duration, span * factor))
        # Keep anchor stable
        ratio = 0.0 if span <= 0 else (mid - vs) / span
        new_start = mid - new_span * ratio
        new_end = new_start + new_span
        if new_start < 0:
            new_start = 0.0
            new_end = new_span
        if new_end > self._duration:
            new_end = self._duration
            new_start = max(0.0, new_end - new_span)
        self.set_view(new_start, new_end)

    def zoom_in(self):
        self.zoom(0.6)

    def zoom_out(self):
        self.zoom(1.6)

    def zoom_reset(self):
        self.set_view(0.0, self._duration)

    def selection(self) -> tuple[float | None, float | None]:
        if self._sel_a is None or self._sel_b is None:
            return None, None
        a, b = sorted((self._sel_a, self._sel_b))
        if abs(b - a) < 0.01:
            return None, None
        return a, b

    def set_selection(self, start: float, end: float):
        a, b = sorted((float(start), float(end)))
        a = max(0.0, min(a, self._duration))
        b = max(0.0, min(b, self._duration))
        self._sel_a, self._sel_b = a, b
        self.selectionChanged.emit(a, b)
        self.update()

    def set_selection_block(self, duration_s: float, start: float | None = None):
        """Place a movable trim block of fixed length (15/30/60s presets)."""
        if self._duration <= 0:
            return
        dur = max(0.05, float(duration_s))
        if start is None:
            # Center on playhead, clamped to file
            start = max(0.0, min(self._position, max(0.0, self._duration - dur)))
        start = max(0.0, min(float(start), max(0.0, self._duration - min(dur, self._duration))))
        end = min(self._duration, start + dur)
        self.set_selection(start, end)
        # Zoom so the block is easy to edit (padding around selection)
        pad = max(0.5, (end - start) * 0.4)
        self.set_view(max(0.0, start - pad), min(self._duration, end + pad))

    def clear_selection(self):
        self._sel_a = self._sel_b = None
        self.selectionChanged.emit(None, None)
        self.update()

    def _x_to_time(self, x: float) -> float:
        vs, ve = self.view_range()
        span = max(1e-6, ve - vs)
        w = max(1, self.width())
        t = vs + (x / w) * span
        return max(0.0, min(t, self._duration))

    def _time_to_x(self, t: float) -> float:
        vs, ve = self.view_range()
        span = max(1e-6, ve - vs)
        return ((t - vs) / span) * self.width()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2.0
        vs, ve = self.view_range()

        # Background
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor("#08080c"))
        bg.setColorAt(1, QColor(Color.SURFACE))
        p.fillRect(self.rect(), bg)

        # Grid
        p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.55)), 1))
        for frac in (0.25, 0.5, 0.75):
            p.drawLine(0, int(h * frac), w, int(h * frac))

        # Waveform — full color always in studio (cyan → purple along view)
        peaks = self._peaks
        n = len(peaks)
        if n > 1 and w > 2 and self._duration > 0:
            path_top = QPainterPath()
            path_bot = QPainterPath()
            amp = h * 0.42
            # Map peak indices to visible view
            i0 = int((vs / self._duration) * (n - 1))
            i1 = int((ve / self._duration) * (n - 1))
            i0 = max(0, min(n - 1, i0))
            i1 = max(i0 + 1, min(n - 1, i1))
            path_top.moveTo(0, mid)
            path_bot.moveTo(0, mid)
            for i in range(i0, i1 + 1):
                t = (i / max(1, n - 1)) * self._duration
                x = self._time_to_x(t)
                y = peaks[i] * amp
                path_top.lineTo(x, mid - y)
                path_bot.lineTo(x, mid + y)
            path_top.lineTo(self._time_to_x(ve), mid)
            path_bot.lineTo(self._time_to_x(ve), mid)
            path_top.closeSubpath()
            path_bot.closeSubpath()

            c0, c1, c2 = Color.wave_stops()
            glow = QColor(c2)
            glow.setAlpha(45)
            p.setBrush(QBrush(glow))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPath(path_top)
            p.drawPath(path_bot)

            fill = QLinearGradient(0, 0, w, 0)
            fill.setColorAt(0.0, QColor(c0))
            fill.setColorAt(0.5, QColor(c1))
            fill.setColorAt(1.0, QColor(c2))
            p.setOpacity(0.9)
            p.setBrush(QBrush(fill))
            p.drawPath(path_top)
            p.drawPath(path_bot)
            p.setOpacity(1.0)

            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(Color.wave_edge()), 1.15))
            for i in range(i0, i1):
                t0 = (i / max(1, n - 1)) * self._duration
                t1 = ((i + 1) / max(1, n - 1)) * self._duration
                p.drawLine(
                    QPointF(self._time_to_x(t0), mid - peaks[i] * amp),
                    QPointF(self._time_to_x(t1), mid - peaks[i + 1] * amp),
                )

        # Artifact markers (repair intelligence)
        if self._markers and self._duration > 0:
            kind_color = {
                "digital": QColor("#f59e0b"),
                "mouth": QColor("#a78bfa"),
                "clip_edge": QColor("#ef4444"),
                "dropout": QColor("#38bdf8"),
            }
            for m in self._markers:
                try:
                    t0 = float(m.get("start_s", 0.0))
                    t1 = float(m.get("end_s", t0))
                    conf = float(m.get("confidence", 0.6))
                    kind = str(m.get("kind") or "digital")
                except Exception:
                    continue
                if t1 < vs or t0 > ve:
                    continue
                x0 = int(self._time_to_x(max(vs, t0)))
                x1 = int(self._time_to_x(min(ve, t1)))
                col = QColor(kind_color.get(kind, "#f59e0b"))
                col.setAlpha(int(40 + 140 * max(0.2, min(1.0, conf))))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawRect(x0, 0, max(2, x1 - x0 + 1), h)
                p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 220), 1))
                p.drawLine(x0, 0, x0, h)

        # Glass selection overlay (see-through highlight)
        sa, sb = self.selection()
        if sa is not None and sb is not None:
            x0, x1 = self._time_to_x(sa), self._time_to_x(sb)
            if x1 < x0:
                x0, x1 = x1, x0
            # Frosted glass panel
            c0, c1, c2 = Color.wave_stops()
            a0, a1, a2 = QColor(c0), QColor(c1), QColor(c2)
            a0.setAlpha(40)
            a1.setAlpha(50)
            a2.setAlpha(36)
            glass = QLinearGradient(x0, 0, x0, h)
            glass.setColorAt(0, QColor(255, 255, 255, 28))
            glass.setColorAt(0.5, a0)
            glass.setColorAt(1, a2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glass)
            p.setOpacity(0.92)
            p.drawRoundedRect(int(x0), 2, max(2, int(x1 - x0)), h - 4, 6, 6)
            p.setOpacity(1.0)
            # Glass edge (hairline + soft outer glow)
            p.setPen(QPen(QColor(255, 255, 255, 120), 1.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(int(x0), 2, max(2, int(x1 - x0)), h - 4, 6, 6)
            p.setPen(QPen(QColor(c0), 1.0))
            p.drawLine(int(x0), 0, int(x0), h)
            p.setPen(QPen(QColor(c1), 1.0))
            p.drawLine(int(x1), 0, int(x1), h)
            # Duration chip
            p.setPen(QColor("#e4e4e7"))
            font = QFont()
            font.setPointSize(8)
            font.setBold(True)
            p.setFont(font)
            p.drawText(int(x0) + 6, 16, f"{sb - sa:.2f}s trim")

        # Lookahead
        if self._duration > 0 and self._lookahead > 0:
            x_ph = self._time_to_x(self._position)
            x_la = self._time_to_x(min(self._duration, self._position + self._lookahead))
            if x_la > x_ph:
                c0, c1, _ = Color.wave_stops()
                grad = QLinearGradient(x_ph, 0, x_la, 0)
                grad.setColorAt(0, QColor(Color.with_alpha(c0, 0.25)))
                grad.setColorAt(1, QColor(Color.with_alpha(c1, 0.02)))
                p.fillRect(int(x_ph), 0, max(1, int(x_la - x_ph)), h, grad)

        # Center line
        p.setPen(QPen(QColor(Color.with_alpha(Color.MUTED, 0.35)), 1))
        p.drawLine(0, int(mid), w, int(mid))

        # Playhead
        if self._duration > 0 and vs <= self._position <= ve:
            x = int(self._time_to_x(self._position))
            p.setPen(QPen(QColor(Color.WHITE), 2))
            p.drawLine(x, 0, x, h)
            p.setBrush(QColor(Color.wave_edge()))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(x - 4, 2, 8, 8)

        # Hover
        if self._hover_x is not None:
            p.setPen(QPen(QColor(Color.with_alpha(Color.MUTED, 0.5)), 1, Qt.PenStyle.DashLine))
            p.drawLine(int(self._hover_x), 0, int(self._hover_x), h)

        # Time ticks for view
        p.setPen(QColor(Color.MUTED))
        font = QFont()
        font.setPointSize(8)
        p.setFont(font)
        if self._duration > 0:
            steps = 6
            span = max(1e-6, ve - vs)
            for i in range(steps + 1):
                t = vs + span * i / steps
                x = int(self._time_to_x(t))
                p.drawText(x + 2, h - 4, _fmt_time(t * 1000))

        # Zoom badge
        if self._duration > 0 and (ve - vs) < self._duration * 0.999:
            p.setPen(QColor(Color.ACCENT_SOFT))
            p.drawText(8, 14, f"zoom  {(self._duration / max(0.05, ve - vs)):.1f}×")

        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.MiddleButton or (
            event.button() == Qt.MouseButton.LeftButton
            and event.modifiers() & Qt.KeyboardModifier.AltModifier
        ):
            self._drag_mode = "pan"
            self._pan_origin_x = event.position().x()
            self._pan_origin_view = self.view_range()[0]
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            t = self._x_to_time(event.position().x())
            self._drag_mode = "select"
            self._drag_origin = t
            self._sel_a = t
            self._sel_b = t
            self.seekRequested.emit(t)
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.clear_selection()

    def mouseMoveEvent(self, event: QMouseEvent):
        self._hover_x = event.position().x()
        if self._drag_mode == "pan" and event.buttons():
            vs, ve = self.view_range()
            span = ve - vs
            dx = event.position().x() - self._pan_origin_x
            dt = -(dx / max(1, self.width())) * span
            self.set_view(self._pan_origin_view + dt, self._pan_origin_view + dt + span)
            event.accept()
            return
        if self._drag_mode == "select" and event.buttons() & Qt.MouseButton.LeftButton:
            t = self._x_to_time(event.position().x())
            self._sel_b = t
            a, b = self.selection()
            self.selectionChanged.emit(a, b)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_mode == "select":
            if self._drag_origin is not None:
                t = self._x_to_time(event.position().x())
                if abs(t - self._drag_origin) < 0.02:
                    self._sel_a = self._sel_b = None
                    self.selectionChanged.emit(None, None)
                    self.seekRequested.emit(self._drag_origin)
                else:
                    self._sel_b = t
                    a, b = self.selection()
                    self.selectionChanged.emit(a, b)
            self._drag_origin = None
            self._drag_mode = None
            self.update()
        if event.button() == Qt.MouseButton.MiddleButton:
            self._drag_mode = None

    def leaveEvent(self, event):
        self._hover_x = None
        self.update()

    def wheelEvent(self, event: QWheelEvent):
        if not self._duration:
            return
        # Ctrl+wheel = zoom for cleaner cuts; plain wheel = scrub
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            anchor = self._x_to_time(event.position().x())
            factor = 0.85 if event.angleDelta().y() > 0 else 1.18
            self.zoom(factor, anchor=anchor)
            event.accept()
            return
        delta = event.angleDelta().y()
        vs, ve = self.view_range()
        step = max(0.02, (ve - vs) * 0.02) * (-1 if delta < 0 else 1)
        self.seekRequested.emit(max(0.0, min(self._duration, self._position + step)))
        event.accept()


# =============================================================================
# Studio player dialog
# =============================================================================


class StudioPlayerWindow(QDialog):
    """Inspect + play a mix with premium transport and tools.

    Opens full-height at the top of the screen, docked flush to the right
    edge of the main CoProducer window. Transport lives over the waveform.
    """

    # Emitted just before play starts so the main window can stop other decks
    aboutToPlay = Signal()

    def __init__(
        self,
        parent: QWidget | None,
        path: str | Path,
        report: dict[str, Any] | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("CoProducer Studio Player")
        self.setModal(False)
        self.setMinimumSize(780, 640)
        # Prefer a tool window that can sit beside the main app
        try:
            self.setWindowFlag(Qt.WindowType.Window, True)
        except Exception:
            pass

        self._path = Path(path)
        self._report = report or {}
        self._duration_ms = 0
        self._playing = False

        self.setStyleSheet(f"""
            QDialog {{
                background: {Color.BG};
                color: {Color.TEXT};
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.25)};
            }}
            QLabel {{ background: transparent; color: {Color.TEXT}; }}
            QToolButton {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: 8px;
                padding: 8px;
                min-width: 40px;
                min-height: 40px;
            }}
            QToolButton:hover {{
                background: {Color.HOVER};
                border-color: {Color.ACCENT};
            }}
            QToolButton#PrimaryTransport {{
                background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
                    stop:0 {Color.ACCENT_DIM}, stop:1 {Color.ACCENT});
                border: none;
                min-width: 52px;
                min-height: 52px;
                border-radius: 26px;
            }}
            QToolButton#PrimaryTransport:hover {{
                background: {Color.ACCENT_SOFT};
            }}
            QFrame#TransportBar {{
                background: {Color.with_alpha(Color.ELEVATED, 0.92)};
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.28)};
                border-radius: 12px;
            }}
            QPushButton {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: 8px;
                padding: 8px 14px;
                color: {Color.TEXT};
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {Color.ACCENT};
                background: {Color.HOVER};
            }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {Color.LINE};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 14px; height: 14px; margin: -5px 0;
                background: {Color.ACCENT};
                border-radius: 7px;
            }}
            QSlider::sub-page:horizontal {{
                background: {Color.ACCENT};
                border-radius: 2px;
            }}
            QDoubleSpinBox {{
                background: {Color.SURFACE};
                border: 1px solid {Color.LINE};
                border-radius: 6px;
                padding: 4px 8px;
                color: {Color.TEXT};
            }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.MD)
        root.setSpacing(Space.SM)

        # ---- Compact header ----
        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self._name_lbl = QLabel(self._path.name)
        self._name_lbl.setStyleSheet(
            f"font-size: {Type.H2}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"font-family: {Type.DISPLAY}; letter-spacing: -0.3px;"
        )
        title_col.addWidget(self._name_lbl)
        self._path_lbl = QLabel(str(self._path))
        self._path_lbl.setStyleSheet(f"font-size: {Type.TINY}px; color: {Color.MUTED};")
        self._path_lbl.setWordWrap(True)
        title_col.addWidget(self._path_lbl)
        hdr.addLayout(title_col, 1)

        score = self._report.get("score")
        if score is not None:
            sc = QLabel(f"{int(score)}")
            sc.setAlignment(Qt.AlignCenter)
            sc.setFixedSize(48, 48)
            sc.setStyleSheet(
                f"font-size: 18px; font-weight: 700; font-family: {Type.DISPLAY}; "
                f"color: {Color.ACCENT}; background: {Color.ELEVATED}; "
                f"border: 1px solid {Color.with_alpha(Color.ACCENT, 0.4)}; border-radius: 24px;"
            )
            hdr.addWidget(sc)
        root.addLayout(hdr)

        # ---- Specs (compact) ----
        specs = self._build_specs()
        root.addWidget(specs)

        # ---- Waveform + transport overlaid at top of wave stage ----
        wave_frame = QFrame()
        wave_frame.setStyleSheet(f"""
            QFrame {{
                background: {Color.SURFACE};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        wl = QVBoxLayout(wave_frame)
        wl.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.MD)
        wl.setSpacing(Space.XS)

        # Transport bar — top of waveform section (play toolbar over the wave)
        transport_bar = QFrame()
        transport_bar.setObjectName("TransportBar")
        transport_bar.setFixedHeight(64)
        tlay = QHBoxLayout(transport_bar)
        tlay.setContentsMargins(10, 6, 10, 6)
        tlay.setSpacing(Space.SM)

        self._btn_rew = self._tool_btn("rewind", "Rewind to start", self._rewind)
        self._btn_stop = self._tool_btn("stop", "Stop", self._stop)
        self._btn_play = self._tool_btn("play", "Play / Pause", self._toggle_play, primary=True)
        tlay.addWidget(self._btn_rew)
        tlay.addWidget(self._btn_stop)
        tlay.addWidget(self._btn_play)
        tlay.addSpacing(10)

        # Time + scrub on the transport bar
        self._time_lbl = QLabel("0:00.00")
        self._time_lbl.setStyleSheet(
            f"font-family: {Type.MONO}; font-size: {Type.BODY}px; color: {Color.ACCENT_SOFT};"
        )
        self._dur_lbl = QLabel("/ 0:00.00")
        self._dur_lbl.setStyleSheet(
            f"font-family: {Type.MONO}; font-size: {Type.BODY}px; color: {Color.MUTED};"
        )
        tlay.addWidget(self._time_lbl)
        tlay.addWidget(self._dur_lbl)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, 1000)
        self._slider.setMinimumWidth(120)
        self._slider.sliderMoved.connect(self._on_slider_moved)
        self._slider.sliderPressed.connect(lambda: setattr(self, "_scrubbing", True))
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._scrubbing = False
        tlay.addWidget(self._slider, 1)

        self._sel_lbl = QLabel("No selection")
        self._sel_lbl.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.MUTED};")
        tlay.addWidget(self._sel_lbl)
        tlay.addSpacing(8)

        self._btn_trim = self._tool_btn("cut", "Trim selection to new file", self._trim_selection)
        self._btn_convert = self._tool_btn("convert", "Convert format (ffmpeg)", self._convert)
        self._btn_folder = self._tool_btn("folder", "Open containing folder", self._open_folder)
        self._btn_reveal = self._tool_btn("external", "Reveal in Explorer", self._reveal)
        tlay.addWidget(self._btn_trim)
        tlay.addWidget(self._btn_convert)
        tlay.addWidget(self._btn_folder)
        tlay.addWidget(self._btn_reveal)
        tlay.addSpacing(6)
        self._btn_hunter = self._tool_btn("alert", "Artifact Hunter (clicks / DC / dropouts)", self._open_artifact_hunter)
        self._btn_bleedfix = self._tool_btn("waveform", "Bleedfix (gate mic bleed)", self._open_bleedfix)
        tlay.addWidget(self._btn_hunter)
        tlay.addWidget(self._btn_bleedfix)
        wl.addWidget(transport_bar)

        # Waveform chrome
        wh = QHBoxLayout()
        wtitle = QLabel(
            "WAVEFORM  ·  drag glass trim  ·  Ctrl+wheel zoom  ·  Alt-drag pan"
        )
        wtitle.setStyleSheet(
            f"font-size: {Type.TINY}px; font-weight: 600; letter-spacing: 1.2px; "
            f"color: {Color.MUTED};"
        )
        wh.addWidget(wtitle)
        wh.addStretch()
        la_lbl = QLabel("Lookahead")
        la_lbl.setStyleSheet(f"font-size: {Type.TINY}px; color: {Color.MUTED};")
        wh.addWidget(la_lbl)
        self._lookahead_spin = QDoubleSpinBox()
        self._lookahead_spin.setRange(0.0, 5.0)
        self._lookahead_spin.setSingleStep(0.05)
        self._lookahead_spin.setDecimals(2)
        self._lookahead_spin.setSuffix(" s")
        self._lookahead_spin.setValue(0.35)
        self._lookahead_spin.setFixedWidth(90)
        self._lookahead_spin.valueChanged.connect(self._on_lookahead)
        wh.addWidget(self._lookahead_spin)
        wl.addLayout(wh)

        self.canvas = WaveformCanvas()
        self.canvas.setMinimumHeight(140)
        self.canvas.seekRequested.connect(self._seek_seconds)
        self.canvas.selectionChanged.connect(self._on_selection)
        wl.addWidget(self.canvas, 1)

        # Trim presets + zoom under waveform
        tools = QHBoxLayout()
        tools.setSpacing(Space.SM)
        tlab = QLabel("Trim block")
        tlab.setStyleSheet(f"font-size: 10px; color: {Color.MUTED}; font-weight: 600;")
        tools.addWidget(tlab)
        for label, secs in (("15s", 15.0), ("30s", 30.0), ("1 min", 60.0)):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.setToolTip(f"Set a movable {label} selection at the playhead (glass highlight)")
            b.clicked.connect(lambda _=False, s=secs: self._preset_trim(s))
            tools.addWidget(b)
        tools.addSpacing(12)
        zlab = QLabel("Zoom")
        zlab.setStyleSheet(f"font-size: 10px; color: {Color.MUTED}; font-weight: 600;")
        tools.addWidget(zlab)
        for label, slot_name in (("−", "zoom_out"), ("+", "zoom_in"), ("Fit", "zoom_reset")):
            b = QPushButton(label)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(28)
            b.setMinimumWidth(36)
            b.setToolTip(
                "Zoom out"
                if slot_name == "zoom_out"
                else "Zoom in for cleaner cuts"
                if slot_name == "zoom_in"
                else "Show full file"
            )
            if slot_name == "zoom_in":
                b.clicked.connect(self.canvas.zoom_in)
            elif slot_name == "zoom_out":
                b.clicked.connect(self.canvas.zoom_out)
            else:
                b.clicked.connect(self.canvas.zoom_reset)
            tools.addWidget(b)
        tools.addStretch()
        btn_master = QPushButton("Master export…")
        btn_master.setCursor(Qt.PointingHandCursor)
        btn_master.setFixedHeight(28)
        btn_master.setToolTip("Normalize to a streaming preset (−14 / −16 / −9 LUFS) with true-peak limit")
        btn_master.clicked.connect(self._open_master_export)
        tools.addWidget(btn_master)
        tip = QLabel("Right-click clear · Ctrl+wheel zoom · Alt-drag pan")
        tip.setStyleSheet(f"font-size: 10px; color: {Color.MUTED};")
        tools.addWidget(tip)
        wl.addLayout(tools)

        root.addWidget(wave_frame, 1)

        # ---- Creative EQ knobs (LOW / MID / HIGH) ----
        from .eq_knobs import CreativeEqStrip

        self.eq_strip = CreativeEqStrip(compact=False)
        self.eq_strip.applyRequested.connect(self._apply_studio_eq)
        self.eq_strip.gainsChanged.connect(self._apply_studio_eq)
        self.eq_strip.downloadRequested.connect(self._download_studio_eq)
        self._eq_source_clean = str(self._path.resolve())
        self._eq_baked_path: Path | None = None
        root.addWidget(self.eq_strip)

        # ---- Studio FX (artifact hunter · bleedfix · EQ · presets) ----
        from .studio_fx_panel import StudioFxPanel
        from PySide6.QtWidgets import QScrollArea

        project_root = Path(__file__).resolve().parents[3]
        self.fx_panel = StudioFxPanel(project_root)
        self.fx_panel.set_current_path(self._path)
        self.fx_panel.renderRequested.connect(self._render_studio_fx)
        self.fx_panel.saveRequested.connect(self._save_studio_fx)
        self.fx_panel.liveChanged.connect(self._push_live)
        self.fx_panel.artifactMarkersReady.connect(self._on_artifact_markers)
        fx_scroll = QScrollArea()
        fx_scroll.setWidgetResizable(True)
        fx_scroll.setFrameShape(QFrame.Shape.NoFrame)
        fx_scroll.setMinimumHeight(320)
        fx_scroll.setWidget(self.fx_panel)
        root.addWidget(fx_scroll, 2)
        self.fx_panel.load_vault()

        # Status
        self._status = QLabel("")
        self._status.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.MUTED};")
        root.addWidget(self._status)

        # Dock geometry after first show (needs parent/screen metrics)
        QTimer.singleShot(0, self.place_beside_main)

        # ---- Realtime FX engine (streams + processes live, no restart) ----
        from nodaw.audio.live_fx import LiveFxEngine

        self._player = LiveFxEngine(self)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.errorOccurred.connect(self._on_error)
        self._player.liveSummary.connect(self._on_live_summary)
        self._fx_source_clean = str(self._path.resolve())
        self._player.setSource(self._path)
        # Seed dry settings so first play is bit-transparent until auto-dial runs
        try:
            self._push_live()
        except Exception:
            pass

        # Load waveform async-ish via timer so dialog paints first
        QTimer.singleShot(30, self._load_waveform)

    def closeEvent(self, event):
        try:
            self._player.shutdown()
        except Exception:
            pass
        super().closeEvent(event)

    def place_beside_main(self) -> None:
        """
        Open full-height from the top of the screen, docked to the right of
        the main CoProducer window (left edge flush with main's right edge).
        If there isn't enough room, align the studio's right edge with the
        main window's right edge instead.
        """
        try:
            parent = self.parentWidget()
            # Prefer the top-level main window frame
            main = parent.window() if parent is not None else None
            if main is None or main is self:
                main = parent
            screen = None
            if main is not None:
                screen = main.screen()
            if screen is None:
                screen = QGuiApplication.primaryScreen()
            if screen is None:
                return
            avail: QRect = screen.availableGeometry()
            top = int(avail.top())
            height = int(avail.height())
            # Desired studio width (comfortable for EQ + FX)
            want_w = 980
            min_w = 820

            if main is not None:
                # Use frameGeometry so we flush against the visible chrome edge
                try:
                    mg = main.frameGeometry()
                except Exception:
                    mg = main.geometry()
                main_right = int(mg.right())
                room_right = int(avail.right()) - main_right
                if room_right >= min_w:
                    # Dock to the right of CoProducer — left edge flush with main right
                    left = main_right
                    width = min(want_w, room_right)
                else:
                    # Not enough space to the right: sit over the right side of main,
                    # right edge flush with main's right edge (or screen right)
                    width = min(want_w, max(min_w, int(avail.width() * 0.48)))
                    left = main_right - width + 1
                    if left < avail.left():
                        left = int(avail.left())
                        width = min(width, int(avail.right()) - left)
            else:
                width = min(want_w, max(min_w, int(avail.width() * 0.45)))
                left = int(avail.right()) - width

            # Clamp into available geometry
            if left + width > avail.right():
                width = max(min_w, int(avail.right()) - left)
            if height < 600:
                height = int(avail.height())
            self.setGeometry(int(left), top, int(width), height)
            self.raise_()
            self.activateWindow()
        except Exception:
            # Fallback: full available height on primary screen right half
            try:
                scr = QGuiApplication.primaryScreen()
                if scr is None:
                    return
                ag = scr.availableGeometry()
                w = max(820, ag.width() // 2)
                self.setGeometry(ag.right() - w, ag.top(), w, ag.height())
            except Exception:
                pass

    def showEvent(self, event):
        super().showEvent(event)
        # Re-dock once on first show (screen metrics are reliable after map)
        if not getattr(self, "_placed_once", False):
            self._placed_once = True
            QTimer.singleShot(30, self.place_beside_main)

    def _tool_btn(self, icon: str, tip: str, slot, primary: bool = False) -> QToolButton:
        b = QToolButton()
        if primary:
            b.setObjectName("PrimaryTransport")
        b.setToolTip(tip)
        b.setCursor(Qt.PointingHandCursor)
        # Embed icon via layout
        wrap = QVBoxLayout(b)
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setAlignment(Qt.AlignCenter)
        color = Color.BG if primary else Color.TEXT
        size = 22 if primary else 18
        ico = IconWidget(icon, size=size, color=color)
        wrap.addWidget(ico, 0, Qt.AlignCenter)
        b._icon = ico
        b.clicked.connect(slot)
        return b

    def _build_specs(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.LG}px;
            }}
        """)
        grid = QGridLayout(frame)
        grid.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        grid.setHorizontalSpacing(Space.XL)
        grid.setVerticalSpacing(6)

        track = self._report.get("track") if isinstance(self._report.get("track"), dict) else {}
        audio = (track or {}).get("audio") or {}
        metrics = (track or {}).get("metrics") or {}
        loud = (metrics.get("loudness") or {}) if isinstance(metrics, dict) else {}

        pairs = [
            ("Format", audio.get("format_name") or self._path.suffix.lstrip(".").upper() or "-"),
            ("Codec", audio.get("codec_name") or "-"),
            (
                "Sample rate",
                f"{audio.get('sample_rate_hz', '-')} Hz" if audio.get("sample_rate_hz") else "-",
            ),
            ("Bit depth", f"{audio.get('bit_depth')}-bit" if audio.get("bit_depth") else "-"),
            ("Channels", audio.get("channels") or "-"),
            (
                "Bitrate",
                f"{int(audio.get('bit_rate_bps') / 1000)} kbps"
                if audio.get("bit_rate_bps")
                else "-",
            ),
            ("Duration", f"{audio.get('duration_seconds', '-')} s"),
            ("Size", self._file_size()),
            ("LUFS", loud.get("integrated_lufs", "-")),
            ("True Peak", loud.get("true_peak_dbtp", "-")),
            ("Peak", metrics.get("peak_dbfs", "-") if isinstance(metrics, dict) else "-"),
            ("RMS", metrics.get("rms_dbfs", "-") if isinstance(metrics, dict) else "-"),
            ("Score", self._report.get("score", "-")),
            ("Rating", self._report.get("rating", "-")),
            ("Mode", self._report.get("report_type", "-")),
            ("Run ID", self._report.get("run_id", "-")),
        ]

        # Probe file if report thin
        if not audio.get("sample_rate_hz") and self._path.is_file():
            try:
                import soundfile as sf

                info = sf.info(str(self._path))
                pairs[2] = ("Sample rate", f"{info.samplerate} Hz")
                pairs[4] = ("Channels", info.channels)
                pairs[6] = ("Duration", f"{info.duration:.2f} s")
                pairs[1] = ("Codec", info.subtype or pairs[1][1])
            except Exception:
                pass

        for i, (k, v) in enumerate(pairs):
            row, col = divmod(i, 4)
            cell = QVBoxLayout()
            kl = QLabel(str(k).upper())
            kl.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1px; color: {Color.MUTED};"
            )
            vl = QLabel(str(v) if v is not None else "-")
            vl.setStyleSheet(
                f"font-size: {Type.BODY}px; font-weight: 600; font-family: {Type.MONO}; "
                f"color: {Color.TEXT};"
            )
            cell.addWidget(kl)
            cell.addWidget(vl)
            grid.addLayout(cell, row, col)

        return frame

    def _file_size(self) -> str:
        try:
            n = self._path.stat().st_size
            if n > 1_000_000:
                return f"{n / 1_000_000:.2f} MB"
            return f"{n / 1000:.1f} KB"
        except Exception:
            return "-"

    def _load_waveform(self):
        if not self._path.is_file():
            self._status.setText("File not found on disk.")
            return
        self._status.setText("Loading waveform…")
        peaks, dur = _load_peaks(self._path)
        # Prefer report duration if player duration not ready
        track = self._report.get("track") if isinstance(self._report.get("track"), dict) else {}
        audio = (track or {}).get("audio") or {}
        if audio.get("duration_seconds") and (not dur or dur <= 0):
            dur = float(audio["duration_seconds"])
        # Prefer report waveform peaks if present
        metrics = (track or {}).get("metrics") or {}
        wf = metrics.get("waveform") if isinstance(metrics, dict) else None
        if isinstance(wf, list) and len(wf) > 8:
            try:
                arr = [abs(float(x)) for x in wf]
                mx = max(arr) or 1.0
                peaks = [v / mx for v in arr]
            except Exception:
                pass
        self.canvas.set_peaks(peaks, dur)
        if dur:
            self._duration_ms = int(dur * 1000)
            self._dur_lbl.setText(f"/ {_fmt_time(self._duration_ms)}")
            self._slider.setRange(0, self._duration_ms)
        self._status.setText("Ready - click waveform to seek, drag to select a region.")
        self.fx_panel.set_current_path(self._path)
        QTimer.singleShot(250, lambda: self.fx_panel.auto_scan(self._path))

    # ---- transport ----

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            if not self._path.is_file():
                QMessageBox.warning(self, "Playback", "Audio file not found.")
                return
            try:
                self.aboutToPlay.emit()
            except Exception:
                pass
            self._player.play()

    def stop_playback(self):
        """External exclusive-stop (other decks starting)."""
        try:
            self._player.stop()
            self._playing = False
        except Exception:
            pass

    def _stop(self):
        self._player.stop()
        self._player.setPosition(0)
        self.canvas.set_position(0)
        self._time_lbl.setText(_fmt_time(0))
        self._slider.setValue(0)

    def _rewind(self):
        self._player.setPosition(0)
        self.canvas.set_position(0)
        self._time_lbl.setText(_fmt_time(0))
        self._slider.setValue(0)

    def _seek_seconds(self, seconds: float):
        ms = int(max(0.0, seconds) * 1000)
        self._player.setPosition(ms)
        self.canvas.set_position(seconds)
        self._time_lbl.setText(_fmt_time(ms))
        if not self._scrubbing:
            self._slider.setValue(ms)

    def _on_slider_moved(self, value: int):
        self._player.setPosition(value)
        self.canvas.set_position(value / 1000.0)
        self._time_lbl.setText(_fmt_time(value))

    def _on_slider_released(self):
        self._scrubbing = False
        self._player.setPosition(self._slider.value())

    def _on_position(self, pos: int):
        if self._scrubbing:
            return
        self._time_lbl.setText(_fmt_time(pos))
        self._slider.blockSignals(True)
        self._slider.setValue(pos)
        self._slider.blockSignals(False)
        self.canvas.set_position(pos / 1000.0)

    def _on_duration(self, dur: int):
        if dur > 0:
            self._duration_ms = dur
            self._slider.setRange(0, dur)
            self._dur_lbl.setText(f"/ {_fmt_time(dur)}")
            # sync canvas duration if peaks loaded with 0
            if self.canvas._duration <= 0:
                self.canvas._duration = dur / 1000.0

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._playing = playing
        try:
            self.fx_panel.set_live_active(playing)
        except Exception:
            pass
        # swap play/pause icon
        self._btn_play._icon.set_name("pause" if playing else "play")
        self._btn_play._icon.set_color(Color.BG)

    def _on_error(self, *args):
        err = self._player.errorString()
        self._status.setText(f"Playback error: {err}")

    def _on_lookahead(self, v: float):
        self.canvas.set_lookahead(v)

    def _preset_trim(self, seconds: float):
        """Place 15/30/60s glass selection at playhead and zoom for clean cuts."""
        try:
            self.canvas.set_selection_block(float(seconds))
            a, b = self.canvas.selection()
            if a is not None and b is not None:
                self._status.setText(
                    f"Trim block {seconds:g}s ({a:.2f}s → {b:.2f}s). "
                    "Adjust by dragging, then use Trim."
                )
                self._on_selection(a, b)
        except Exception as exc:
            self._status.setText(f"Preset trim failed: {exc}")

    def _apply_studio_eq(self, *args):
        """Live tone EQ — power + knobs push straight into the realtime chain."""
        self._push_live()
        lo, mid, hi = self.eq_strip.gains()
        if self.eq_strip.is_powered():
            self.eq_strip.set_status_hint(f"on  L{lo:+.0f} M{mid:+.0f} H{hi:+.0f} dB")
        else:
            self.eq_strip.set_status_hint("power off · dry signal")

    # ---- live FX ----------------------------------------------------------

    def _on_artifact_markers(self, hits):
        """Overlay hunt hits on the Studio waveform canvas."""
        try:
            self.canvas.set_markers(list(hits or []))
            n = len(hits or [])
            if n:
                self._status.setText(f"Artifact map · {n} hit(s) marked on waveform")
        except Exception:
            pass

    def _push_live(self, *_):
        """Compose panel + tone settings into the realtime engine (no restart)."""
        try:
            settings = self.fx_panel.settings()
        except Exception:
            return
        settings["artifacts"] = self.fx_panel.artifacts()
        settings["master_on"] = not self.fx_panel.is_bypassed()
        # Ensure no VST stage is activated (hosting removed)
        settings.pop("vst_path", None)
        settings.pop("vst_chain", None)
        settings.pop("vst_params", None)
        if settings.get("bat_effect"):
            self._status.setText(
                "Live: batch tool applies on Render / Save (not realtime)"
            )
        self._player.setSettings(settings)
        # Clear any legacy VST chain on the engine
        try:
            self._player.setVstChain([])
        except Exception:
            pass
        lo, mid, hi = self.eq_strip.gains()
        self._player.setTone(
            lo, mid, hi,
            power=self.eq_strip.is_powered(),
        )

    def _on_live_summary(self, text: str):
        # Always surface live chain state while Studio is open
        try:
            playing = (
                self._player.playbackState()
                == QMediaPlayer.PlaybackState.PlayingState
            )
        except Exception:
            playing = False
        prefix = "Live" if playing else "Ready"
        self._status.setText(f"{prefix} · {text} · realtime preview")


    def _open_master_export(self):
        """Open the streaming-ready master export dialog for the current file."""
        from .master_dialog import MasterExportDialog

        src = getattr(self, "_fx_source_clean", None) or getattr(self, "_eq_source_clean", None) or str(self._path)
        if not Path(src).is_file():
            self._status.setText("Master export: no source")
            return
        dlg = MasterExportDialog(src, parent=self)
        dlg.exec()

    def _download_studio_eq(self):
        """Save EQ'd WAV/FLAC/MP3 via file dialog."""
        from .eq_knobs import apply_eq_pedalboard
        from .convert_dialog import show_convert_results
        from nodaw.audio.convert import convert_one

        lo, mid, hi = self.eq_strip.gains()
        src = getattr(self, "_eq_source_clean", None) or str(self._path)
        if not Path(src).is_file():
            self._status.setText("Download: no source")
            return
        if abs(lo) + abs(mid) + abs(hi) < 0.08:
            QMessageBox.information(
                self, "Download EQ", "Set LOW/MID/HIGH first — bands are flat."
            )
            return
        default = (
            Path(__file__).resolve().parents[3]
            / "exports"
            / "eq_preview"
            / f"{Path(src).stem}_eq.wav"
        )
        default.parent.mkdir(parents=True, exist_ok=True)
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save EQ'd version", str(default), "WAV (*.wav);;FLAC (*.flac);;MP3 (*.mp3)"
        )
        if not dest:
            return
        dest_p = Path(dest)
        if dest_p.suffix.lower() not in {".wav", ".flac", ".mp3"}:
            dest_p = dest_p.with_suffix(".wav")
        wav_tmp = dest_p if dest_p.suffix.lower() == ".wav" else default.parent / f"{dest_p.stem}_tmp.wav"
        self._status.setText("Exporting EQ'd file…")
        QApplication.processEvents()
        res = apply_eq_pedalboard(src, wav_tmp, lo, mid, hi)
        if not res.get("ok"):
            self._status.setText(f"Export failed: {res.get('error')}")
            return
        if dest_p.suffix.lower() != ".wav":
            cres = convert_one(wav_tmp, dest_p)
            if not cres.get("ok"):
                self._status.setText(f"Export failed: {cres.get('error')}")
                return
        else:
            dest_p = Path(wav_tmp)
        self._eq_baked_path = dest_p
        self._status.setText(f"Saved EQ → {dest_p.name}")
        show_convert_results(
            self, [{"ok": True, "dest": str(dest_p), "fmt": dest_p.suffix.lstrip(".")}]
        )

    def _render_studio_fx(self):
        """Bake the full Studio FX chain (artifacts → bleedfix → EQ → VST)."""
        from nodaw.audio.studio_fx import render_fx_chain, run_bat_effect

        if self.fx_panel.is_bypassed():
            self._swap_fx_source(None, "FX bypassed — dry")
            return
        src = getattr(self, "_fx_source_clean", None) or getattr(self, "_eq_source_clean", None) or str(self._path)
        if not Path(src).is_file():
            self._status.setText("FX: source missing")
            return
        settings = self.fx_panel.settings()
        settings.pop("vst_path", None)
        settings.pop("vst_chain", None)
        settings.pop("vst_params", None)
        artifacts = self.fx_panel.artifacts()
        bat = settings.pop("bat_effect", None)
        settings.pop("artifacts", None)
        has_bleed = bool(settings.get("bleed", {}).get("on"))
        has_eq = any(b["on"] and abs(b["gain_db"]) > 0.05 for b in settings["eq_bands"])
        has_art = bool(
            settings.get("declick") or settings.get("dedc") or settings.get("deedge")
        )
        active = (
            (artifacts is not None and has_art)
            or has_bleed
            or has_eq
            or bool(settings["json_effect"])
            or bool(bat)
            or settings["wet_dry"] < 0.995
        )
        if not active:
            self._swap_fx_source(None, "FX on — flat chain")
            return
        out_dir = Path(__file__).resolve().parents[3] / "exports" / "fx_preview"
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{Path(src).stem}_fx.wav"
        self._status.setText("Rendering Studio FX…")
        QApplication.processEvents()
        res = render_fx_chain(src, dest, artifacts=artifacts, **settings)
        if not res.get("ok"):
            self._status.setText(f"FX failed: {res.get('error')}")
            return
        status = "FX · " + ", ".join(res["applied"])[:90]
        if bat:
            self._status.setText("Rendering batch tool effect…")
            QApplication.processEvents()
            bdest = dest.with_name(f"{dest.stem}_bat.wav")
            bres = run_bat_effect(str(dest), str(bdest), bat_path=bat.path)
            if not bres.get("ok"):
                self._status.setText(f"Bat failed: {bres.get('error')}")
                return
            dest = bdest
            status = f"FX · {bat.name[:40]} · {bres.get('filter', '')[:50]}"
        self._swap_fx_source(dest, status)

    def _swap_fx_source(self, baked: Path | None, status_text: str):
        """Swap player source to a baked preview (or back to dry), keeping position."""
        dry = getattr(self, "_fx_source_clean", None) or getattr(self, "_eq_source_clean", None) or str(self._path)
        was = self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        pos = int(self._player.position() or 0)
        self._player.stop()
        if baked is None or not Path(baked).is_file():
            self._player.setSource(str(Path(dry).resolve()))
            self._path = Path(dry)
        else:
            self._player.setSource(str(Path(baked).resolve()))
            self._path = Path(baked)
        self._player.setPosition(pos)
        if was:
            try:
                self.aboutToPlay.emit()
            except Exception:
                pass
            self._player.play()
        self._status.setText(status_text)

    def _save_studio_fx(self):
        """Export the full FX chain to WAV / FLAC / MP3 via file dialog."""
        from nodaw.audio.studio_fx import render_fx_chain, run_bat_effect
        from .convert_dialog import show_convert_results
        from nodaw.audio.convert import convert_one

        src = getattr(self, "_fx_source_clean", None) or getattr(self, "_eq_source_clean", None) or str(self._path)
        if not Path(src).is_file():
            self._status.setText("Export: no source")
            return
        settings = self.fx_panel.settings()
        settings.pop("vst_path", None)
        settings.pop("vst_chain", None)
        settings.pop("vst_params", None)
        artifacts = self.fx_panel.artifacts()
        bat = settings.pop("bat_effect", None)
        settings.pop("artifacts", None)
        has_bleed = bool(settings.get("bleed", {}).get("on"))
        has_eq = any(b["on"] and abs(b["gain_db"]) > 0.05 for b in settings["eq_bands"])
        has_art = bool(
            settings.get("declick") or settings.get("dedc") or settings.get("deedge")
        )
        active = (
            (artifacts is not None and has_art)
            or has_bleed
            or has_eq
            or bool(settings["json_effect"])
            or bool(bat)
            or settings["wet_dry"] < 0.995
        )
        if not active:
            QMessageBox.information(
                self, "Export FX", "Turn something on — the chain is flat (or run the artifact scan)."
            )
            return
        default = (
            Path(__file__).resolve().parents[3]
            / "exports"
            / "fx_preview"
            / f"{Path(src).stem}_fx.wav"
        )
        default.parent.mkdir(parents=True, exist_ok=True)
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save processed version", str(default), "WAV (*.wav);;FLAC (*.flac);;MP3 (*.mp3)"
        )
        if not dest:
            return
        dest_p = Path(dest)
        if dest_p.suffix.lower() not in {".wav", ".flac", ".mp3"}:
            dest_p = dest_p.with_suffix(".wav")
        wav_tmp = dest_p if dest_p.suffix.lower() == ".wav" else default.parent / f"{dest_p.stem}_tmp.wav"
        self._status.setText("Exporting Studio FX…")
        QApplication.processEvents()
        res = render_fx_chain(src, wav_tmp, artifacts=artifacts, **settings)
        if not res.get("ok"):
            self._status.setText(f"Export failed: {res.get('error')}")
            return
        if bat:
            self._status.setText("Applying batch tool effect…")
            QApplication.processEvents()
            btmp = wav_tmp.with_name(f"{wav_tmp.stem}_bat.wav")
            bres = run_bat_effect(str(wav_tmp), str(btmp), bat_path=bat.path)
            if not bres.get("ok"):
                self._status.setText(f"Bat failed: {bres.get('error')}")
                return
            wav_tmp = btmp
        if dest_p.suffix.lower() != ".wav":
            cres = convert_one(wav_tmp, dest_p)
            if not cres.get("ok"):
                self._status.setText(f"Export failed: {cres.get('error')}")
                return
        else:
            dest_p = Path(wav_tmp)
        self._status.setText(f"Saved FX → {dest_p.name}")
        show_convert_results(
            self, [{"ok": True, "dest": str(dest_p), "fmt": dest_p.suffix.lstrip(".")}]
        )

    def _on_selection(self, a, b):
        if a is None or b is None:
            self._sel_lbl.setText("No selection")
        else:
            self._sel_lbl.setText(
                f"Selection  {_fmt_time(a * 1000)}  →  {_fmt_time(b * 1000)}  ({b - a:.2f}s)"
            )

    # ---- tools ----

    def _open_folder(self):
        if self._path.parent.is_dir():
            os.startfile(str(self._path.parent))

    def _reveal(self):
        if self._path.is_file():
            subprocess.Popen(["explorer", "/select,", str(self._path)])
        else:
            self._open_folder()

    def _open_artifact_hunter(self):
        from .fx_tools import artifact_hunter_modal

        artifact_hunter_modal(self._path, parent=self)

    def _open_bleedfix(self):
        from .fx_tools import bleedfix_modal

        bleedfix_modal(self._path, parent=self)

    def _trim_selection(self):
        a, b = self.canvas.selection()
        if a is None or b is None:
            QMessageBox.information(
                self,
                "Trim",
                "Drag on the waveform to select a region first.",
            )
            return
        # Prefer dry source for trim when EQ preview is loaded
        trim_src = Path(getattr(self, "_eq_source_clean", None) or self._path)
        if not trim_src.is_file():
            QMessageBox.warning(self, "Trim", "Source file missing.")
            return
        # app/nodaw/ui/player.py → project root is parents[3]
        out_dir = Path(__file__).resolve().parents[3] / "exports" / "trims"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{trim_src.stem}_trim_{a:.2f}-{b:.2f}{trim_src.suffix}"
        # ffmpeg accurate trim
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{a:.3f}",
            "-to",
            f"{b:.3f}",
            "-i",
            str(trim_src),
            "-c",
            "copy",
            str(out),
        ]
        # copy may fail on some formats - fall back to re-encode
        self._status.setText("Trimming…")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not out.is_file():
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{a:.3f}",
                    "-to",
                    f"{b:.3f}",
                    "-i",
                    str(self._path),
                    "-c:a",
                    "pcm_s16le" if out.suffix.lower() == ".wav" else "libmp3lame",
                    str(out.with_suffix(".wav" if out.suffix.lower() != ".mp3" else ".mp3")),
                ]
                out = Path(cmd[-1])
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if r.returncode == 0 and out.is_file():
                self._status.setText(f"Trimmed → {out.name}")
                QMessageBox.information(
                    self,
                    "Trim complete",
                    f"Saved:\n{out}\n\nOpen folder?",
                )
                os.startfile(str(out.parent))
            else:
                err = (r.stderr or "")[-400:]
                QMessageBox.critical(self, "Trim failed", err or "ffmpeg error")
                self._status.setText("Trim failed")
        except Exception as exc:
            QMessageBox.critical(self, "Trim failed", str(exc))
            self._status.setText(str(exc))

    def _convert(self):
        """Multi-format convert dialog → sequential FFmpeg jobs → result panel with Open folder."""
        if not self._path.is_file():
            QMessageBox.warning(self, "Convert", "Source file missing.")
            return

        from nodaw.audio.convert import CONVERT_FORMATS, convert_one, default_dest
        from nodaw.ui.convert_dialog import show_convert_results

        dlg = QDialog(self)
        dlg.setWindowTitle("Convert formats")
        dlg.setMinimumWidth(420)
        lay = QVBoxLayout(dlg)
        hint = QLabel(
            "Check one or more formats. Jobs run one after another in the background.\n"
            "Exports go to the project exports folder (unique names)."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)
        checks: dict[str, QCheckBox] = {}
        for fmt, label in CONVERT_FORMATS:
            cb = QCheckBox(label)
            cb.setChecked(fmt in {"wav", "mp3"})
            checks[fmt] = cb
            lay.addWidget(cb)
        # Optional custom folder
        folder_row = QHBoxLayout()
        folder_lbl = QLabel("Output folder")
        folder_val = QLabel("")
        folder_val.setWordWrap(True)
        out_dir = Path(__file__).resolve().parents[3] / "exports" / "converts"
        out_dir.mkdir(parents=True, exist_ok=True)
        folder_val.setText(str(out_dir))
        folder_val.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        pick = QPushButton("Change…")

        def _pick():
            nonlocal out_dir
            d = QFileDialog.getExistingDirectory(dlg, "Convert output folder", str(out_dir))
            if d:
                out_dir = Path(d)
                folder_val.setText(str(out_dir))

        pick.clicked.connect(_pick)
        folder_row.addWidget(folder_lbl)
        lay.addLayout(folder_row)
        lay.addWidget(folder_val)
        lay.addWidget(pick)
        buttons = QHBoxLayout()
        ok = QPushButton("Convert")
        cancel = QPushButton("Cancel")
        buttons.addStretch()
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        lay.addLayout(buttons)
        cancel.clicked.connect(dlg.reject)
        ok.clicked.connect(dlg.accept)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        fmts = [f for f, cb in checks.items() if cb.isChecked()]
        if not fmts:
            QMessageBox.information(self, "Convert", "Select at least one format.")
            return

        results: list[dict] = []
        total = len(fmts)
        for i, fmt in enumerate(fmts, 1):
            dest = default_dest(self._path, fmt, out_dir)
            self._status.setText(f"Converting {fmt} ({i}/{total})…")
            QApplication.processEvents()
            res = convert_one(self._path, dest)
            results.append(res)

        ok_n = sum(1 for r in results if r.get("ok"))
        self._status.setText(f"Convert done: {ok_n}/{total} ok")
        show_convert_results(self, results)

    def closeEvent(self, event):
        try:
            self._player.stop()
        except Exception:
            pass
        try:
            self.fx_panel.close_threads()
        except Exception:
            pass
        super().closeEvent(event)


def resolve_audio_path(report: dict[str, Any] | None) -> Path | None:
    """Best-effort extract of on-disk audio path from a report dict."""
    if not report:
        return None
    track = report.get("track") if isinstance(report.get("track"), dict) else {}
    audio = (track or {}).get("audio") or {}
    for key in ("path", "file_path", "source_path", "source"):
        raw = audio.get(key) if isinstance(audio, dict) else None
        if raw and Path(str(raw)).is_file():
            return Path(str(raw))
    # top-level
    for key in ("path", "file_path", "source"):
        raw = report.get(key)
        if raw and Path(str(raw)).is_file():
            return Path(str(raw))
    return None
