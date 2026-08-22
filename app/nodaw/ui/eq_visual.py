"""
Advanced multi-band visual equalizer.

Paints a pro-audio frequency-response curve (log Hz × dB) driven by
shelf / peaking bands. Used as the glass backdrop behind Tone Sculpt
knobs and as the parametric EQ stage graph in Studio FX.
"""

from __future__ import annotations

import math
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from .theme import Color, Type


def _band_color(role: str) -> QColor:
    c0, c1, c2 = Color.wave_stops()
    if role == "low":
        return QColor(Color.GOLD if hasattr(Color, "GOLD") else c2)
    if role == "high":
        return QColor(c0)
    if role == "warn":
        return QColor(Color.WARNING)
    return QColor(c1)


def _log_x(f: float, f_min: float = 20.0, f_max: float = 20000.0) -> float:
    f = max(f_min, min(f_max, float(f)))
    return (math.log10(f) - math.log10(f_min)) / (math.log10(f_max) - math.log10(f_min))


def _x_to_freq(t: float, f_min: float = 20.0, f_max: float = 20000.0) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 10.0 ** (math.log10(f_min) + t * (math.log10(f_max) - math.log10(f_min)))


def response_db(
    freqs: list[float] | Any,
    bands: list[dict[str, Any]],
) -> list[float]:
    """
    Approximate composite magnitude (dB) for UI curves.

    Supports type: lowshelf | highshelf | peaking | notch | highpass | lowpass.
    Dynamic bands draw at static (target) gain — live GR is heard, not graphed.
    """
    out: list[float] = []
    for f in freqs:
        g = 0.0
        for b in bands:
            if not b.get("on", True):
                continue
            gain = float(b.get("gain_db", 0.0))
            f0 = max(20.0, min(20000.0, float(b.get("freq", 1000.0))))
            q = max(0.15, min(24.0, float(b.get("q", 0.9))))
            typ = str(b.get("type") or "peaking").lower()
            x = math.log2(max(f, 1.0) / f0)
            if typ in ("lowshelf", "ls", "low_shelf"):
                if abs(gain) < 0.02:
                    continue
                k = max(0.35, 1.4 / q)
                w = 1.0 / (1.0 + math.exp(x / k * 3.2))
                g += gain * w
            elif typ in ("highshelf", "hs", "high_shelf"):
                if abs(gain) < 0.02:
                    continue
                k = max(0.35, 1.4 / q)
                w = 1.0 / (1.0 + math.exp(-x / k * 3.2))
                g += gain * w
            elif typ in ("notch", "bandstop", "bs"):
                depth = abs(gain) if abs(gain) >= 0.5 else 18.0
                bw = max(0.05, 0.45 / q)
                w = 1.0 / (1.0 + (x / bw) ** 2)
                g -= depth * w
            elif typ in ("highpass", "hpf", "hp"):
                # Butterworth-ish slope ~ order via Q
                k = max(0.25, 0.9 / q)
                # 0 below, full above
                w = 1.0 / (1.0 + math.exp(-(x) / k * 4.0))
                # map to ~-48 dB floor
                g += (1.0 - w) * -48.0
            elif typ in ("lowpass", "lpf", "lp"):
                k = max(0.25, 0.9 / q)
                w = 1.0 / (1.0 + math.exp((x) / k * 4.0))
                g += (1.0 - w) * -48.0
            else:
                if abs(gain) < 0.02:
                    continue
                # Peaking — log-frequency Lorentzian shaped by Q
                bw = max(0.08, 0.7 / q)
                w = 1.0 / (1.0 + (x / bw) ** 2)
                g += gain * w
        out.append(g)
    return out


class MultiBandEqVisualizer(QWidget):
    """
    Advanced multi-band EQ graph.

    - Log frequency axis 20 Hz – 20 kHz
    - ±db_range grid with zero line
    - Per-band ghost curves + composite filled response
    - Band handles (freq/gain) with optional drag
    - Hover readout
    """

    bandDragged = Signal(int, float, float)  # index, freq_hz, gain_db
    bandQChanged = Signal(int, float)  # index, q
    bandReleased = Signal(int)
    bandSelected = Signal(int)

    def __init__(
        self,
        parent=None,
        *,
        db_range: float = 12.0,
        interactive: bool = True,
        show_spectrum: bool = True,
        title: str = "MULTI-BAND EQ",
    ):
        super().__init__(parent)
        self._db_range = float(db_range)
        self._interactive = bool(interactive)
        self._show_spectrum = bool(show_spectrum)
        self._title = title
        self._bands: list[dict[str, Any]] = []
        self._powered = True
        self._hover_f: float | None = None
        self._hover_db: float | None = None
        self._drag_idx: int | None = None
        self._selected: int = 0
        self._spectrum: list[float] | None = None  # 0..1 bars, optional
        self._n_curve = 180
        self.setMinimumHeight(112)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(
            "Drag handles = freq + gain  ·  mouse-wheel on handle = Q  ·  "
            "double-click = zero gain  ·  click empty = select nearest"
        )

    # ------------------------------------------------------------------ API

    def set_bands(self, bands: list[dict[str, Any]]) -> None:
        """
        bands: list of {freq, gain_db, q, type?, on?, color_role?, label?}
        """
        self._bands = [dict(b) for b in (bands or [])]
        self.update()

    def set_powered(self, on: bool) -> None:
        self._powered = bool(on)
        self.update()

    def set_db_range(self, db: float) -> None:
        self._db_range = max(6.0, float(db))
        self.update()

    def set_spectrum(self, levels: list[float] | None) -> None:
        """Optional analyzer bars 0..1 across log frequency (len free)."""
        self._spectrum = list(levels) if levels is not None else None
        self.update()

    def bands(self) -> list[dict[str, Any]]:
        return [dict(b) for b in self._bands]

    def set_selected(self, idx: int) -> None:
        self._selected = int(idx)
        self.update()

    def selected(self) -> int:
        return int(self._selected)

    # -------------------------------------------------------------- geometry

    def _plot_rect(self) -> QRectF:
        # Extra bottom margin so straddling knobs don't cover the zero line labels
        m_l, m_r, m_t, m_b = 36.0, 12.0, 18.0, 28.0
        return QRectF(m_l, m_t, max(10.0, self.width() - m_l - m_r), max(10.0, self.height() - m_t - m_b))

    def _freq_to_x(self, f: float, rect: QRectF) -> float:
        return rect.left() + _log_x(f) * rect.width()

    def _db_to_y(self, db: float, rect: QRectF) -> float:
        # +db at top, −db at bottom
        t = (float(db) + self._db_range) / (2.0 * self._db_range)
        t = max(0.0, min(1.0, t))
        return rect.bottom() - t * rect.height()

    def _xy_to_freq_db(self, x: float, y: float, rect: QRectF) -> tuple[float, float]:
        t = (x - rect.left()) / max(1e-6, rect.width())
        f = _x_to_freq(t)
        u = (rect.bottom() - y) / max(1e-6, rect.height())
        db = u * 2.0 * self._db_range - self._db_range
        db = max(-self._db_range, min(self._db_range, db))
        return f, db

    def _handle_pos(self, i: int, rect: QRectF) -> QPointF | None:
        if i < 0 or i >= len(self._bands):
            return None
        b = self._bands[i]
        if not b.get("on", True):
            return None
        f = float(b.get("freq", 1000.0))
        g = float(b.get("gain_db", 0.0))
        return QPointF(self._freq_to_x(f, rect), self._db_to_y(g, rect))

    def _hit_handle(self, pos, rect: QRectF) -> int | None:
        best = None
        best_d = 14.0 ** 2
        for i in range(len(self._bands)):
            p = self._handle_pos(i, rect)
            if p is None:
                continue
            d = (p.x() - pos.x()) ** 2 + (p.y() - pos.y()) ** 2
            if d < best_d:
                best_d = d
                best = i
        return best

    # ----------------------------------------------------------------- paint

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        if w < 40 or h < 40:
            p.end()
            return

        rect = self._plot_rect()

        # Glass panel
        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0.0, QColor(Color.with_alpha(Color.ELEVATED, 0.55)))
        bg.setColorAt(1.0, QColor(Color.with_alpha(Color.BG, 0.72)))
        p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.65)), 1.0))
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), 10, 10)

        # Title
        p.setPen(QColor(Color.MUTED))
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
        p.setFont(font)
        p.drawText(QRectF(10, 2, w - 20, 14), Qt.AlignLeft | Qt.AlignVCenter, self._title)

        if not self._powered:
            p.setPen(QColor(Color.with_alpha(Color.MUTED, 0.7)))
            font.setPointSize(9)
            font.setBold(False)
            p.setFont(font)
            p.drawText(rect, Qt.AlignCenter, "EQ bypassed · dry")
            p.end()
            return

        # Optional soft spectrum wash (decorative analyzer floor)
        if self._show_spectrum and self._spectrum:
            n = len(self._spectrum)
            bar_w = rect.width() / max(1, n)
            for i, lv in enumerate(self._spectrum):
                lv = max(0.0, min(1.0, float(lv)))
                if lv < 0.02:
                    continue
                bh = lv * rect.height() * 0.55
                x = rect.left() + i * bar_w
                c = QColor(Color.with_alpha(Color.ACCENT, 0.06 + 0.12 * lv))
                p.fillRect(QRectF(x, rect.bottom() - bh, max(1.0, bar_w - 0.5), bh), c)
        elif self._show_spectrum:
            # Idle ambient bars — subtle log-spaced noise floor aesthetic
            n = 48
            bar_w = rect.width() / n
            for i in range(n):
                # gentle high-end rolloff shape
                t = i / max(1, n - 1)
                lv = 0.08 + 0.06 * math.sin(t * 9.0) * math.sin(t * 3.1)
                lv *= 0.55 + 0.45 * (1.0 - t * 0.5)
                bh = lv * rect.height() * 0.35
                x = rect.left() + i * bar_w
                p.fillRect(
                    QRectF(x, rect.bottom() - bh, max(1.0, bar_w - 0.6), bh),
                    QColor(Color.with_alpha(Color.ACCENT, 0.05)),
                )

        # Grid
        p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.45)), 1.0))
        # Horizontal dB lines
        for db in (-self._db_range, -self._db_range / 2, 0.0, self._db_range / 2, self._db_range):
            y = self._db_to_y(db, rect)
            if abs(db) < 0.01:
                p.setPen(QPen(QColor(Color.with_alpha(Color.ACCENT, 0.35)), 1.2))
            else:
                p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.4)), 1.0))
            p.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            p.setPen(QColor(Color.MUTED))
            font.setPointSize(7)
            font.setBold(False)
            p.setFont(font)
            label = f"{db:+.0f}" if abs(db) > 0.01 else "0"
            p.drawText(QRectF(2, y - 7, 32, 14), Qt.AlignRight | Qt.AlignVCenter, label)

        # Vertical frequency lines
        freq_marks = [20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]
        for f in freq_marks:
            x = self._freq_to_x(f, rect)
            p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.35)), 1.0))
            p.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        # Labels
        p.setPen(QColor(Color.MUTED))
        font.setPointSize(7)
        p.setFont(font)
        for f, lab in ((20, "20"), (100, "100"), (1000, "1k"), (10000, "10k"), (20000, "20k")):
            x = self._freq_to_x(f, rect)
            p.drawText(QRectF(x - 16, rect.bottom() + 2, 32, 14), Qt.AlignHCenter, lab)

        # Sample composite curve
        freqs = [
            _x_to_freq(i / max(1, self._n_curve - 1)) for i in range(self._n_curve)
        ]
        # Per-band ghost curves
        for bi, b in enumerate(self._bands):
            if not b.get("on", True) or abs(float(b.get("gain_db", 0))) < 0.03:
                continue
            solo = [b]
            ys = response_db(freqs, solo)
            role = str(b.get("color_role") or ("low" if bi == 0 else "high" if bi == len(self._bands) - 1 else "mid"))
            col = _band_color(role)
            path = QPainterPath()
            for i, (f, db) in enumerate(zip(freqs, ys)):
                x = self._freq_to_x(f, rect)
                y = self._db_to_y(db, rect)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            c = QColor(col)
            c.setAlpha(90)
            p.setPen(QPen(c, 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(path)

        # Composite curve + fill
        ys = response_db(freqs, self._bands)
        path = QPainterPath()
        fill = QPainterPath()
        zero_y = self._db_to_y(0.0, rect)
        for i, (f, db) in enumerate(zip(freqs, ys)):
            x = self._freq_to_x(f, rect)
            y = self._db_to_y(db, rect)
            if i == 0:
                path.moveTo(x, y)
                fill.moveTo(x, zero_y)
                fill.lineTo(x, y)
            else:
                path.lineTo(x, y)
                fill.lineTo(x, y)
        fill.lineTo(self._freq_to_x(freqs[-1], rect), zero_y)
        fill.closeSubpath()

        # Fill under curve
        grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
        grad.setColorAt(0.0, QColor(Color.with_alpha(Color.ACCENT, 0.28)))
        grad.setColorAt(0.5, QColor(Color.with_alpha(Color.ACCENT_SOFT, 0.12)))
        grad.setColorAt(1.0, QColor(Color.with_alpha(Color.ACCENT, 0.03)))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(grad)
        p.drawPath(fill)

        # Glow stroke
        glow = QColor(Color.ACCENT)
        glow.setAlpha(55)
        p.setPen(QPen(glow, 5.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path)
        p.setPen(
            QPen(
                QColor(Color.ACCENT_SOFT),
                2.0,
                Qt.PenStyle.SolidLine,
                Qt.PenCapStyle.RoundCap,
                Qt.PenJoinStyle.RoundJoin,
            )
        )
        p.drawPath(path)

        # Handles
        for i, b in enumerate(self._bands):
            if not b.get("on", True):
                # dim off-band marker on zero line
                f = float(b.get("freq", 1000.0))
                px = self._freq_to_x(f, rect)
                zy = self._db_to_y(0.0, rect)
                p.setPen(QPen(QColor(Color.with_alpha(Color.MUTED, 0.35)), 1.0, Qt.PenStyle.DotLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(px, zy), 4, 4)
                continue
            pos = self._handle_pos(i, rect)
            if pos is None:
                continue
            role = str(b.get("color_role") or ("low" if i == 0 else "high" if i == len(self._bands) - 1 else "mid"))
            col = _band_color(role)
            selected = i == self._selected
            # vertical guide
            p.setPen(
                QPen(
                    QColor(Color.with_alpha(col.name(), 0.45 if selected else 0.22)),
                    1.4 if selected else 1.0,
                    Qt.PenStyle.DashLine,
                )
            )
            p.drawLine(QPointF(pos.x(), rect.top()), QPointF(pos.x(), rect.bottom()))
            # bloom
            br = 18 if selected else 14
            bloom = QRadialGradient(pos, br)
            c0 = QColor(col)
            c0.setAlpha(150 if selected else 110)
            c1 = QColor(col)
            c1.setAlpha(0)
            bloom.setColorAt(0.0, c0)
            bloom.setColorAt(1.0, c1)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bloom)
            p.drawEllipse(pos, br, br)
            # node
            p.setBrush(QColor(col))
            ring = QColor(Color.WHITE) if selected else QColor(Color.with_alpha(Color.WHITE, 0.85))
            p.setPen(QPen(ring, 2.0 if selected else 1.4))
            p.drawEllipse(pos, 7.0 if selected else 5.5, 7.0 if selected else 5.5)
            # dynamic ring
            if b.get("dynamic"):
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor(Color.WARNING), 1.6, Qt.PenStyle.DotLine))
                p.drawEllipse(pos, 11, 11)
            # label + type
            lab = str(b.get("label") or f"B{i + 1}")
            typ = str(b.get("type") or "peak")[:2].upper()
            p.setPen(QColor(col))
            font.setPointSize(7)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRectF(pos.x() - 22, pos.y() - 22, 44, 12), Qt.AlignHCenter, f"{lab}·{typ}")

        # Hover readout
        if self._hover_f is not None and self._hover_db is not None:
            hx = self._freq_to_x(self._hover_f, rect)
            hy = self._db_to_y(self._hover_db, rect)
            p.setPen(QPen(QColor(Color.with_alpha(Color.TEXT, 0.5)), 1.0, Qt.PenStyle.DotLine))
            p.drawLine(QPointF(hx, rect.top()), QPointF(hx, rect.bottom()))
            p.drawLine(QPointF(rect.left(), hy), QPointF(rect.right(), hy))
            f = self._hover_f
            if f >= 1000:
                fs = f"{f / 1000:.2f} kHz" if f < 10000 else f"{f / 1000:.1f} kHz"
            else:
                fs = f"{f:.0f} Hz"
            txt = f"{fs}  ·  {self._hover_db:+.1f} dB"
            p.setPen(QColor(Color.TEXT))
            font.setPointSize(8)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRectF(rect.left() + 6, rect.top() + 2, rect.width() - 12, 14), Qt.AlignRight, txt)

        p.end()

    # ---------------------------------------------------------------- mouse

    def mousePressEvent(self, event):
        if not self._interactive or not self._powered:
            return super().mousePressEvent(event)
        rect = self._plot_rect()
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._hit_handle(event.position(), rect)
            if idx is not None:
                self._drag_idx = idx
                self._selected = idx
                self.bandSelected.emit(idx)
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
                event.accept()
                return
            # Click empty plot → select nearest band by frequency
            if rect.contains(event.position()):
                f, _ = self._xy_to_freq_db(event.position().x(), event.position().y(), rect)
                best_i, best_d = 0, 1e18
                for i, b in enumerate(self._bands):
                    d = abs(math.log2(max(20.0, float(b.get("freq", 1000.0))) / max(20.0, f)))
                    if d < best_d:
                        best_d, best_i = d, i
                self._selected = best_i
                self.bandSelected.emit(best_i)
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        if not self._interactive or not self._powered:
            return super().wheelEvent(event)
        rect = self._plot_rect()
        idx = self._hit_handle(event.position(), rect)
        if idx is None:
            idx = self._selected if 0 <= self._selected < len(self._bands) else None
        if idx is None or not (0 <= idx < len(self._bands)):
            return super().wheelEvent(event)
        step = 0.08 if event.angleDelta().y() > 0 else -0.08
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            step *= 3.0
        q = float(self._bands[idx].get("q", 0.9))
        q = max(0.2, min(12.0, q + step))
        self._bands[idx]["q"] = q
        self._selected = idx
        self.bandQChanged.emit(idx, q)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event):
        rect = self._plot_rect()
        pos = event.position()
        if self._drag_idx is not None and self._interactive:
            f, db = self._xy_to_freq_db(pos.x(), pos.y(), rect)
            i = self._drag_idx
            if 0 <= i < len(self._bands):
                self._bands[i]["freq"] = f
                self._bands[i]["gain_db"] = db
                self.bandDragged.emit(i, f, db)
                self.update()
            event.accept()
            return
        if rect.contains(pos):
            f, _ = self._xy_to_freq_db(pos.x(), pos.y(), rect)
            # composite db at this freq
            dbs = response_db([f], self._bands)
            self._hover_f = f
            self._hover_db = dbs[0] if dbs else 0.0
            hit = self._hit_handle(pos, rect)
            self.setCursor(
                Qt.CursorShape.OpenHandCursor
                if hit is not None and self._interactive
                else Qt.CursorShape.CrossCursor
            )
            self.update()
        else:
            if self._hover_f is not None:
                self._hover_f = None
                self._hover_db = None
                self.setCursor(Qt.CursorShape.ArrowCursor)
                self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_idx is not None:
            i = self._drag_idx
            self._drag_idx = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.bandReleased.emit(i)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if not self._interactive or not self._powered:
            return super().mouseDoubleClickEvent(event)
        rect = self._plot_rect()
        idx = self._hit_handle(event.position(), rect)
        if idx is not None:
            self._bands[idx]["gain_db"] = 0.0
            f = float(self._bands[idx].get("freq", 1000.0))
            self.bandDragged.emit(idx, f, 0.0)
            self.bandReleased.emit(idx)
            self.update()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def leaveEvent(self, event):
        self._hover_f = None
        self._hover_db = None
        self.update()
        super().leaveEvent(event)


class EqOverlayStage(QWidget):
    """
    Hosts MultiBandEqVisualizer with knobs straddling the bottom edge:
    ~half the knob strip sits on the graph, ~half hangs below (room to work).
    """

    def __init__(
        self,
        parent=None,
        *,
        db_range: float = 12.0,
        title: str = "MULTI-BAND EQ",
        curve_height: int = 150,
        overlap: int = 56,
        knobs_height: int = 118,
    ):
        super().__init__(parent)
        self._curve_h = int(curve_height)
        self._overlap = int(overlap)
        self._knobs_h = int(knobs_height)
        self.curve = MultiBandEqVisualizer(
            self, db_range=db_range, interactive=True, show_spectrum=True, title=title
        )
        self.overlay = QWidget(self)
        self.overlay.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.overlay.setStyleSheet("background: transparent;")
        total = self._curve_h + self._knobs_h - self._overlap
        self.setMinimumHeight(total)
        self.setFixedHeight(total)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()
        self.curve.setGeometry(0, 0, w, self._curve_h)
        # Knobs start overlap pixels above the bottom of the curve
        y0 = self._curve_h - self._overlap
        self.overlay.setGeometry(0, y0, w, self._knobs_h)
        self.curve.lower()
        self.overlay.raise_()
