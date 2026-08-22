"""
CoProducer Design System -- Reusable UI Components.

Every widget uses:
    - theme tokens from `theme.py` (no hardcoded values)
    - SVG icons from `icons.py` (no unicode glyphs, no emoji)
    - 8-point spacing grid
    - Inter font family
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QConicalGradient,
    QDragEnterEvent,
    QDropEvent,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


def run_ffmpeg_in_terminal(command: str) -> None:
    """
    Open a terminal, paste ONLY the listed FFmpeg command, then press Enter
    so it runs immediately (no banners / wrappers — just that one line).
    """
    cmd = (command or "").strip()
    if not cmd:
        QMessageBox.information(None, "Repair", "No FFmpeg command on this recommendation.")
        return
    # Must be a full ffmpeg invocation (not a bare filter fragment)
    if not cmd.lower().startswith("ffmpeg"):
        QMessageBox.warning(
            None,
            "Repair",
            "This recommendation is missing a full FFmpeg command.\n"
            f"Got: {cmd[:200]}",
        )
        return

    # Clipboard holds ONLY the listed command
    try:
        app = QApplication.instance()
        if app is not None:
            app.clipboard().setText(cmd)
    except Exception:
        pass

    try:
        if sys.platform.startswith("win"):
            # Fresh cmd → Ctrl+V (paste exact line) → Enter (run)
            # Brief sleeps so the new console has focus before keys fire
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "Start-Process -FilePath cmd.exe -WindowStyle Normal; "
                "Start-Sleep -Milliseconds 750; "
                "[System.Windows.Forms.SendKeys]::SendWait('^v'); "
                "Start-Sleep -Milliseconds 120; "
                "[System.Windows.Forms.SendKeys]::SendWait('{ENTER}')"
            )
            subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-ExecutionPolicy", "Bypass",
                    "-Command", ps,
                ],
                creationflags=0x08000000,  # CREATE_NO_WINDOW for the helper
                env=os.environ.copy(),
            )
        elif sys.platform == "darwin":
            # Open Terminal → ⌘V paste → Return to run
            script = (
                'tell application "Terminal"\n'
                "  activate\n"
                "  do script \"\"\n"
                "end tell\n"
                "delay 0.5\n"
                'tell application "System Events"\n'
                '  keystroke "v" using command down\n'
                "  delay 0.1\n"
                "  key code 36\n"  # Return
                "end tell\n"
            )
            subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE).communicate(
                cmd.encode("utf-8")
            )
            subprocess.Popen(["osascript", "-e", script])
        else:
            # Linux: clipboard + open a shell; try xdotool to paste+enter when available
            for clip in (
                ["xclip", "-selection", "clipboard"],
                ["xsel", "--clipboard", "--input"],
            ):
                try:
                    p = subprocess.Popen(clip, stdin=subprocess.PIPE)
                    p.communicate(cmd.encode("utf-8"))
                    if p.returncode == 0:
                        break
                except FileNotFoundError:
                    continue
            launched = False
            for term in (
                ["x-terminal-emulator"],
                ["gnome-terminal"],
                ["konsole"],
                ["xterm"],
            ):
                try:
                    subprocess.Popen(term)
                    launched = True
                    break
                except FileNotFoundError:
                    continue
            if launched:
                # Best-effort: paste + Enter after the terminal opens
                try:
                    subprocess.Popen(
                        [
                            "bash",
                            "-c",
                            "sleep 0.8; "
                            "xdotool key --clearmodifiers ctrl+shift+v 2>/dev/null "
                            "|| xdotool key --clearmodifiers ctrl+v; "
                            "sleep 0.1; xdotool key Return",
                        ]
                    )
                except Exception:
                    pass
    except Exception as exc:
        QMessageBox.critical(None, "Repair", f"Could not open terminal:\n{exc}")

from .icons import IconWidget
from .theme import (
    Color,
    Duration,
    Easing,
    Elevation,
    Layout,
    Radius,
    Space,
    Type,
    current_skin_id,
    pick_ui_font,
    score_color,
    score_rating,
)

# -- Helpers ----------------------------------------------------=


class SweepButton(QPushButton):
    """
    Primary action control — flat, theme-shaped, no gradients.

    Colors always follow the active skin's Color.ACCENT (not a fixed blue).
    Label uses a small professional UI font on every skin.

    Styles (Layout.BUTTON_STYLE):
      solid  flat accent fill + hairline
      outline elevated fill + accent stroke
      hud    sharp radar plate + corner ticks
      dual   hard dual-border neon
      soft   pill field with quiet lift
      liquid telemetry plate + dash edge
      cyber  HUD panel + corner brackets + LED rail
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self._hover = 0.0
        self._pressed = False
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(34)
        self.setMaximumHeight(38)
        self.setFlat(True)
        self.setStyleSheet("border: none; background: transparent; color: transparent;")
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(32)

    def _style(self) -> str:
        return getattr(Layout, "BUTTON_STYLE", "solid") or "solid"

    def _tick(self):
        target = 1.0 if (self.isEnabled() and self.underMouse()) else 0.0
        self._hover += (target - self._hover) * 0.22
        if abs(self._hover - target) < 0.01:
            self._hover = target
        if self._hover > 0.001 or target > 0:
            self.update()

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        self._pressed = False
        super().leaveEvent(event)
        self.update()

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._pressed = False
        self.update()
        super().mouseReleaseEvent(event)

    @staticmethod
    def _ha(hex_color: str, a: float) -> QColor:
        c = QColor(hex_color)
        c.setAlphaF(max(0.0, min(1.0, float(a))))
        return c

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        full = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        style = self._style()
        radius = float(max(0, Radius.BUTTON))
        if style == "hard" or style == "dual":
            radius = 0.0
        elif style == "hud" or style == "cyber":
            radius = min(radius, 3.0)
        elif style == "soft":
            radius = max(radius, 16.0)
        h = self._hover
        press = 1.0 if self._pressed else 0.0
        r = full.adjusted(0, press * 0.5, 0, press * 0.5)
        ha = self._ha

        if not self.isEnabled():
            p.setPen(QPen(ha(Color.LINE, 0.75), 1.0))
            p.setBrush(ha(Color.ELEVATED, 0.72))
            p.drawRoundedRect(r, radius, radius)
            p.setPen(ha(Color.MUTED, 0.7))
            self._draw_label(p, r, muted=True, style=style)
            p.end()
            return

        # --- Body: always skin primary Color.ACCENT (shape from BUTTON_STYLE) ---
        if style in ("solid", "hud", "dual", "liquid", "cyber"):
            fill = QColor(Color.ACCENT)
            if press:
                fill = fill.darker(114)
            elif h > 0.01:
                # Stay on the same hue — never blend ACCENT_SOFT (often a second blue)
                fill = fill.lighter(100 + int(10 * h))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(r, radius, radius)
            ink = QColor(Color.BG)
        elif style == "outline":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ha(Color.ELEVATED, 0.96))
            p.drawRoundedRect(r, radius, radius)
            ink = QColor(Color.ACCENT if h >= 0.2 else Color.TEXT)
        elif style == "soft":
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ha(Color.ACCENT, 0.88 + 0.12 * h))
            p.drawRoundedRect(r, radius, radius)
            ink = QColor(Color.BG)
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(Color.ACCENT))
            p.drawRoundedRect(r, radius, radius)
            ink = QColor(Color.BG)

        # --- Frame details per style (all accent / bg from active skin) ---
        if style == "hud":
            p.setPen(QPen(ha(Color.BG, 0.35 + 0.2 * h), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), max(0.0, radius - 1), max(0.0, radius - 1))
            p.setPen(QPen(ha(Color.BG, 0.7), 1.4))
            arm = 7.0
            for ox, oy, sx, sy in (
                (r.left(), r.top(), 1, 1),
                (r.right(), r.top(), -1, 1),
                (r.left(), r.bottom(), 1, -1),
                (r.right(), r.bottom(), -1, -1),
            ):
                p.drawLine(QPointF(ox, oy + sy * arm), QPointF(ox, oy))
                p.drawLine(QPointF(ox, oy), QPointF(ox + sx * arm, oy))
        elif style == "dual":
            # Outer ring uses primary accent (same family as fill), not accent_soft blue
            p.setPen(QPen(ha(Color.ACCENT, 0.55 + 0.3 * h), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r.adjusted(-1.2, -1.2, 1.2, 1.2))
            p.setPen(QPen(ha(Color.BG, 0.45), 1.0))
            p.drawRect(r.adjusted(2, 2, -2, -2))
        elif style == "outline":
            p.setPen(QPen(ha(Color.ACCENT, 0.55 + 0.4 * h), 1.25 + 0.4 * h))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
        elif style == "soft":
            p.setPen(QPen(ha(Color.ACCENT, 0.35 + 0.35 * h), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
        elif style == "liquid":
            p.setPen(QPen(ha(Color.BG, 0.4), 1.0, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(2, 2, -2, -2), max(0.0, radius - 1), max(0.0, radius - 1))
        elif style == "cyber":
            p.setPen(QPen(ha(Color.BG, 0.5), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ha(Color.BG, 0.35 + 0.25 * h))
            p.drawRect(QRectF(r.left() + 10, r.top() + 3, r.width() - 20, 2.0))
            p.setPen(QPen(ha(Color.BG, 0.75), 1.5))
            arm = 8.0
            p.drawLine(QPointF(r.left() + 2, r.top() + arm), QPointF(r.left() + 2, r.top() + 2))
            p.drawLine(QPointF(r.left() + 2, r.top() + 2), QPointF(r.left() + arm, r.top() + 2))
            p.drawLine(QPointF(r.right() - arm, r.top() + 2), QPointF(r.right() - 2, r.top() + 2))
            p.drawLine(QPointF(r.right() - 2, r.top() + 2), QPointF(r.right() - 2, r.top() + arm))
        else:  # solid
            p.setPen(QPen(ha(Color.BG, 0.22 + 0.18 * h), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r.adjusted(0.8, 0.8, -0.8, -0.8), max(0.0, radius - 0.5), max(0.0, radius - 0.5))

        # Hover ring stays on primary accent hue
        if h > 0.05 and style not in ("dual",):
            p.setPen(QPen(ha(Color.ACCENT, 0.28 * h), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(full.adjusted(-0.5, -0.5, 0.5, 0.5), radius + 1, radius + 1)

        self._draw_label(p, r, ink=ink, style=style)
        p.end()

    def _draw_label(
        self,
        p: QPainter,
        r: QRectF,
        *,
        ink: QColor | None = None,
        muted: bool = False,
        style: str = "solid",
    ):
        # Small professional UI face on every skin (never mono / Impact / ALL CAPS)
        pt = max(9.5, min(11.0, float(Type.CAPTION)))
        font = pick_ui_font(int(round(pt)), QFont.Weight.Medium)
        font.setPointSizeF(pt)
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.15)
        font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
        )
        p.setFont(font)
        text = self.text() or ""
        if muted:
            p.setPen(self._ha(Color.MUTED, 0.85))
        else:
            p.setPen(ink or QColor(Color.BG))
        p.drawText(r, int(Qt.AlignmentFlag.AlignCenter), text)


def _shadow(blur: int = 14, opacity: int = 50) -> QGraphicsDropShadowEffect:
    s = QGraphicsDropShadowEffect()
    s.setBlurRadius(blur)
    s.setOffset(0, 4)
    c = QColor(Color.SHADOW)
    c.setAlpha(opacity)
    s.setColor(c)
    return s


def _fade_in(widget: QWidget, duration: int = Duration.FADE) -> None:
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(0.0)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity")
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(Easing.STANDARD)
    anim.start()
    widget._fade_anim = anim


def _card_bg(card_type: str) -> str:
    return {
        "danger": Color.with_alpha(Color.ERROR, 0.06),
        "success": Color.with_alpha(Color.SUCCESS, 0.06),
        "primary": Color.with_alpha(Color.ACCENT, 0.06),
        "elevated": Color.ELEVATED,
        "panel": Color.SURFACE,
    }.get(card_type, Color.ELEVATED)


def _card_border(card_type: str) -> str:
    return {
        "danger": Color.with_alpha(Color.ERROR, 0.25),
        "success": Color.with_alpha(Color.SUCCESS, 0.25),
        "primary": Color.with_alpha(Color.ACCENT, 0.25),
    }.get(card_type, Color.LINE)


from PySide6.QtCore import QPropertyAnimation

# ===============================================================
# Cards
# ===============================================================


class Card(QFrame):
    """
    Themed panel surface — shape language from Layout.CARD_SHAPE.

    hud    sharp plate + corner ticks + top scale marks
    glass  restrained rect with hairline + top accent rail
    soft   generous radius, quiet depth
    hard   zero-radius dual edge
    prism  soft radius + multi-tone edge
    liquid dashed inner + solid outer
    cyber  HUD brackets + LED top rail
    """

    clicked = Signal()

    def __init__(
        self,
        card_type: str = "elevated",
        hoverable: bool = False,
        clickable: bool = False,
        elevation: int = Elevation.LOW,
    ):
        super().__init__()
        self._card_type = card_type
        self._hoverable = hoverable
        self._clickable = clickable
        self._hovering = False
        # Shadow strength is theme-driven (0 = flush panels)
        shadow_blur = int(getattr(Layout, "CARD_SHADOW", 28) or 0)
        if shadow_blur > 0 and elevation:
            self.setGraphicsEffect(_shadow(min(elevation, shadow_blur), max(20, shadow_blur // 2)))
        else:
            self.setGraphicsEffect(None)
        # Transparent shell — paint draws the themed frame
        self.setStyleSheet("QFrame { background: transparent; border: none; }")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        if clickable:
            self.setCursor(Qt.PointingHandCursor)

    def _shape(self) -> str:
        return getattr(Layout, "CARD_SHAPE", "glass") or "glass"

    def enterEvent(self, event):
        if self._hoverable:
            self._hovering = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._hoverable:
            self._hovering = False
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = self._shape()
        radius = float(max(0, Radius.CARD))
        if shape == "hard":
            radius = 0.0
        elif shape == "hud":
            radius = min(radius, 3.0)
        elif shape == "cyber":
            radius = min(radius, 4.0)
        elif shape == "soft":
            radius = max(radius, 14.0)
        elif shape == "prism":
            radius = max(radius, 10.0)

        bg = QColor(_card_bg(self._card_type) if not str(_card_bg(self._card_type)).startswith("rgba") else Color.ELEVATED)
        # _card_bg may return rgba() string
        bg_s = _card_bg(self._card_type)
        if str(bg_s).startswith("rgba"):
            # parse rgba(r,g,b,a)
            try:
                inner = str(bg_s)[5:-1]
                parts = [x.strip() for x in inner.split(",")]
                bg = QColor(int(parts[0]), int(parts[1]), int(parts[2]), int(float(parts[3]) * 255))
            except Exception:
                bg = QColor(Color.ELEVATED)
        else:
            bg = QColor(bg_s)

        if self._hovering and self._hoverable:
            bg = QColor(Color.HOVER)

        # Flat fill
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawPath(path)

        def ha(hex_c: str, a: float) -> QColor:
            c = QColor(hex_c)
            c.setAlphaF(max(0.0, min(1.0, a)))
            return c

        border = _card_border(self._card_type)
        if str(border).startswith("rgba"):
            try:
                inner = str(border)[5:-1]
                parts = [x.strip() for x in inner.split(",")]
                edge = QColor(int(parts[0]), int(parts[1]), int(parts[2]), int(float(parts[3]) * 255))
            except Exception:
                edge = ha(Color.LINE, 1.0)
        else:
            edge = QColor(border)

        if self._hovering and self._hoverable:
            edge = ha(Color.ACCENT, 0.65)

        if shape == "hud":
            p.setPen(QPen(edge, 1.1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            # corner ticks
            p.setPen(QPen(ha(Color.ACCENT, 0.55), 1.3))
            arm = 10.0
            corners = (
                (r.left(), r.top(), 1, 1),
                (r.right(), r.top(), -1, 1),
                (r.left(), r.bottom(), 1, -1),
                (r.right(), r.bottom(), -1, -1),
            )
            for ox, oy, sx, sy in corners:
                p.drawLine(QPointF(ox, oy + sy * arm), QPointF(ox, oy))
                p.drawLine(QPointF(ox, oy), QPointF(ox + sx * arm, oy))
            # top scale marks
            p.setPen(QPen(ha(Color.ACCENT, 0.28), 1.0))
            x = r.left() + 12
            while x < r.right() - 12:
                p.drawLine(QPointF(x, r.top() + 1), QPointF(x, r.top() + 4))
                x += 14
        elif shape == "hard":
            p.setPen(QPen(ha(Color.ACCENT, 0.75), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(r)
            p.setPen(QPen(ha(Color.ACCENT_SOFT, 0.45), 1.0))
            p.drawRect(r.adjusted(2.5, 2.5, -2.5, -2.5))
        elif shape == "soft":
            p.setPen(QPen(ha(Color.LINE_HOVER, 0.7), 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            # soft top rail
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ha(Color.ACCENT, 0.14 if not self._hovering else 0.28))
            p.drawRoundedRect(QRectF(r.left() + 18, r.top() + 1, r.width() - 36, 2.0), 1.0, 1.0)
        elif shape == "prism":
            p.setPen(QPen(ha(Color.ACCENT, 0.4), 1.1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            p.setPen(QPen(ha(Color.ACCENT_SOFT, 0.22), 1.0))
            p.drawRoundedRect(r.adjusted(1.5, 1.5, -1.5, -1.5), max(0.0, radius - 1.5), max(0.0, radius - 1.5))
        elif shape == "liquid":
            p.setPen(QPen(edge, 1.1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            p.setPen(QPen(ha(Color.ACCENT, 0.35), 1.0, Qt.PenStyle.DashLine))
            p.drawRoundedRect(r.adjusted(3, 3, -3, -3), max(0.0, radius - 2), max(0.0, radius - 2))
        elif shape == "cyber":
            p.setPen(QPen(ha(Color.LINE_HOVER, 0.95), 1.15))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            # LED top
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ha(Color.ACCENT, 0.45 if not self._hovering else 0.7))
            p.drawRect(QRectF(r.left() + 14, r.top() + 2, r.width() - 28, 2.0))
            # brackets
            p.setPen(QPen(ha(Color.ACCENT, 0.7), 1.5))
            arm = 12.0
            p.drawLine(QPointF(r.left() + 1, r.top() + arm), QPointF(r.left() + 1, r.top() + 1))
            p.drawLine(QPointF(r.left() + 1, r.top() + 1), QPointF(r.left() + arm, r.top() + 1))
            p.drawLine(QPointF(r.right() - arm, r.top() + 1), QPointF(r.right() - 1, r.top() + 1))
            p.drawLine(QPointF(r.right() - 1, r.top() + 1), QPointF(r.right() - 1, r.top() + arm))
            p.drawLine(QPointF(r.left() + 1, r.bottom() - arm), QPointF(r.left() + 1, r.bottom() - 1))
            p.drawLine(QPointF(r.left() + 1, r.bottom() - 1), QPointF(r.left() + arm, r.bottom() - 1))
            p.drawLine(QPointF(r.right() - arm, r.bottom() - 1), QPointF(r.right() - 1, r.bottom() - 1))
            p.drawLine(QPointF(r.right() - 1, r.bottom() - 1), QPointF(r.right() - 1, r.bottom() - arm))
        else:  # glass — clean, not bubble-round
            p.setPen(QPen(edge, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            # single accent top rail (signature, not double hairline chrome)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(ha(Color.ACCENT, 0.22 if not self._hovering else 0.45))
            p.drawRect(QRectF(r.left() + 16, r.top() + 1, r.width() - 32, 1.5))

        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._clickable:
            self.clicked.emit()
        super().mousePressEvent(event)


# ===============================================================
# Metric Row
# ===============================================================


class MetricRow(QFrame):
    """Single metric: label | delta | value."""

    def __init__(self, label: str, value: str, delta: str | None = None, good: bool = True):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background: transparent; border: none;
                border-bottom: 1px solid {Color.LINE};
                border-radius: 0;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        lay.setSpacing(Space.MD)

        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"font-size: {Type.BODY}px; color: {Color.MUTED}; background: transparent; border: none;"
        )
        lay.addWidget(lbl)
        lay.addStretch()

        if delta:
            d_lbl = QLabel(delta)
            d_color = Color.SUCCESS if good else Color.WARNING
            d_lbl.setStyleSheet(
                f"font-size: {Type.BODY}px; font-weight: {Type.WEIGHTS['semibold']}; color: {d_color}; background: transparent; border: none;"
            )
            lay.addWidget(d_lbl)

        val = QLabel(value)
        val.setStyleSheet(
            f"font-size: {Type.BODY}px; font-weight: {Type.WEIGHTS['medium']}; color: {Color.TEXT}; background: transparent; border: none;"
        )
        lay.addWidget(val)


# ===============================================================
# Drop Zone
# ===============================================================


class DropZone(QFrame):
    """
    Themed import surface — each skin paints its own language:
      artifact-scan     radar grid + sweep
      obsidian-glass    black glass + cyan rim
      newton-antigravity soft floating orbs
      chromatic-nihilism dual-neon glitch frame
      crystal-prism     multi-hue refraction
      liquid-logic      telemetry liquid wash
      nodaw-cyber       HUD brackets + LED rail
    """

    filesDropped = Signal(list)

    # Eyebrow copy keyed to skin personality
    _EYEBROWS = {
        "artifact-scan": "RADAR · INGEST",
        "obsidian-glass": "STUDIO IMPORT",
        "newton-antigravity": "FIELD ENTRY",
        "chromatic-nihilism": "DROP · DESTROY",
        "crystal-prism": "PRISM GATE",
        "liquid-logic": "SIGNAL IN",
        "nodaw-cyber": "AUDIO · INGEST",
        "ember-console": "CONSOLE LOAD",
        "arctic-line": "ICE INGEST",
        "rosewood-suite": "SUITE IMPORT",
        "jade-master": "MASTER IN",
        "champagne-noir": "LISTENING ROOM",
    }

    def __init__(
        self,
        title: str = "Import mix",
        subtitle: str = "WAV · MP3 · FLAC · M4A · AIFF",
        chips: list[str] | None = None,
    ):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(148)
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("QFrame { background: transparent; border: none; }")

        self._phase = 0.0
        self._hover = 0.0
        self._drag_over = False
        self._title_text = title
        self._subtitle_text = subtitle
        self._chip_texts = list(
            chips
            or (
                "Score at a Glance",
                "Technical Scorecards",
                "Reference Match",
                "100% Local",
            )
        )

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(28, 18, 28, 18)
        layout.setSpacing(0)

        self._eyebrow = QLabel()
        self._eyebrow.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._eyebrow)
        layout.addSpacing(10)

        icon_row = QHBoxLayout()
        icon_row.setAlignment(Qt.AlignCenter)
        self._icon = IconWidget("plus", size=18, color=Color.ACCENT_SOFT)
        icon_row.addWidget(self._icon, 0, Qt.AlignCenter)
        layout.addLayout(icon_row)
        layout.addSpacing(10)

        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title)
        layout.addSpacing(4)

        self.subtitle = QLabel(subtitle)
        self.subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.subtitle)
        layout.addSpacing(14)

        chip_row = QHBoxLayout()
        chip_row.setSpacing(8)
        chip_row.addStretch(1)
        self._chip_labels: list[QLabel] = []
        for text in self._chip_texts:
            b = QLabel(text)
            b.setAlignment(Qt.AlignCenter)
            self._chip_labels.append(b)
            chip_row.addWidget(b, 0)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)

        self._apply_label_styles()

        self._motion_timer = QTimer(self)
        self._motion_timer.timeout.connect(self._tick_motion)
        self._motion_timer.start(40)
        self.mousePressEvent = lambda e: self.filesDropped.emit([])

    # ------------------------------------------------------------------ theme
    def _skin(self) -> str:
        try:
            return current_skin_id() or "artifact-scan"
        except Exception:
            return "artifact-scan"

    def _apply_label_styles(self) -> None:
        sid = self._skin()
        eyebrow = self._EYEBROWS.get(sid, "IMPORT")
        mono = Type.MONO if sid in ("artifact-scan", "liquid-logic", "nodaw-cyber") else Type.FAMILY
        track = "2.6px" if sid in ("artifact-scan", "chromatic-nihilism") else "2.0px"
        chip_r = 2 if sid in ("artifact-scan", "chromatic-nihilism", "nodaw-cyber") else 999
        chip_bg = Color.with_alpha(Color.ACCENT, 0.06 if sid != "chromatic-nihilism" else 0.10)
        chip_bd = Color.with_alpha(Color.ACCENT, 0.28 if sid != "obsidian-glass" else 0.18)
        if sid == "obsidian-glass":
            chip_bg = Color.with_alpha(Color.ELEVATED, 0.7)
            chip_bd = Color.with_alpha(Color.LINE_HOVER, 0.7)
        elif sid == "crystal-prism":
            chip_bg = Color.with_alpha(Color.ACCENT_SOFT, 0.06)
            chip_bd = Color.with_alpha(Color.ACCENT, 0.30)
        elif sid == "newton-antigravity":
            chip_bg = Color.with_alpha(Color.ACCENT, 0.08)
            chip_bd = Color.with_alpha(Color.ACCENT_SOFT, 0.25)
            chip_r = 14

        self._eyebrow.setText(eyebrow)
        self._eyebrow.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: {track}; "
            f"font-family: {mono}; color: {Color.MUTED}; "
            f"background: transparent; border: none;"
        )
        self.title.setStyleSheet(
            f"font-size: 15px; font-weight: 500; font-family: {Type.FAMILY}; "
            f"color: {Color.TEXT}; letter-spacing: -0.2px; "
            f"background: transparent; border: none;"
        )
        self.subtitle.setStyleSheet(
            f"font-size: 11px; font-weight: 400; font-family: {Type.FAMILY}; "
            f"color: {Color.MUTED}; letter-spacing: 0.8px; "
            f"background: transparent; border: none;"
        )
        for b in self._chip_labels:
            b.setStyleSheet(f"""
                QLabel {{
                    background: {chip_bg};
                    color: {Color.with_alpha(Color.TEXT, 0.82)};
                    border: 1px solid {chip_bd};
                    border-radius: {chip_r}px;
                    padding: 5px 12px;
                    font-size: 10px;
                    font-weight: 500;
                    letter-spacing: 0.35px;
                    font-family: {Type.FAMILY};
                }}
            """)
        self._icon.set_color(Color.ACCENT_SOFT)

    def _tick_motion(self):
        target = 1.0 if self._hovering or self._drag_over else 0.0
        self._hover += (target - self._hover) * 0.14
        if abs(self._hover - target) < 0.008:
            self._hover = target
        # Per-skin motion pace
        sid = self._skin()
        base = {
            "artifact-scan": 0.008,
            "chromatic-nihilism": 0.014,
            "newton-antigravity": 0.004,
            "crystal-prism": 0.005,
            "liquid-logic": 0.007,
            "nodaw-cyber": 0.006,
            "obsidian-glass": 0.0035,
        }.get(sid, 0.004)
        self._phase = (self._phase + base + 0.003 * self._hover) % 1.0
        self.update()

    @property
    def _hovering(self) -> bool:
        return getattr(self, "__hover_flag", False)

    @_hovering.setter
    def _hovering(self, v: bool):
        self.__hover_flag = bool(v)

    # ------------------------------------------------------------------ paint
    @staticmethod
    def _hex_a(hex_color: str, a: float) -> QColor:
        c = QColor(hex_color)
        c.setAlphaF(max(0.0, min(1.0, float(a))))
        return c

    def paintEvent(self, event):
        import math

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        r = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        sid = self._skin()
        # Radius: honor theme scale, then skin-specific hard edges
        radius = float(max(4, Radius.DROP_ZONE if hasattr(Radius, "DROP_ZONE") else Radius.CARD))
        if sid == "artifact-scan":
            radius = 6.0
        elif sid == "chromatic-nihilism":
            radius = 3.0
        elif sid == "nodaw-cyber":
            radius = 8.0
        elif sid == "newton-antigravity":
            radius = max(radius, 22.0)
        elif sid == "crystal-prism":
            radius = max(radius, 18.0)
        elif sid == "obsidian-glass":
            radius = max(radius, 16.0)

        t = self._phase
        hov = self._hover
        if self._drag_over:
            hov = max(hov, 0.9)

        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        p.setClipPath(path)

        w, h = r.width(), r.height()
        cx, cy = r.center().x(), r.center().y()
        ha = self._hex_a

        # Shared deep base — always grounded in theme surfaces
        base = QLinearGradient(r.left(), r.top(), r.left(), r.bottom())
        base.setColorAt(0.0, ha(Color.ELEVATED, 0.96))
        base.setColorAt(0.55, ha(Color.SURFACE, 0.94))
        base.setColorAt(1.0, ha(Color.BG, 0.98))
        p.fillRect(r, base)

        # Skin-specific interior language
        if sid == "artifact-scan":
            self._paint_artifact(p, r, w, h, cx, cy, t, hov, math)
        elif sid == "obsidian-glass":
            self._paint_obsidian(p, r, w, h, cx, cy, t, hov, math)
        elif sid == "newton-antigravity":
            self._paint_newton(p, r, w, h, cx, cy, t, hov, math)
        elif sid == "chromatic-nihilism":
            self._paint_nihilism(p, r, w, h, cx, cy, t, hov, math)
        elif sid == "crystal-prism":
            self._paint_prism(p, r, w, h, cx, cy, t, hov, math)
        elif sid == "liquid-logic":
            self._paint_liquid(p, r, w, h, cx, cy, t, hov, math)
        elif sid == "nodaw-cyber":
            self._paint_cyber(p, r, w, h, cx, cy, t, hov, math)
        else:
            self._paint_obsidian(p, r, w, h, cx, cy, t, hov, math)

        # Icon halo (all skins, accent-tinted)
        icon_cy = r.top() + 42
        ring = QRadialGradient(QPointF(cx, icon_cy), 30)
        ring.setColorAt(0.0, ha(Color.ACCENT, 0.10 + 0.10 * hov))
        ring.setColorAt(0.5, ha(Color.ACCENT, 0.04 + 0.04 * hov))
        ring.setColorAt(1.0, ha(Color.ACCENT, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(ring)
        p.drawEllipse(QPointF(cx, icon_cy), 30, 30)
        p.setPen(QPen(ha(Color.ACCENT, 0.28 + 0.35 * hov), 1.15))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(cx, icon_cy), 17, 17)

        p.setClipping(False)

        # Outer frame
        if sid == "chromatic-nihilism":
            # Dual neon edge: pink outer + cyan inner
            p.setPen(QPen(ha(Color.ACCENT, 0.55 + 0.4 * hov), 1.4))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            inner = r.adjusted(2.0, 2.0, -2.0, -2.0)
            p.setPen(QPen(ha(Color.ACCENT_SOFT, 0.35 + 0.35 * hov), 1.0))
            p.drawRoundedRect(inner, max(1.0, radius - 2), max(1.0, radius - 2))
        elif sid == "nodaw-cyber":
            # Solid HUD panel border + corner brackets
            p.setPen(QPen(ha(Color.LINE_HOVER, 0.9 if not self._drag_over else 1.0), 1.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            self._draw_hud_brackets(p, r, hov)
        elif sid == "artifact-scan":
            p.setPen(QPen(ha(Color.ACCENT, 0.35 + 0.45 * hov), 1.1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            # tick marks on top edge
            p.setPen(QPen(ha(Color.ACCENT, 0.25 + 0.3 * hov), 1.0))
            for i in range(0, int(w), 18):
                x = r.left() + 8 + i
                if x > r.right() - 8:
                    break
                p.drawLine(QPointF(x, r.top() + 1), QPointF(x, r.top() + (5 if i % 54 == 0 else 3)))
        else:
            outer = ha(Color.ACCENT if self._drag_over else Color.LINE_HOVER, 0.45 + 0.45 * hov)
            if self._drag_over:
                outer = ha(Color.ACCENT, 0.85)
            p.setPen(QPen(outer, 1.15))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(r, radius, radius)
            # accent-tinted inner hairline (never pure white)
            inner = r.adjusted(1.6, 1.6, -1.6, -1.6)
            p.setPen(QPen(ha(Color.ACCENT_SOFT, 0.10 + 0.18 * hov), 1.0))
            p.drawRoundedRect(inner, max(1.0, radius - 1.6), max(1.0, radius - 1.6))

        # Bottom accent rail (shared signature, accent-colored)
        if hov > 0.02 or self._drag_over:
            p.setPen(Qt.PenStyle.NoPen)
            rail = QLinearGradient(r.left() + 20, 0, r.right() - 20, 0)
            rail.setColorAt(0.0, ha(Color.ACCENT, 0.0))
            rail.setColorAt(0.5, ha(Color.ACCENT, 0.45 + 0.4 * hov))
            rail.setColorAt(1.0, ha(Color.ACCENT, 0.0))
            if sid == "chromatic-nihilism":
                rail.setColorAt(0.25, ha(Color.ACCENT, 0.55 + 0.3 * hov))
                rail.setColorAt(0.75, ha(Color.ACCENT_SOFT, 0.55 + 0.3 * hov))
            p.setBrush(rail)
            p.drawRoundedRect(QRectF(r.left() + 24, r.bottom() - 2.4, w - 48, 2.0), 1.0, 1.0)

        p.end()

    # ---- per-skin interiors -------------------------------------------------
    def _paint_artifact(self, p, r, w, h, cx, cy, t, hov, math):
        ha = self._hex_a
        # Dim grid
        p.setPen(QPen(ha(Color.ACCENT, 0.06 + 0.05 * hov), 1.0))
        step = 16
        x = r.left()
        while x < r.right():
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            x += step
        y = r.top()
        while y < r.bottom():
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            y += step
        # Radar rings
        p.setPen(QPen(ha(Color.ACCENT, 0.12 + 0.12 * hov), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for rad in (28, 48, 72):
            p.drawEllipse(QPointF(cx, cy - 6), rad, rad * 0.55)
        # Sweep beam
        ang = t * math.tau
        beam = QConicalGradient(QPointF(cx, cy - 6), -math.degrees(ang))
        beam.setColorAt(0.0, ha(Color.ACCENT, 0.22 + 0.18 * hov))
        beam.setColorAt(0.08, ha(Color.ACCENT_SOFT, 0.08))
        beam.setColorAt(0.2, ha(Color.ACCENT, 0.0))
        beam.setColorAt(1.0, ha(Color.ACCENT, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(beam)
        p.drawEllipse(QPointF(cx, cy - 6), 78, 48)
        # Horizontal scanline
        sy = r.top() + ((t * 1.4) % 1.0) * h
        scan = QLinearGradient(r.left(), sy - 6, r.left(), sy + 6)
        scan.setColorAt(0.0, ha(Color.ACCENT, 0.0))
        scan.setColorAt(0.5, ha(Color.ACCENT, 0.16 + 0.12 * hov))
        scan.setColorAt(1.0, ha(Color.ACCENT, 0.0))
        p.setBrush(scan)
        p.drawRect(QRectF(r.left(), sy - 6, w, 12))

    def _paint_obsidian(self, p, r, w, h, cx, cy, t, hov, math):
        ha = self._hex_a
        # Soft dual glow (accent + soft), no white wash
        ax = r.left() + w * (0.28 + 0.10 * math.sin(t * math.tau))
        ay = cy - h * 0.12
        g1 = QRadialGradient(QPointF(ax, ay), max(w, h) * 0.7)
        g1.setColorAt(0.0, ha(Color.ACCENT, 0.10 + 0.08 * hov))
        g1.setColorAt(0.5, ha(Color.ACCENT, 0.03))
        g1.setColorAt(1.0, ha(Color.ACCENT, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g1)
        p.drawRect(r)
        bx = r.left() + w * (0.7 + 0.08 * math.cos(t * math.tau * 0.9 + 0.8))
        by = cy + h * 0.18
        g2 = QRadialGradient(QPointF(bx, by), max(w, h) * 0.55)
        g2.setColorAt(0.0, ha(Color.ACCENT_SOFT, 0.07 + 0.06 * hov))
        g2.setColorAt(1.0, ha(Color.ACCENT_SOFT, 0.0))
        p.setBrush(g2)
        p.drawRect(r)
        # Accent-tinted top specular (not pure white)
        top = QLinearGradient(r.left(), r.top(), r.left(), r.top() + 22)
        top.setColorAt(0.0, ha(Color.ACCENT_SOFT, 0.12 + 0.10 * hov))
        top.setColorAt(0.4, ha(Color.TEXT, 0.04 + 0.03 * hov))
        top.setColorAt(1.0, ha(Color.ACCENT, 0.0))
        p.setBrush(top)
        p.drawRect(QRectF(r.left(), r.top(), w, 24))
        # Soft edge vignette
        vig = QRadialGradient(QPointF(cx, cy), max(w, h) * 0.8)
        vig.setColorAt(0.55, ha(Color.BG, 0.0))
        vig.setColorAt(1.0, ha(Color.BG, 0.35 - 0.12 * hov))
        p.setBrush(vig)
        p.drawRect(r)

    def _paint_newton(self, p, r, w, h, cx, cy, t, hov, math):
        ha = self._hex_a
        # Floating orbs (violet / magenta / gold)
        orbs = [
            (0.22, 0.35, 0.55, Color.ACCENT, 0.16),
            (0.72, 0.55, 0.48, Color.ACCENT_SOFT, 0.12),
            (0.48, 0.22, 0.38, Color.GOLD, 0.07),
            (0.58, 0.78, 0.32, Color.ACCENT, 0.08),
        ]
        p.setPen(Qt.PenStyle.NoPen)
        for ox, oy, sc, col, a0 in orbs:
            px = r.left() + w * (ox + 0.04 * math.sin(t * math.tau + ox * 5))
            py = r.top() + h * (oy + 0.05 * math.cos(t * math.tau * 0.8 + oy * 4))
            rad = max(w, h) * sc * 0.45
            g = QRadialGradient(QPointF(px, py), rad)
            g.setColorAt(0.0, ha(col, (a0 + 0.08 * hov)))
            g.setColorAt(0.55, ha(col, a0 * 0.35))
            g.setColorAt(1.0, ha(col, 0.0))
            p.setBrush(g)
            p.drawEllipse(QPointF(px, py), rad, rad)
        # Soft vertical lift glow
        lift = QLinearGradient(r.left(), r.bottom(), r.left(), r.top())
        lift.setColorAt(0.0, ha(Color.ACCENT, 0.08 + 0.06 * hov))
        lift.setColorAt(0.45, ha(Color.ACCENT, 0.0))
        lift.setColorAt(1.0, ha(Color.ACCENT_SOFT, 0.04))
        p.setBrush(lift)
        p.drawRect(r)

    def _paint_nihilism(self, p, r, w, h, cx, cy, t, hov, math):
        ha = self._hex_a
        # Split diagonal fields: hot pink vs cyan
        diag = QLinearGradient(r.left(), r.top(), r.right(), r.bottom())
        diag.setColorAt(0.0, ha(Color.ACCENT, 0.10 + 0.08 * hov))
        diag.setColorAt(0.45, ha(Color.BG, 0.0))
        diag.setColorAt(0.55, ha(Color.BG, 0.0))
        diag.setColorAt(1.0, ha(Color.ACCENT_SOFT, 0.10 + 0.08 * hov))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(diag)
        p.drawRect(r)
        # Glitch offset bars
        p.setPen(Qt.PenStyle.NoPen)
        for i, (yy, col, a) in enumerate(
            (
                (0.18, Color.ACCENT, 0.14),
                (0.42, Color.ACCENT_SOFT, 0.10),
                (0.71, Color.GOLD, 0.08),
                (0.88, Color.ACCENT, 0.12),
            )
        ):
            off = 6 * math.sin(t * math.tau * 3 + i)
            bar = QRectF(r.left() + off, r.top() + h * yy, w * 0.35, 2.0)
            p.setBrush(ha(col, a + 0.1 * hov))
            p.drawRect(bar)
            bar2 = QRectF(r.right() - w * 0.28 + off * 0.5, r.top() + h * yy + 4, w * 0.28, 1.5)
            p.setBrush(ha(col, (a + 0.1 * hov) * 0.7))
            p.drawRect(bar2)
        # Hard noise hash lines
        p.setPen(QPen(ha(Color.TEXT, 0.04 + 0.03 * hov), 1.0))
        x = r.left() + 4
        while x < r.right():
            p.drawLine(QPointF(x, r.top() + 4), QPointF(x + 3, r.bottom() - 4))
            x += 22

    def _paint_prism(self, p, r, w, h, cx, cy, t, hov, math):
        ha = self._hex_a
        # Prismatic multi-stop wash
        shift = 0.08 * math.sin(t * math.tau)
        pr = QLinearGradient(r.left(), r.top(), r.right(), r.bottom())
        pr.setColorAt(0.0, ha(Color.ACCENT, 0.10 + 0.06 * hov))
        pr.setColorAt(0.28 + shift, ha(Color.ACCENT_SOFT, 0.08 + 0.05 * hov))
        pr.setColorAt(0.55, ha(Color.GOLD, 0.06 + 0.04 * hov))
        pr.setColorAt(0.82 - shift, ha(Color.ACCENT, 0.07))
        pr.setColorAt(1.0, ha(Color.ACCENT_SOFT, 0.05))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(pr)
        p.drawRect(r)
        # Soft refraction beams
        for i, frac in enumerate((0.2, 0.45, 0.7)):
            px = r.left() + w * (frac + 0.03 * math.sin(t * math.tau + i))
            beam = QLinearGradient(px, r.top(), px + w * 0.12, r.bottom())
            col = (Color.ACCENT, Color.ACCENT_SOFT, Color.GOLD)[i]
            beam.setColorAt(0.0, ha(col, 0.0))
            beam.setColorAt(0.4, ha(col, 0.08 + 0.07 * hov))
            beam.setColorAt(1.0, ha(col, 0.0))
            p.setBrush(beam)
            p.drawRect(r)
        # Crystal top highlight
        top = QLinearGradient(r.left(), r.top(), r.left(), r.top() + 28)
        top.setColorAt(0.0, ha(Color.TEXT, 0.10 + 0.08 * hov))
        top.setColorAt(0.5, ha(Color.ACCENT_SOFT, 0.05))
        top.setColorAt(1.0, ha(Color.ACCENT, 0.0))
        p.setBrush(top)
        p.drawRect(QRectF(r.left(), r.top(), w, 30))

    def _paint_liquid(self, p, r, w, h, cx, cy, t, hov, math):
        ha = self._hex_a
        # Flowing liquid blobs
        p.setPen(Qt.PenStyle.NoPen)
        for i, (ox, oy, sc, col) in enumerate(
            (
                (0.25, 0.4, 0.7, Color.ACCENT),
                (0.7, 0.55, 0.6, Color.ACCENT_SOFT),
                (0.5, 0.25, 0.45, Color.GOLD),
            )
        ):
            px = r.left() + w * (ox + 0.06 * math.sin(t * math.tau + i * 1.7))
            py = r.top() + h * (oy + 0.07 * math.cos(t * math.tau * 0.7 + i))
            rad = max(w, h) * sc * 0.4
            g = QRadialGradient(QPointF(px, py), rad)
            g.setColorAt(0.0, ha(col, 0.14 + 0.1 * hov))
            g.setColorAt(0.55, ha(col, 0.05))
            g.setColorAt(1.0, ha(col, 0.0))
            p.setBrush(g)
            p.drawEllipse(QPointF(px, py), rad * 1.15, rad * 0.75)
        # Telemetry dashed lines
        p.setPen(QPen(ha(Color.ACCENT, 0.12 + 0.1 * hov), 1.0, Qt.PenStyle.DashLine))
        y1 = r.top() + h * (0.3 + 0.04 * math.sin(t * math.tau))
        y2 = r.top() + h * (0.72 + 0.03 * math.cos(t * math.tau))
        p.drawLine(QPointF(r.left() + 12, y1), QPointF(r.right() - 12, y1))
        p.drawLine(QPointF(r.left() + 12, y2), QPointF(r.right() - 12, y2))
        # Data ticks
        p.setPen(QPen(ha(Color.ACCENT_SOFT, 0.18 + 0.12 * hov), 1.0))
        for i in range(8):
            x = r.left() + 16 + i * ((w - 32) / 7)
            p.drawLine(QPointF(x, y1 - 3), QPointF(x, y1 + 3))

    def _paint_cyber(self, p, r, w, h, cx, cy, t, hov, math):
        ha = self._hex_a
        # Panel fill wash
        wash = QLinearGradient(r.left(), r.top(), r.right(), r.bottom())
        wash.setColorAt(0.0, ha(Color.ACCENT, 0.06 + 0.05 * hov))
        wash.setColorAt(0.5, ha(Color.SURFACE, 0.0))
        wash.setColorAt(1.0, ha(Color.SUCCESS, 0.05 + 0.04 * hov))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(wash)
        p.drawRect(r)
        # Fine HUD grid
        p.setPen(QPen(ha(Color.LINE, 0.55), 1.0))
        step = 20
        x = r.left() + 10
        while x < r.right() - 10:
            p.drawLine(QPointF(x, r.top() + 8), QPointF(x, r.bottom() - 8))
            x += step
        # LED status rail top
        led = QLinearGradient(r.left() + 16, 0, r.right() - 16, 0)
        led.setColorAt(0.0, ha(Color.ACCENT, 0.0))
        led.setColorAt(0.3, ha(Color.ACCENT, 0.35 + 0.25 * hov))
        led.setColorAt(0.5, ha(Color.SUCCESS, 0.40 + 0.25 * hov))
        led.setColorAt(0.7, ha(Color.ACCENT, 0.35 + 0.25 * hov))
        led.setColorAt(1.0, ha(Color.ACCENT, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(led)
        p.drawRect(QRectF(r.left() + 16, r.top() + 3, w - 32, 2.0))
        # Pulse node
        nx = r.left() + w * ((t * 0.85) % 1.0)
        node = QRadialGradient(QPointF(nx, r.top() + 4), 14)
        node.setColorAt(0.0, ha(Color.ACCENT_SOFT, 0.55))
        node.setColorAt(1.0, ha(Color.ACCENT, 0.0))
        p.setBrush(node)
        p.drawEllipse(QPointF(nx, r.top() + 4), 14, 8)

    def _draw_hud_brackets(self, p, r, hov):
        ha = self._hex_a
        p.setPen(QPen(ha(Color.ACCENT, 0.55 + 0.35 * hov), 1.6))
        arm = 14.0
        # TL
        p.drawLine(QPointF(r.left() + 2, r.top() + arm), QPointF(r.left() + 2, r.top() + 2))
        p.drawLine(QPointF(r.left() + 2, r.top() + 2), QPointF(r.left() + arm, r.top() + 2))
        # TR
        p.drawLine(QPointF(r.right() - arm, r.top() + 2), QPointF(r.right() - 2, r.top() + 2))
        p.drawLine(QPointF(r.right() - 2, r.top() + 2), QPointF(r.right() - 2, r.top() + arm))
        # BL
        p.drawLine(QPointF(r.left() + 2, r.bottom() - arm), QPointF(r.left() + 2, r.bottom() - 2))
        p.drawLine(QPointF(r.left() + 2, r.bottom() - 2), QPointF(r.left() + arm, r.bottom() - 2))
        # BR
        p.drawLine(QPointF(r.right() - arm, r.bottom() - 2), QPointF(r.right() - 2, r.bottom() - 2))
        p.drawLine(QPointF(r.right() - 2, r.bottom() - 2), QPointF(r.right() - 2, r.bottom() - arm))

    # ------------------------------------------------------------------ events
    def enterEvent(self, event):
        self._hovering = True
        self._icon.set_color(Color.ACCENT)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        if not self._drag_over:
            self._icon.set_color(Color.ACCENT_SOFT)
        super().leaveEvent(event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._drag_over = True
            self._icon.set_color(Color.ACCENT)
            self.update()

    def dragLeaveEvent(self, event):
        self._drag_over = False
        self._icon.set_color(Color.ACCENT_SOFT if not self._hovering else Color.ACCENT)
        self.update()

    def dropEvent(self, event: QDropEvent):
        urls = [u.toLocalFile() for u in event.mimeData().urls()]
        self.filesDropped.emit(urls)
        self._drag_over = False
        self._icon.set_color(Color.ACCENT_SOFT)
        self.update()


# ===============================================================
# Score Display
# ===============================================================


class ScoreDisplay(QFrame):
    """Premium animated score card with color-coded verdict badge."""

    def __init__(self):
        super().__init__()
        self.setGraphicsEffect(_shadow(Elevation.HIGH, 45))
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.SURFACE};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.CARD}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.XXL, Space.XL, Space.XXL, Space.XL)
        lay.setSpacing(Space.SM)

        self._number = QLabel("--")
        self._number.setAlignment(Qt.AlignCenter)
        self._number.setStyleSheet(
            f"font-size: {Type.DISPLAY_XL}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {Color.MUTED}; letter-spacing: -2px; background: transparent; border: none;"
        )
        lay.addWidget(self._number)

        self._badge = QFrame()
        self._badge.setFixedHeight(28)
        bl = QHBoxLayout(self._badge)
        bl.setContentsMargins(Space.MD, 0, Space.MD, 0)
        bl.setAlignment(Qt.AlignCenter)
        bl.setSpacing(Space.SM)

        self._badge_icon = IconWidget("star", size=14, color=Color.MUTED)
        bl.addWidget(self._badge_icon)

        self._badge_label = QLabel("Awaiting analysis")
        self._badge_label.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 0.3px;"
        )
        bl.addWidget(self._badge_label)
        self._badge.setStyleSheet(
            f"background: {Color.with_alpha(Color.MUTED, 0.10)}; border-radius: {Radius.PILL}px; border: none;"
        )
        lay.addWidget(self._badge)

        self._summary = QLabel("")
        self._summary.setAlignment(Qt.AlignCenter)
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; line-height: 1.4; background: transparent; border: none;"
        )
        lay.addWidget(self._summary)

        self._target: int | None = None
        self._current: int = 0
        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)

    def set_score(self, score: int | None = None, rating: str = "", summary: str = ""):
        if score is None:
            self._number.setText("--")
            self._number.setStyleSheet(
                f"font-size: {Type.DISPLAY_XL}px; font-weight: {Type.WEIGHTS['bold']}; "
                f"color: {Color.MUTED}; letter-spacing: -2px; background: transparent; border: none;"
            )
            self._badge_label.setText("Awaiting analysis")
            self._badge_icon.set_color(Color.MUTED)
            self._badge.setStyleSheet(
                f"background: {Color.with_alpha(Color.MUTED, 0.10)}; border-radius: {Radius.PILL}px; border: none;"
            )
            self._summary.setText("")
            return

        self._target = score
        self._current = 0
        self._badge_label.setText(rating or score_rating(score))
        self._summary.setText(summary or "")
        self._update_badge(score)
        self._anim.start(20)

    def _update_badge(self, score: int):
        c = score_color(score)
        self._badge_icon.set_color(c)
        self._badge.setStyleSheet(
            f"background: {Color.with_alpha(c, 0.12)}; border-radius: {Radius.PILL}px; border: none;"
        )
        self._badge_label.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {c}; background: transparent; border: none; letter-spacing: 0.3px;"
        )

    def _tick(self):
        if self._target is None:
            self._anim.stop()
            return
        step = max(1, (self._target - self._current) // 5)
        self._current = min(self._current + step, self._target)
        c = score_color(self._current)
        self._number.setText(str(self._current))
        self._number.setStyleSheet(
            f"font-size: {Type.DISPLAY_XL}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {c}; letter-spacing: -2px; background: transparent; border: none;"
        )
        if self._current >= self._target:
            self._anim.stop()


# ===============================================================
# Recommendation Card
# ===============================================================


class RecommendationCard(QFrame):
    """Actionable recommendations with check icons and optional one-click FFmpeg repair."""

    repairClicked = Signal(str)

    def __init__(self):
        super().__init__()
        self.setGraphicsEffect(_shadow(Elevation.MED, 45))
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.SURFACE};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.CARD}px;
            }}
        """)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        self.lay.setSpacing(Space.SM)

        self._rebuild_header()
        self._empty = QLabel("No analysis yet.\nDrop a mix to get started.")
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet(
            f"color: {Color.MUTED}; font-size: {Type.BODY}px; background: transparent; border: none;"
        )
        self.lay.addWidget(self._empty)

    def _rebuild_header(self, title: str = "Recommended Actions"):
        hdr = QHBoxLayout()
        hdr.setSpacing(Space.SM)
        hi = IconWidget("sparkle", size=16, color=Color.MUTED)
        hdr.addWidget(hi)
        hl = QLabel(title)
        hl.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; letter-spacing: 0.5px; background: transparent; border: none;"
        )
        hdr.addWidget(hl)
        hdr.addStretch()
        self.lay.addLayout(hdr)

    def _clear(self):
        """Remove all layout items safely (widgets + nested layouts)."""
        while self.lay.count():
            item = self.lay.takeAt(0)
            w = item.widget()
            if w is not None:
                if w is self._empty:
                    w.setParent(None)
                else:
                    w.deleteLater()
            child = item.layout()
            if child is not None:
                while child.count():
                    sub = child.takeAt(0)
                    sw = sub.widget()
                    if sw is not None:
                        sw.deleteLater()

    def set_items(self, items: list[str] | None, header: str = "Recommended Actions"):
        self._clear()
        if not items:
            self._empty.setText("No analysis yet.\nDrop a mix to get started.")
            self.lay.addWidget(self._empty)
            self.adjustSize()
            return
        self._rebuild_header(header)
        for rec in items[:12]:
            row = QHBoxLayout()
            row.setSpacing(Space.SM)
            icon = IconWidget("check", size=14, color=Color.ACCENT)
            row.addWidget(icon)
            lbl = QLabel(rec)
            lbl.setWordWrap(True)
            lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            lbl.setStyleSheet(
                f"color: {Color.TEXT}; font-size: {Type.BODY - 1}px; "
                f"line-height: 1.4; background: transparent; border: none;"
            )
            row.addWidget(lbl, 1)
            self.lay.addLayout(row)
        # No trailing stretch - height follows content (works inside QScrollArea)
        self.adjustSize()

    @staticmethod
    def _friendly_filter_desc(filter_chain: str) -> list[str]:
        """Turn an ffmpeg filter chain into user-friendly benefit bullets."""
        bullets = []
        if "loudnorm" in filter_chain:
            bullets.append(
                "Normalize loudness to streaming targets (Spotify, Apple Music, YouTube)"
            )
        if "alimiter" in filter_chain or "limiter" in filter_chain:
            bullets.append("Protect against intersample peaks and digital clipping")
        if "highpass" in filter_chain:
            bullets.append("Reduce subsonic rumble and DC offset")
        if "equalizer" in filter_chain:
            bullets.append("Match spectral balance to your reference track")
        if "anull" in filter_chain or not filter_chain:
            bullets.append("No automatic correction required by current thresholds")
        if not bullets:
            bullets.append("Apply conservative mastering-grade corrections")
        return bullets

    def set_repairs(self, repairs: list[dict]):
        """Engine checklist with per-item FFmpeg command + Run in Terminal."""
        self._clear()
        self._rebuild_header("Engine checklist")

        if not repairs:
            self.lay.addWidget(self._empty)
            return

        for rep in repairs[:6]:
            title = rep.get("title", "Suggested correction")
            filter_chain = (
                rep.get("ffmpeg_filter")
                or rep.get("filter")
                or rep.get("ffmpeg")
                or ""
            )
            filter_chain = str(filter_chain).strip()
            command = (
                rep.get("command")
                or rep.get("ffmpeg_command")
                or rep.get("cli")
                or ""
            )
            command = str(command).strip()
            # If only a filter was provided, build a minimal ffmpeg line (caller should
            # normally pass a full absolute-path command via _live_repair_dicts)
            if filter_chain and not command.lower().startswith("ffmpeg"):
                command = f'ffmpeg -y -i "INPUT" -af "{filter_chain}" "OUTPUT_repaired.wav"'
            caution = rep.get("caution", "") or ""
            benefits = self._friendly_filter_desc(filter_chain)
            is_noop = (not command) or (
                filter_chain == "anull" and "all suggested" not in title.lower()
                and "auto technical" not in title.lower()
            )

            card = QFrame()
            card.setStyleSheet(
                f"background: {Color.ELEVATED}; border: 1px solid {Color.LINE}; "
                f"border-radius: {Radius.MD}px;"
            )
            cl = QVBoxLayout(card)
            cl.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
            cl.setSpacing(Space.XS)

            tl = QLabel(title)
            tl.setWordWrap(True)
            tl.setStyleSheet(
                f"font-size: {Type.BODY}px; font-weight: {Type.WEIGHTS['medium']}; "
                f"color: {Color.TEXT}; background: transparent; border: none;"
            )
            cl.addWidget(tl)

            reason = rep.get("reason", "") or ""
            if reason:
                rl = QLabel(reason)
                rl.setWordWrap(True)
                rl.setStyleSheet(
                    f"font-size: {Type.CAPTION - 1}px; color: {Color.MUTED}; "
                    f"background: transparent; border: none;"
                )
                cl.addWidget(rl)

            for b in benefits:
                row = QHBoxLayout()
                row.setSpacing(Space.SM)
                icon = IconWidget("check", size=10, color=Color.SUCCESS)
                row.addWidget(icon)
                bl = QLabel(b)
                bl.setWordWrap(True)
                bl.setStyleSheet(
                    f"font-size: {Type.CAPTION - 1}px; color: {Color.TEXT}; "
                    f"background: transparent; border: none;"
                )
                row.addWidget(bl, 1)
                cl.addLayout(row)

            if caution:
                clbl = QLabel(caution)
                clbl.setWordWrap(True)
                clbl.setStyleSheet(
                    f"font-size: {Type.CAPTION - 1}px; color: {Color.WARNING}; "
                    f"background: transparent; border: none;"
                )
                cl.addWidget(clbl)

            # FFmpeg command always visible — this exact string is what Terminal runs
            cmd_hdr = QLabel("FFmpeg command")
            cmd_hdr.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 0.8px; "
                f"color: {Color.MUTED}; background: transparent; border: none; margin-top: 4px;"
            )
            cl.addWidget(cmd_hdr)
            display_cmd = command or "(no command)"
            cmd_lbl = QLabel(display_cmd)
            cmd_lbl.setWordWrap(True)
            cmd_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            # Authoritative command for Terminal/Copy (survives label elision / wrapping)
            cmd_lbl.setProperty("ffmpeg_command", command)
            cmd_lbl.setStyleSheet(
                f"font-size: {Type.CAPTION - 1}px; font-family: {Type.MONO}; "
                f"color: {Color.TEXT}; background: {Color.with_alpha(Color.ACCENT, 0.08)}; "
                f"padding: 6px 8px; border-radius: {Radius.SM}px; border: 1px solid {Color.LINE};"
            )
            cl.addWidget(cmd_lbl)

            def _cmd_from_label(lbl: QLabel = cmd_lbl) -> str:
                raw = lbl.property("ffmpeg_command")
                text = str(raw).strip() if raw else ""
                if not text or text == "(no command)":
                    text = (lbl.text() or "").strip()
                if text == "(no command)":
                    return ""
                return text

            # Actions: Run in Terminal (primary) + Copy
            btn_row = QHBoxLayout()
            btn_row.setSpacing(Space.SM)
            run_btn = QPushButton("Run in Terminal")
            run_btn.setCursor(Qt.PointingHandCursor)
            run_btn.setEnabled(bool(command) and not is_noop)
            run_btn.setToolTip(
                "Opens a terminal, pastes the FFmpeg command shown above, and presses Enter "
                "to run it. Only that command — nothing else."
            )
            run_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {Color.ACCENT};
                    color: {Color.BG};
                    border: none;
                    border-radius: {Radius.MD}px;
                    padding: 7px 14px;
                    font-size: {Type.CAPTION}px;
                    font-weight: {Type.WEIGHTS['semibold']};
                }}
                QPushButton:hover {{ background: {Color.ACCENT_SOFT}; }}
                QPushButton:disabled {{
                    background: {Color.LINE};
                    color: {Color.MUTED};
                }}
            """)
            # Terminal only — use the exact command displayed on the card (not a silent re-run)
            run_btn.clicked.connect(
                lambda checked=False, get=_cmd_from_label: (
                    run_ffmpeg_in_terminal(get()) if get() else None
                )
            )
            btn_row.addWidget(run_btn)

            copy_btn = QPushButton("Copy")
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.setEnabled(bool(command))
            copy_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {Color.TEXT};
                    border: 1px solid {Color.LINE};
                    border-radius: {Radius.MD}px;
                    padding: 7px 12px;
                    font-size: {Type.CAPTION}px;
                }}
                QPushButton:hover {{
                    border-color: {Color.ACCENT};
                    background: {Color.HOVER};
                }}
            """)
            copy_btn.clicked.connect(
                lambda checked=False, get=_cmd_from_label: (
                    QApplication.clipboard().setText(get()) if get() else None
                )
            )
            btn_row.addWidget(copy_btn)
            btn_row.addStretch(1)
            cl.addLayout(btn_row)

            # Advanced (filter chain detail) collapsed
            adv_btn = QPushButton("[+] Advanced details")
            adv_btn.setCursor(Qt.PointingHandCursor)
            adv_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none; text-align: left;
                    font-size: {Type.CAPTION - 1}px; color: {Color.MUTED};
                    padding: {Space.XS}px 0 {Space.XS}px 0;
                }}
                QPushButton:hover {{ color: {Color.ACCENT}; }}
            """)
            adv_content = QFrame()
            adv_content.setStyleSheet("background: transparent; border: none;")
            adv_content.hide()
            ac_lay = QVBoxLayout(adv_content)
            ac_lay.setContentsMargins(0, 0, 0, 0)
            ac_lay.setSpacing(Space.XS)
            fl = QLabel(
                f"Filter chain:  {filter_chain}"
                if filter_chain
                else "Filter chain:  anull (passthrough)"
            )
            fl.setWordWrap(True)
            fl.setStyleSheet(
                f"font-size: {Type.CAPTION - 1}px; font-family: {Type.MONO}; "
                f"color: {Color.ACCENT}; background: {Color.with_alpha(Color.ACCENT, 0.06)}; "
                f"padding: {Space.XS}px {Space.SM}px; border-radius: {Radius.SM}px; border: none;"
            )
            ac_lay.addWidget(fl)
            adv_btn.clicked.connect(
                lambda checked=False, a=adv_content, b=adv_btn: (
                    a.setVisible(not a.isVisible()),
                    b.setText(
                        "[-] Advanced details" if a.isVisible() else "[+] Advanced details"
                    ),
                )
            )
            cl.addWidget(adv_btn)
            cl.addWidget(adv_content)

            self.lay.addWidget(card)

        self.adjustSize()


# ===============================================================
# Verdict Badge
# ===============================================================


class VerdictBadge(QFrame):
    """Overall verdict bar with status dot and score pill."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(48)
        self.setStyleSheet(
            f"background: {Color.ELEVATED}; border-radius: {Radius.XL}px; border: 1px solid {Color.LINE};"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(Space.XL, 0, Space.XL, 0)
        lay.setSpacing(Space.MD)

        self._dot = QFrame()
        self._dot.setFixedSize(10, 10)
        self._dot.setStyleSheet(f"background: {Color.MUTED}; border-radius: 5px; border: none;")
        lay.addWidget(self._dot)

        self._label = QLabel("Drop a mix to analyze")
        self._label.setStyleSheet(
            f"font-size: {Type.BODY + 1}px; font-weight: {Type.WEIGHTS['medium']}; "
            f"color: {Color.MUTED}; background: transparent; border: none;"
        )
        lay.addWidget(self._label, 1)

        self._pill = QFrame()
        self._pill.setFixedWidth(48)
        self._pill.setStyleSheet(
            f"background: {Color.with_alpha(Color.MUTED, 0.12)}; border-radius: {Radius.PILL}px; border: none;"
        )
        pl = QHBoxLayout(self._pill)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setAlignment(Qt.AlignCenter)
        self._pill_label = QLabel("")
        self._pill_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none;"
        )
        pl.addWidget(self._pill_label)
        lay.addWidget(self._pill)
        self._pill.hide()

    def set_verdict(self, score: int | None, rating: str = ""):
        if score is None:
            self._dot.setStyleSheet(f"background: {Color.MUTED}; border-radius: 5px; border: none;")
            self._label.setText("Drop a mix to analyze")
            self._label.setStyleSheet(
                f"font-size: {Type.BODY + 1}px; font-weight: {Type.WEIGHTS['medium']}; "
                f"color: {Color.MUTED}; background: transparent; border: none;"
            )
            self._pill.hide()
            return

        c = score_color(score)
        self._dot.setStyleSheet(f"background: {c}; border-radius: 5px; border: none;")
        self._label.setText(rating or score_rating(score))
        self._label.setStyleSheet(
            f"font-size: {Type.BODY + 1}px; font-weight: {Type.WEIGHTS['medium']}; "
            f"color: {Color.TEXT}; background: transparent; border: none;"
        )
        self._pill_label.setText(str(score))
        self._pill_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {c}; background: transparent; border: none;"
        )
        self._pill.setStyleSheet(
            f"background: {Color.with_alpha(c, 0.18)}; border-radius: {Radius.PILL}px; border: none;"
        )
        self._pill.show()


# ===============================================================
# Status Badge
# ===============================================================


class StatusBadge(QFrame):
    """Small colored pill with icon for semantic status."""

    STATUS_ICONS = {
        "success": "check",
        "warning": "alert",
        "error": "close",
        "accent": "info",
        "neutral": "info",
    }

    def __init__(self, text: str, status: str = "neutral", size: str = "md"):
        super().__init__()
        sizes_map = {"sm": 20, "md": 24, "lg": 28}
        h = sizes_map.get(size, 24)
        color_map = {
            "neutral": Color.MUTED,
            "success": Color.SUCCESS,
            "warning": Color.WARNING,
            "error": Color.ERROR,
            "accent": Color.ACCENT,
        }
        c = color_map.get(status, Color.MUTED)

        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.with_alpha(c, 0.12)};
                border-radius: {h // 2}px;
                border: none;
            }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(Space.MD, 2, Space.MD, 2)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(Space.XS)

        icon_name = self.STATUS_ICONS.get(status, "info")
        icon = IconWidget(icon_name, size=h - 6, color=c)
        lay.addWidget(icon)

        lbl = QLabel(text)
        lbl.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {c}; background: transparent; border: none; letter-spacing: 0.3px;"
        )
        lay.addWidget(lbl)


# ===============================================================
# Collapsible Section
# ===============================================================


class CollapsibleSection(QFrame):
    """Expandable card section with chevron toggle."""

    toggled = Signal(str, bool)

    def __init__(self, title: str, content: QWidget, expanded: bool = False):
        super().__init__()
        self._expanded = expanded
        self._content = content
        self._title_text = title

        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.GLASS};
                border: 1px solid {Color.GLASS_BORDER};
                border-radius: {Radius.CARD}px;
            }}
        """)
        self.setGraphicsEffect(_shadow(Elevation.LOW, 35))

        main_lay = QVBoxLayout(self)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(0)

        # Header button
        btn = QPushButton()
        btn.setCursor(Qt.PointingHandCursor)
        btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; border: none; border-radius: {Radius.XL}px;
                padding: {Space.LG}px {Space.XL}px;
                text-align: left; font-size: {Type.BODY}px;
                font-weight: {Type.WEIGHTS["semibold"]}; color: {Color.TEXT};
            }}
            QPushButton:hover {{ color: {Color.ACCENT}; }}
        """)
        btn.clicked.connect(self._toggle)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(Space.LG, Space.LG, Space.XL, Space.LG)
        btn_layout.setSpacing(Space.SM)

        self._chevron = IconWidget(
            "chevron_down" if expanded else "chevron_right", size=16, color=Color.MUTED
        )
        btn_layout.addWidget(self._chevron)

        self._title = QLabel(title)
        self._title.setStyleSheet(
            "font-size: 14px; font-weight: 600; color: white; background: transparent; border: none;"
        )
        btn_layout.addWidget(self._title, 1)
        btn_layout.addStretch()

        btn.setLayout(btn_layout)
        main_lay.addWidget(btn)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background: {Color.LINE}; border: none;")
        main_lay.addWidget(sep)

        content.setVisible(expanded)
        content.setStyleSheet("background: transparent; border: none; border-radius: 0;")
        main_lay.addWidget(content)

    def _toggle(self):
        self._expanded = not self._expanded
        self._content.setVisible(self._expanded)
        self._chevron.set_name("chevron_down" if self._expanded else "chevron_right")
        self.toggled.emit(self._title_text, self._expanded)


# ===============================================================
# Finding Card
# ===============================================================


class FindingCard(QFrame):
    """Single engineering finding with icon and severity badge."""

    SEV_ICONS = {
        "pass": "check",
        "critical": "close",
        "warning": "alert",
        "notice": "info",
    }

    def __init__(self, severity: str, title: str, message: str, action: str = ""):
        super().__init__()
        sev_map = {
            "pass": Color.SUCCESS,
            "critical": Color.ERROR,
            "warning": Color.WARNING,
            "notice": Color.MUTED,
        }
        sev_color = sev_map.get(severity, Color.MUTED)

        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.LG}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        lay.setSpacing(Space.XS)

        hdr = QHBoxLayout()
        hdr.setSpacing(Space.SM)

        icon_name = self.SEV_ICONS.get(severity, "info")
        icon = IconWidget(icon_name, size=16, color=sev_color)
        hdr.addWidget(icon)

        badge = QLabel(severity.upper())
        badge.setStyleSheet(
            f"font-size: {Type.TINY}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {sev_color}; background: {Color.with_alpha(sev_color, 0.12)}; "
            f"padding: 2px {Space.SM}px; border-radius: {Radius.SM}px; border: none;"
        )
        hdr.addWidget(badge)

        t = QLabel(f"<b>{title}</b>")
        t.setStyleSheet(
            f"font-size: {Type.BODY}px; color: {Color.TEXT}; background: transparent; border: none;"
        )
        hdr.addWidget(t, 1)
        lay.addLayout(hdr)

        m = QLabel(message)
        m.setWordWrap(True)
        m.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent; border: none;"
        )
        lay.addWidget(m)

        if action:
            a = QLabel(f"<b>Action:</b> {action}")
            a.setWordWrap(True)
            a.setStyleSheet(
                f"font-size: {Type.CAPTION}px; color: {Color.TEXT}; background: transparent; border: none;"
            )
            lay.addWidget(a)


# ===============================================================
# Empty State
# ===============================================================


class EmptyState(QFrame):
    """Centered empty state with SVG illustration."""

    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.setStyleSheet("background: transparent; border: none;")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(Space.SM)

        from .icons import IconWidget

        ill = IconWidget("empty_state", size=80, color=Color.MUTED)
        lay.addWidget(ill, 0, Qt.AlignCenter)

        t = QLabel(title)
        t.setAlignment(Qt.AlignCenter)
        t.setWordWrap(True)
        t.setStyleSheet(
            f"font-size: {Type.H2}px; font-weight: {Type.WEIGHTS['medium']}; "
            f"color: {Color.TEXT}; background: transparent; border: none;"
        )
        lay.addWidget(t)

        sub = QLabel(subtitle)
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        sub.setStyleSheet(
            f"font-size: {Type.BODY}px; color: {Color.MUTED}; background: transparent; border: none;"
        )
        lay.addWidget(sub)


# ===============================================================
# Loading Bar
# ===============================================================


class LoadingBar(QFrame):
    """Animated loading bar with shimmer."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(4)
        self.setStyleSheet(f"background: {Color.LINE}; border-radius: 2px; border: none;")

        self._fill = QFrame(self)
        self._fill.setFixedHeight(4)
        self._fill.setStyleSheet(f"background: {Color.ACCENT}; border-radius: 2px; border: none;")
        self._fill.setFixedWidth(0)

        self._progress = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    def start(self):
        self._progress = 0
        self.show()
        self._timer.start(50)

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        parent_w = self.width() or 400
        self._progress = (self._progress + 6) % (parent_w + 120)
        w = min(120, parent_w // 3)
        self._fill.setFixedWidth(w)
        self._fill.move(self._progress - w, 0)


# ===============================================================
# Recent Card
# ===============================================================


class RecentCard(QFrame):
    """Compact history card with music icon."""

    clicked = Signal(dict)

    def __init__(
        self, title: str = "--", score_str: str = "", date: str = "", data: dict | None = None
    ):
        super().__init__()
        self._data = data or {}
        self.setObjectName("Card")
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumWidth(200)
        self.setFixedHeight(88)
        self.setGraphicsEffect(_shadow(Elevation.LOW, 30))
        self.setStyleSheet(f"""
            QFrame#Card {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
            QFrame#Card:hover {{
                background: {Color.HOVER};
                border-color: {Color.ACCENT};
            }}
        """)
        l = QVBoxLayout(self)
        l.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        l.setSpacing(Space.XS)

        hdr = QHBoxLayout()
        hdr.setSpacing(Space.SM)
        icon = IconWidget("music", size=14, color=Color.MUTED)
        hdr.addWidget(icon)

        t = QLabel(title)
        t.setStyleSheet(
            f"font-weight: {Type.WEIGHTS['semibold']}; font-size: {Type.BODY + 1}px; "
            f"color: {Color.WHITE}; background: transparent; border: none;"
        )
        hdr.addWidget(t, 1)
        l.addLayout(hdr)

        s = QLabel()
        s.setStyleSheet(
            f"color: {Color.MUTED}; font-size: {Type.CAPTION}px; background: transparent; border: none; padding-left: 20px;"
        )
        display = score_str
        if date:
            display += f"  {date}" if display else date
        s.setText(display)
        l.addWidget(s)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit(self._data)
        super().mousePressEvent(event)


# ===============================================================
# Reference Track Card
# ===============================================================


class ReferenceTrackCard(QFrame):
    """Track A/B card for Reference Match."""

    def __init__(self, label: str, name: str = "--", info: str = ""):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.LG}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(Space.SM)

        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 0.5px;"
        )
        lay.addWidget(lbl)

        icon = IconWidget("music", size=24, color=Color.ACCENT)
        lay.addWidget(icon, 0, Qt.AlignCenter)

        self.name = QLabel(name)
        self.name.setAlignment(Qt.AlignCenter)
        self.name.setWordWrap(True)
        self.name.setStyleSheet(
            f"font-size: {Type.BODY}px; font-weight: {Type.WEIGHTS['medium']}; "
            f"color: {Color.TEXT}; background: transparent; border: none;"
        )
        lay.addWidget(self.name)

        self.info = QLabel(info)
        self.info.setAlignment(Qt.AlignCenter)
        self.info.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; color: {Color.MUTED}; background: transparent; border: none;"
        )
        lay.addWidget(self.info)

    def set_track(self, name: str, info: str = ""):
        self.name.setText(name[:32] if name else "--")
        self.info.setText(info or "")


# ===============================================================
# Diff Card
# ===============================================================


class DiffCard(QFrame):
    """Metric difference card for Reference Match."""

    def __init__(
        self,
        metric: str,
        delta: float | None,
        user_value: Any,
        ref_value: Any,
        severity: str = "pass",
    ):
        super().__init__()
        good = severity in ("pass", "notice")
        delta_color = Color.SUCCESS if good else Color.WARNING
        icon_name = "check" if good else "alert"

        self.setGraphicsEffect(_shadow(Elevation.LOW, 25))
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.SURFACE};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.LG}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        lay.setSpacing(Space.XS)

        hdr = QHBoxLayout()
        hdr.setSpacing(Space.SM)

        icon = IconWidget(icon_name, size=14, color=delta_color)
        hdr.addWidget(icon)

        lbl = QLabel(metric)
        lbl.setStyleSheet(
            f"font-size: {Type.BODY}px; font-weight: {Type.WEIGHTS['medium']}; color: {Color.TEXT}; background: transparent; border: none;"
        )
        hdr.addWidget(lbl, 1)

        if delta is not None:
            sign = "+" if delta > 0 else ""
            d_lbl = QLabel(f"{sign}{delta:.2f}")
            d_lbl.setStyleSheet(
                f"font-size: {Type.BODY + 1}px; font-weight: {Type.WEIGHTS['bold']}; "
                f"color: {delta_color}; background: transparent; border: none;"
            )
            hdr.addWidget(d_lbl)
        lay.addLayout(hdr)

        vals = QLabel(f"You: {user_value}  |  Ref: {ref_value}")
        vals.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent; border: none; padding-left: 22px;"
        )
        lay.addWidget(vals)


# ===============================================================
# Platform Row
# ===============================================================


class PlatformRow(QFrame):
    """Streaming platform readiness with status icon."""

    def __init__(self, platform: str, status: str):
        super().__init__()
        ready = status == "ready"
        c = Color.SUCCESS if ready else Color.WARNING
        icon_name = "check" if ready else "alert"

        self.setStyleSheet("background: transparent; border: none;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        lay.setSpacing(Space.SM)

        icon = IconWidget(icon_name, size=12, color=c)
        lay.addWidget(icon)

        lbl = QLabel(platform)
        lbl.setStyleSheet(
            f"font-size: {Type.BODY}px; color: {Color.TEXT}; background: transparent; border: none;"
        )
        lay.addWidget(lbl, 1)

        st = QLabel("Ready" if ready else "Adjustment Recommended")
        st.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {c}; background: transparent; border: none;"
        )
        lay.addWidget(st)


# ===============================================================
# Circular Score Ring
# ===============================================================


class CircularScoreRing(QWidget):
    """
    Premium circular mix-score meter.

    One solid colored progress arc over a quiet track (no dual bars / tick rails).
    Larger glass center disc (+15%) with centered score typography.
    """

    def __init__(self, size: int = 240):
        super().__init__()
        self._score: int | None = None
        self._display_score: float = 0.0
        self._ring_size = size
        self._glow_phase: float = 0.0
        # Wider than the ring so rating text under the score is never clipped
        self._outer_w = max(size + 16, 212)
        self._outer_h = size + 58
        self.setFixedSize(self._outer_w, self._outer_h)
        self.setMinimumSize(self._outer_w, self._outer_h)
        self.setStyleSheet("background: transparent; border: none;")

        self._rating_label = QLabel(self)
        self._rating_label.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._rating_label.setWordWrap(True)
        self._rating_label.setGeometry(4, size + 6, self._outer_w - 8, 48)
        self._rating_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 0.5px;"
        )

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)

        self._glow_timer = QTimer(self)
        self._glow_timer.timeout.connect(self._pulse_glow)
        self._glow_timer.start(48)

    def set_score(self, score: int | None, rating: str = ""):
        self._score = score
        self._display_score = 0.0
        self._rating_label.setText(
            (rating or score_rating(score)).upper() if score is not None else ""
        )
        # Color the rating with the score band
        if score is not None:
            c = score_color(int(score))
            self._rating_label.setStyleSheet(
                f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
                f"color: {c}; background: transparent; border: none; letter-spacing: 0.5px;"
            )
        else:
            self._rating_label.setStyleSheet(
                f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
                f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 0.5px;"
            )
        self._anim.start(16)
        self.update()

    def _tick(self):
        if self._score is None:
            self._anim.stop()
            self._display_score = 0.0
            self.update()
            return
        target = float(self._score)
        # Smooth ease toward target (no jumpy integer steps)
        delta = target - self._display_score
        if abs(delta) < 0.35:
            self._display_score = target
            self._anim.stop()
        else:
            self._display_score += delta * 0.18
        self.update()

    def _pulse_glow(self):
        self._glow_phase += 0.035
        if self._score is not None:
            self.update()

    def paintEvent(self, event):
        import math

        from PySide6.QtGui import (
            QColor as QCol,
            QFont,
            QPainter,
            QPen,
            QRadialGradient,
        )

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        cx = self.width() // 2
        cy = self._ring_size // 2
        # Arc radius leaves room for a soft outer glow without second "bar"
        r = (self._ring_size // 2) - 20
        has_score = self._score is not None
        score = int(round(self._display_score)) if has_score else 0
        color = QCol(score_color(score)) if has_score else QCol(Color.MUTED)
        stroke = 11.0

        # Soft ambient halo behind the whole meter (not a second progress bar)
        if has_score and score > 0:
            glow_amp = 0.45 + 0.25 * math.sin(self._glow_phase)
            halo = QRadialGradient(cx, cy, r + 28)
            hc = QCol(color)
            hc.setAlpha(int(28 * glow_amp))
            halo.setColorAt(0.55, QCol(0, 0, 0, 0))
            halo.setColorAt(0.78, hc)
            halo.setColorAt(1.0, QCol(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(cx - r - 28, cy - r - 28, (r + 28) * 2, (r + 28) * 2)

        # Quiet track (single underlay - not a parallel colored bar)
        track = QCol(Color.LINE)
        track.setAlpha(200)
        track_pen = QPen(track, stroke)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(track_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # Full circle track (360°) for a modern gauge look
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 0, 360 * 16)

        # ONE solid progress arc only (no thick glow stroke beside it)
        if has_score and score > 0:
            span = int(360 * 16 * min(100, max(0, score)) / 100.0)
            # Start at 12 o'clock, clockwise
            start_angle = 90 * 16
            # Soft bloom under the solid arc only (same path, higher alpha core)
            bloom = QCol(color.red(), color.green(), color.blue(), 55)
            bloom_pen = QPen(bloom, stroke + 6)
            bloom_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(bloom_pen)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, start_angle, -span)

            main_pen = QPen(color, stroke)
            main_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(main_pen)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, start_angle, -span)

            # End cap highlight (premium bead)
            ang = math.radians(90 - (360.0 * score / 100.0))
            ex = cx + r * math.cos(ang)
            ey = cy - r * math.sin(ang)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QCol(Color.WHITE))
            p.drawEllipse(QPointF(ex, ey), 3.2, 3.2)
            p.setBrush(color)
            p.drawEllipse(QPointF(ex, ey), 2.2, 2.2)

        # Glass center disc — previous radius * 1.15
        elev = QCol(Color.ELEVATED)
        surf = QCol(Color.SURFACE)
        bg = QCol(Color.BG)
        center_r = int(r * 0.65 * 1.15)  # +15%
        # Keep disc inside the track stroke
        max_inner = int(r - stroke * 0.55 - 2)
        center_r = min(center_r, max_inner)

        # Disc shadow
        p.setPen(Qt.PenStyle.NoPen)
        shadow = QRadialGradient(cx, cy + 2, center_r * 1.15)
        shadow.setColorAt(0.7, QCol(0, 0, 0, 0))
        shadow.setColorAt(1.0, QCol(0, 0, 0, 70))
        p.setBrush(shadow)
        p.drawEllipse(cx - center_r - 2, cy - center_r, (center_r + 2) * 2, (center_r + 2) * 2)

        center_gradient = QRadialGradient(cx - center_r * 0.15, cy - center_r * 0.2, center_r)
        center_gradient.setColorAt(0.0, QCol(elev.red(), elev.green(), elev.blue(), 255))
        center_gradient.setColorAt(0.55, QCol(surf.red(), surf.green(), surf.blue(), 250))
        center_gradient.setColorAt(1.0, QCol(bg.red(), bg.green(), bg.blue(), 255))
        p.setBrush(center_gradient)
        # Thin rim
        rim = QCol(Color.LINE_HOVER if has_score else Color.LINE)
        p.setPen(QPen(rim, 1.25))
        p.drawEllipse(cx - center_r, cy - center_r, center_r * 2, center_r * 2)

        # Inner highlight ring on the disc
        p.setPen(QPen(QCol(255, 255, 255, 22), 1.0))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(cx - center_r + 3, cy - center_r + 3, (center_r - 3) * 2, (center_r - 3) * 2)

        # Score number — fully centered on disc
        score_str = str(score) if has_score else "—"
        pt = max(28, int(center_r * 0.72))
        try:
            from nodaw.ui.theme import pick_display_font

            font = pick_display_font(pt, QFont.Weight.Bold)
        except Exception:
            font = QFont("Bahnschrift", pt, Type.WEIGHTS["bold"])
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -1.2)
        p.setFont(font)
        p.setPen(color if has_score else QCol(Color.MUTED))
        # Number block slightly above optical center so "MIX SCORE" fits under
        num_h = int(center_r * 0.95)
        p.drawText(
            cx - center_r,
            cy - int(num_h * 0.55),
            center_r * 2,
            num_h,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            score_str,
        )

        # Small label under the number, still on the disc
        font2 = QFont()
        face = Type.FAMILY.split(",")[0].strip().strip('"')
        if face:
            font2.setFamily(face)
        font2.setPointSizeF(max(7.5, center_r * 0.14))
        font2.setWeight(QFont.Weight.DemiBold)
        font2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.1)
        p.setFont(font2)
        p.setPen(QCol(Color.MUTED))
        p.drawText(
            cx - center_r,
            cy + int(center_r * 0.22),
            center_r * 2,
            int(center_r * 0.35),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "MIX SCORE",
        )

        p.end()


# ===============================================================
# Metric Tile (for bottom metrics bar)
# ===============================================================


class MetricTile(QFrame):
    """Compact metric display: label on top, large value below, optional delta."""

    def __init__(self, label: str, value: str, delta: str | None = None):
        super().__init__()
        self.setStyleSheet("background: transparent; border: none;")
        self.setMinimumWidth(100)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        lay.setSpacing(2)
        lay.setAlignment(Qt.AlignCenter)

        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 0.3px;"
        )
        lay.addWidget(lbl)

        self._value = QLabel(value)
        self._value.setAlignment(Qt.AlignCenter)
        self._value.setStyleSheet(
            f"font-size: {Type.H2}px; font-weight: {Type.WEIGHTS['bold']}; "
            f"color: {Color.TEXT}; background: transparent; border: none;"
        )
        lay.addWidget(self._value)

        if delta:
            d = QLabel(delta)
            d.setAlignment(Qt.AlignCenter)
            d.setStyleSheet(
                f"font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['medium']}; "
                f"color: {Color.MUTED}; background: transparent; border: none;"
            )
            lay.addWidget(d)

    def set_value(self, value: str):
        self._value.setText(value)


# ===============================================================
# Bottom Metrics Bar
# ===============================================================


class BottomMetricsBar(QFrame):
    """Horizontal bar showing key analysis metrics."""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.GLASS};
                border: 1px solid {Color.GLASS_BORDER};
                border-radius: {Radius.XL}px;
            }}
        """)
        self.setGraphicsEffect(_shadow(Elevation.LOW, 35))
        lay = QHBoxLayout(self)
        lay.setContentsMargins(Space.XS, Space.XS, Space.XS, Space.XS)
        lay.setSpacing(0)

        self._tiles: dict[str, MetricTile] = {}
        metrics = [
            ("Loudness", "LUFS", "--"),
            ("True Peak", "dBTP", "--"),
            ("Loudness Range", "LU", "--"),
            ("Peak", "dBFS", "--"),
            ("RMS", "dBFS", "--"),
            ("Crest", "dB", "--"),
        ]
        for i, (label, unit, default) in enumerate(metrics):
            if i > 0:
                sep = QFrame()
                sep.setFixedWidth(1)
                sep.setStyleSheet(f"background: {Color.LINE}; border: none;")
                lay.addWidget(sep)
            tile = MetricTile(label, f"{default} {unit}" if unit else default)
            self._tiles[label] = tile
            lay.addWidget(tile, 1)

    def update_metrics(self, track: dict | None):
        if not track:
            return
        m = track.get("metrics", {}) or {}
        lm = m.get("loudness", {}) or {}

        def _f(v, unit, digits=1):
            if v is None:
                return f"-- {unit}".strip()
            try:
                return f"{float(v):.{digits}f} {unit}".strip()
            except (TypeError, ValueError):
                return f"{v} {unit}".strip()

        self._tiles["Loudness"].set_value(_f(lm.get("integrated_lufs"), "LUFS"))
        self._tiles["True Peak"].set_value(_f(lm.get("true_peak_dbtp"), "dBTP"))
        self._tiles["Loudness Range"].set_value(_f(lm.get("loudness_range_lu"), "LU"))
        self._tiles["Peak"].set_value(_f(m.get("peak_dbfs"), "dBFS"))
        self._tiles["RMS"].set_value(_f(m.get("rms_dbfs"), "dBFS"))
        self._tiles["Crest"].set_value(_f(m.get("crest_factor"), ""))


# ===============================================================
# Waveform Panel (placeholder)
# ===============================================================


class WaveformPanel(QFrame):
    """Waveform visualization panel -- render area for future SVG/canvas waveform."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(100)
        self.setGraphicsEffect(_shadow(Elevation.MED, 35))
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.GLASS};
                border: 1px solid {Color.GLASS_BORDER};
                border-radius: {Radius.XL}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        lay.setSpacing(Space.XS)

        hdr = QLabel("WAVEFORM")
        hdr.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 1.5px;"
        )
        lay.addWidget(hdr)

        self._canvas = QFrame()
        self._canvas.setMinimumHeight(48)
        self._canvas.setStyleSheet(
            f"background: {Color.BG}; border-radius: {Radius.SM}px; border: 1px solid {Color.LINE};"
        )
        lay.addWidget(self._canvas)


# ===============================================================
# Spectrum Panel (placeholder)
# ===============================================================


class SpectrumPanel(QFrame):
    """Frequency spectrum visualization panel."""

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(80)
        self.setGraphicsEffect(_shadow(Elevation.MED, 35))
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.GLASS};
                border: 1px solid {Color.GLASS_BORDER};
                border-radius: {Radius.XL}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        lay.setSpacing(Space.XS)

        hdr = QLabel("SPECTRUM")
        hdr.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 1.5px;"
        )
        lay.addWidget(hdr)

        self._canvas = QFrame()
        self._canvas.setMinimumHeight(36)
        self._canvas.setStyleSheet(
            f"background: {Color.BG}; border-radius: {Radius.SM}px; border: 1px solid {Color.LINE};"
        )
        lay.addWidget(self._canvas)


# ===============================================================
# Export Card
# ===============================================================


class ExportCard(QFrame):
    """Report export buttons: HTML / JSON / TXT.

    Designed to stretch vertically so its top/bottom can line up with the
    recent-tracks block beside it on the home screen.
    """

    exportRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setGraphicsEffect(_shadow(Elevation.LOW, 30))
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        self.setMinimumHeight(88 * 2 + Space.MD)  # match 2× RecentCard rows
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.GLASS};
                border: 1px solid {Color.GLASS_BORDER};
                border-radius: {Radius.XL}px;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        lay.setSpacing(Space.SM)

        hdr = QLabel("EXPORT REPORT")
        hdr.setStyleSheet(
            f"font-size: {Type.CAPTION - 1}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 1.5px;"
        )
        lay.addWidget(hdr)
        lay.addSpacing(2)

        for fmt, icon_name in [("HTML", "external"), ("JSON", "download"), ("TXT", "report")]:
            btn = QPushButton(f"  {fmt}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: 1px solid {Color.LINE};
                    border-radius: {Radius.MD}px; padding: {Space.SM}px {Space.MD}px;
                    font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS["medium"]}; color: {Color.TEXT};
                    text-align: left;
                }}
                QPushButton:hover {{ background: {Color.HOVER}; border-color: {Color.ACCENT}; color: {Color.ACCENT}; }}
            """)
            btn.clicked.connect(lambda checked, f=fmt.lower(): self.exportRequested.emit(f))
            lay.addWidget(btn)

        # Absorb extra height when stretched beside the recent tracks block
        lay.addStretch(1)


# Backward compatibility alias
MetricCard = MetricRow
