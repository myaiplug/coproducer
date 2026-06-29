# -*- coding: utf-8 -*-
"""
CoProducer Design System -- Reusable UI Components.

Every widget uses:
    - theme tokens from `theme.py` (no hardcoded values)
    - SVG icons from `icons.py` (no unicode glyphs, no emoji)
    - 8-point spacing grid
    - Inter font family
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy,
    QVBoxLayout, QWidget, QGraphicsOpacityEffect,
)

from .theme import (
    Color, Type, Space, Radius, Duration, Easing, Elevation,
    score_color, score_rating,
)
from .icons import IconWidget


# -- Helpers ----------------------------------------------------=

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
    """Reusable card surface with elevation, border, and optional hover."""
    clicked = Signal()

    def __init__(self, card_type: str = "elevated", hoverable: bool = False,
                 clickable: bool = False, elevation: int = Elevation.LOW):
        super().__init__()
        self._card_type = card_type
        self._hoverable = hoverable
        self._clickable = clickable
        self.setGraphicsEffect(_shadow(elevation, 45))
        self.setStyleSheet(self._build_style())
        if clickable:
            self.setCursor(Qt.PointingHandCursor)

    def _build_style(self) -> str:
        bg = _card_bg(self._card_type)
        border = _card_border(self._card_type)
        hover_bg = Color.HOVER
        hover_border = Color.ACCENT if self._hoverable else border
        base = f"""
            QFrame {{
                background: {bg};
                border: 1px solid {border};
                border-radius: {Radius.CARD}px;
            }}
        """
        if self._hoverable:
            base += f"""
                QFrame:hover {{
                    background: {hover_bg};
                    border-color: {hover_border};
                }}
            """
        return base

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
        lbl.setStyleSheet(f"font-size: {Type.BODY}px; color: {Color.MUTED}; background: transparent; border: none;")
        lay.addWidget(lbl)
        lay.addStretch()

        if delta:
            d_lbl = QLabel(delta)
            d_color = Color.SUCCESS if good else Color.WARNING
            d_lbl.setStyleSheet(f"font-size: {Type.BODY}px; font-weight: {Type.WEIGHTS['semibold']}; color: {d_color}; background: transparent; border: none;")
            lay.addWidget(d_lbl)

        val = QLabel(value)
        val.setStyleSheet(f"font-size: {Type.BODY}px; font-weight: {Type.WEIGHTS['medium']}; color: {Color.TEXT}; background: transparent; border: none;")
        lay.addWidget(val)


# ===============================================================
# Drop Zone
# ===============================================================

class DropZone(QFrame):
    """Drag & drop target with SVG icon and hover animation."""
    filesDropped = Signal(list)

    def __init__(self, title: str = "Drop your mix here", subtitle: str = "WAV  MP3  FLAC  M4A  AIFF"):
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(240)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(self._idle_style())

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(Space.SM)

        self._icon = IconWidget("plus", size=40, color=Color.MUTED)
        layout.addWidget(self._icon, 0, Qt.AlignCenter)

        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet(f"font-size: {Type.TITLE}px; font-weight: {Type.WEIGHTS['semibold']}; color: {Color.TEXT}; background: transparent; border: none;")

        self.subtitle = QLabel(subtitle)
        self.subtitle.setAlignment(Qt.AlignCenter)
        self.subtitle.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent; border: none;")

        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse)
        self._pulse_dir = 1
        self._pulse_step = 0
        self._hovering = False
        self.mousePressEvent = lambda e: self.filesDropped.emit([])

    def _idle_style(self) -> str:
        return f"""
            QFrame {{
                background: {Color.GLASS};
                border: 1.5px solid {Color.GLASS_BORDER};
                border-radius: {Radius.DROP_ZONE}px;
            }}
            QFrame:hover {{
                background: {Color.HOVER};
                border-color: {Color.ACCENT};
            }}
        """

    def _active_style(self) -> str:
        return f"""
            QFrame {{
                background: {Color.HOVER};
                border: 1.5px solid {Color.ACCENT};
                border-radius: {Radius.DROP_ZONE}px;
            }}
        """

    def enterEvent(self, event):
        self._hovering = True
        self._pulse_step = 0
        self._pulse_timer.start(30)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovering = False
        self._pulse_timer.stop()
        self._icon.set_color(Color.MUTED)
        super().leaveEvent(event)

    def _pulse(self):
        self._pulse_step += self._pulse_dir
        if self._pulse_step > 6 or self._pulse_step < 0:
            self._pulse_dir *= -1
            self._pulse_step += self._pulse_dir
        if not self._hovering:
            return
        alpha = min(120 + self._pulse_step * 15, 255)
        c = QColor(Color.ACCENT)
        c.setAlpha(alpha)
        self._icon.set_color(c.name())

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(self._active_style())

    def dragLeaveEvent(self, event):
        self.setStyleSheet(self._idle_style())

    def dropEvent(self, event: QDropEvent):
        urls = [u.toLocalFile() for u in event.mimeData().urls()]
        self.filesDropped.emit(urls)
        self.setStyleSheet(self._idle_style())


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
        self._badge.setStyleSheet(f"background: {Color.with_alpha(Color.MUTED, 0.10)}; border-radius: {Radius.PILL}px; border: none;")
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
            self._badge.setStyleSheet(f"background: {Color.with_alpha(Color.MUTED, 0.10)}; border-radius: {Radius.PILL}px; border: none;")
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
        self._badge.setStyleSheet(f"background: {Color.with_alpha(c, 0.12)}; border-radius: {Radius.PILL}px; border: none;")
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
        self._empty.setStyleSheet(f"color: {Color.MUTED}; font-size: {Type.BODY}px; background: transparent; border: none;")
        self.lay.addWidget(self._empty)

    def _rebuild_header(self):
        hdr = QHBoxLayout()
        hdr.setSpacing(Space.SM)
        hi = IconWidget("sparkle", size=16, color=Color.MUTED)
        hdr.addWidget(hi)
        hl = QLabel("Recommended Actions")
        hl.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; letter-spacing: 0.5px; background: transparent; border: none;"
        )
        hdr.addWidget(hl)
        hdr.addStretch()
        self.lay.addLayout(hdr)

    def _clear(self):
        for i in reversed(range(self.lay.count())):
            w = self.lay.itemAt(i).widget()
            if w:
                w.deleteLater()

    def set_items(self, items: list[str] | None):
        self._clear()
        if not items:
            self.lay.addWidget(self._empty)
            return
        self._rebuild_header()
        for rec in items[:6]:
            row = QHBoxLayout()
            row.setSpacing(Space.SM)
            icon = IconWidget("check", size=14, color=Color.ACCENT)
            row.addWidget(icon)
            lbl = QLabel(rec)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(
                f"color: {Color.TEXT}; font-size: {Type.BODY - 1}px; "
                f"line-height: 1.4; background: transparent; border: none;"
            )
            row.addWidget(lbl, 1)
            self.lay.addLayout(row)
        self.lay.addStretch()

    @staticmethod
    def _friendly_filter_desc(filter_chain: str) -> list[str]:
        """Turn an ffmpeg filter chain into user-friendly benefit bullets."""
        bullets = []
        if "loudnorm" in filter_chain:
            bullets.append("Normalize loudness to streaming targets (Spotify, Apple Music, YouTube)")
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
        """Display repair recommendations with friendly summary and hidden advanced details."""
        self._clear()
        self._rebuild_header()

        if not repairs:
            self.lay.addWidget(self._empty)
            return

        for rep in repairs[:4]:
            title = rep.get("title", "Repair Mix")
            command = rep.get("command", "")
            filter_chain = rep.get("ffmpeg_filter", "")
            caution = rep.get("caution", "")
            benefits = self._friendly_filter_desc(filter_chain)

            card = QFrame()
            card.setStyleSheet(f"background: {Color.ELEVATED}; border: 1px solid {Color.LINE}; border-radius: {Radius.MD}px;")
            cl = QVBoxLayout(card)
            cl.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.SM)
            cl.setSpacing(Space.XS)

            # Title
            tl = QLabel(f"<b>{title}</b>")
            tl.setStyleSheet(f"font-size: {Type.BODY}px; color: {Color.TEXT}; background: transparent; border: none;")
            cl.addWidget(tl)

            # Subtitle
            sub = QLabel("This repair will:")
            sub.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent; border: none; padding-top: {Space.XS}px;")
            cl.addWidget(sub)

            # Benefit bullets
            for b in benefits:
                row = QHBoxLayout()
                row.setSpacing(Space.SM)
                icon = IconWidget("check", size=10, color=Color.SUCCESS)
                row.addWidget(icon)
                bl = QLabel(b)
                bl.setWordWrap(True)
                bl.setStyleSheet(f"font-size: {Type.CAPTION - 1}px; color: {Color.TEXT}; background: transparent; border: none;")
                row.addWidget(bl, 1)
                cl.addLayout(row)

            # Estimated time
            et = QLabel("Estimated processing time: ~2 seconds")
            et.setStyleSheet(f"font-size: {Type.CAPTION - 1}px; color: {Color.MUTED}; background: transparent; border: none; padding-top: {Space.XS}px;")
            cl.addWidget(et)

            # Caution
            if caution:
                clbl = QLabel(caution)
                clbl.setWordWrap(True)
                clbl.setStyleSheet(f"font-size: {Type.CAPTION - 1}px; color: {Color.WARNING}; background: transparent; border: none;")
                cl.addWidget(clbl)

            # Repair Mix button
            btn_row = QHBoxLayout()
            btn_row.setSpacing(Space.SM)
            btn_row.addStretch()
            run_btn = QPushButton("Repair Mix")
            run_btn.setFixedHeight(32)
            run_btn.setCursor(Qt.PointingHandCursor)
            run_btn.setStyleSheet(f"""
                QPushButton {{
                    background: {Color.ACCENT}; color: white; border: none;
                    border-radius: {Radius.MD}px; padding: 0 {Space.XL}px;
                    font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['bold']};
                }}
                QPushButton:hover {{ background: {Color.with_alpha(Color.ACCENT, 0.8)}; }}
                QPushButton:pressed {{ background: {Color.ACCENT}; }}
            """)
            run_btn.clicked.connect(lambda checked, c=command: self.repairClicked.emit(c))
            btn_row.addWidget(run_btn)
            cl.addLayout(btn_row)

            # Advanced collapse (hidden by default)
            adv_btn = QPushButton("[+] Advanced")
            adv_btn.setCursor(Qt.PointingHandCursor)
            adv_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: none; text-align: left;
                    font-size: {Type.CAPTION - 1}px; color: {Color.MUTED};
                    padding: {Space.XS}px 0 {Space.XS}px 0;
                }}
                QPushButton:hover {{ color: {Color.ACCENT}; }}
            """)
            adv_btn.setIconSize(adv_btn.sizeHint())

            adv_content = QFrame()
            adv_content.setStyleSheet("background: transparent; border: none;")
            adv_content.hide()
            ac_lay = QVBoxLayout(adv_content)
            ac_lay.setContentsMargins(0, 0, 0, 0)
            ac_lay.setSpacing(Space.XS)

            # Filter chain
            fl = QLabel(f"Filter chain:  {filter_chain}" if filter_chain else "Filter chain:  anull (passthrough)")
            fl.setWordWrap(True)
            fl.setStyleSheet(
                f"font-size: {Type.CAPTION - 1}px; font-family: {Type.MONO}; "
                f"color: {Color.ACCENT}; background: {Color.with_alpha(Color.ACCENT, 0.06)}; "
                f"padding: {Space.XS}px {Space.SM}px; border-radius: {Radius.SM}px; border: none;"
            )
            ac_lay.addWidget(fl)

            # Full command
            clbl = QLabel("CLI command:")
            clbl.setStyleSheet(f"font-size: {Type.CAPTION - 1}px; color: {Color.MUTED}; background: transparent; border: none;")
            ac_lay.addWidget(clbl)

            cmd_lbl = QLabel(command)
            cmd_lbl.setWordWrap(True)
            cmd_lbl.setStyleSheet(
                f"font-size: {Type.CAPTION - 1}px; font-family: {Type.MONO}; "
                f"color: {Color.TEXT}; background: {Color.with_alpha(Color.ACCENT, 0.06)}; "
                f"padding: {Space.XS}px {Space.SM}px; border-radius: {Radius.SM}px; border: none;"
            )
            ac_lay.addWidget(cmd_lbl)

            # Copy button
            copy_btn = QPushButton("Copy Command")
            copy_btn.setFixedHeight(24)
            copy_btn.setCursor(Qt.PointingHandCursor)
            copy_btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: 1px solid {Color.LINE};
                    border-radius: {Radius.SM}px; padding: 0 {Space.MD}px;
                    font-size: {Type.CAPTION - 1}px; color: {Color.TEXT};
                }}
                QPushButton:hover {{ background: {Color.HOVER}; border-color: {Color.ACCENT}; }}
            """)
            copy_btn.clicked.connect(lambda: QApplication.clipboard().setText(command))
            ac_lay.addWidget(copy_btn)

            adv_btn.clicked.connect(lambda checked, a=adv_content, b=adv_btn: (
                a.setVisible(not a.isVisible()),
                b.setText("[+] Advanced" if not a.isVisible() else "[-] Advanced")
            ))

            cl.addWidget(adv_btn)
            cl.addWidget(adv_content)

            self.lay.addWidget(card)

        self.lay.addStretch()


# ===============================================================
# Verdict Badge
# ===============================================================

class VerdictBadge(QFrame):
    """Overall verdict bar with status dot and score pill."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(48)
        self.setStyleSheet(f"background: {Color.ELEVATED}; border-radius: {Radius.XL}px; border: 1px solid {Color.LINE};")
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
        self._pill.setStyleSheet(f"background: {Color.with_alpha(Color.MUTED, 0.12)}; border-radius: {Radius.PILL}px; border: none;")
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
        self._pill.setStyleSheet(f"background: {Color.with_alpha(c, 0.18)}; border-radius: {Radius.PILL}px; border: none;")
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
                font-weight: {Type.WEIGHTS['semibold']}; color: {Color.TEXT};
            }}
            QPushButton:hover {{ color: {Color.ACCENT}; }}
        """)
        btn.clicked.connect(self._toggle)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(Space.LG, Space.LG, Space.XL, Space.LG)
        btn_layout.setSpacing(Space.SM)

        self._chevron = IconWidget(
            "chevron_down" if expanded else "chevron_right",
            size=16, color=Color.MUTED
        )
        btn_layout.addWidget(self._chevron)

        self._title = QLabel(title)
        self._title.setStyleSheet("font-size: 14px; font-weight: 600; color: white; background: transparent; border: none;")
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
        sev_map = {"pass": Color.SUCCESS, "critical": Color.ERROR,
                    "warning": Color.WARNING, "notice": Color.MUTED}
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
        t.setStyleSheet(f"font-size: {Type.BODY}px; color: {Color.TEXT}; background: transparent; border: none;")
        hdr.addWidget(t, 1)
        lay.addLayout(hdr)

        m = QLabel(message)
        m.setWordWrap(True)
        m.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent; border: none;")
        lay.addWidget(m)

        if action:
            a = QLabel(f"<b>Action:</b> {action}")
            a.setWordWrap(True)
            a.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.TEXT}; background: transparent; border: none;")
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
        sub.setStyleSheet(f"font-size: {Type.BODY}px; color: {Color.MUTED}; background: transparent; border: none;")
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

    def __init__(self, title: str = "--", score_str: str = "", date: str = "", data: dict | None = None):
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
        s.setStyleSheet(f"color: {Color.MUTED}; font-size: {Type.CAPTION}px; background: transparent; border: none; padding-left: 20px;")
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
        self.info.setStyleSheet(f"font-size: {Type.CAPTION - 1}px; color: {Color.MUTED}; background: transparent; border: none;")
        lay.addWidget(self.info)

    def set_track(self, name: str, info: str = ""):
        self.name.setText(name[:32] if name else "--")
        self.info.setText(info or "")


# ===============================================================
# Diff Card
# ===============================================================

class DiffCard(QFrame):
    """Metric difference card for Reference Match."""

    def __init__(self, metric: str, delta: float | None, user_value: Any,
                 ref_value: Any, severity: str = "pass"):
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
        lbl.setStyleSheet(f"font-size: {Type.BODY}px; font-weight: {Type.WEIGHTS['medium']}; color: {Color.TEXT}; background: transparent; border: none;")
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
        vals.setStyleSheet(f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent; border: none; padding-left: 22px;")
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
        lbl.setStyleSheet(f"font-size: {Type.BODY}px; color: {Color.TEXT}; background: transparent; border: none;")
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
    """Premium circular score gauge with animated arc, glow, and glass center."""

    def __init__(self, size: int = 240):
        super().__init__()
        self._score: int | None = None
        self._display_score: int = 0
        self._ring_size = size
        self._glow_phase: float = 0.0
        self.setFixedSize(size, size + 44)
        self.setStyleSheet("background: transparent; border: none;")

        self._rating_label = QLabel(self)
        self._rating_label.setAlignment(Qt.AlignCenter)
        self._rating_label.setGeometry(0, size - 6, size, 28)
        self._rating_label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"color: {Color.MUTED}; background: transparent; border: none; letter-spacing: 1.5px;"
        )

        self._anim = QTimer(self)
        self._anim.timeout.connect(self._tick)

        # Glow pulse timer
        self._glow_timer = QTimer(self)
        self._glow_timer.timeout.connect(self._pulse_glow)
        self._glow_timer.start(50)

    def set_score(self, score: int | None, rating: str = ""):
        self._score = score
        self._display_score = 0
        self._rating_label.setText((rating or score_rating(score)).upper() if score is not None else "")
        self._anim.start(20)
        self.update()

    def _tick(self):
        if self._score is None:
            self._anim.stop()
            return
        step = max(1, (self._score - self._display_score) // 5)
        self._display_score = min(self._display_score + step, self._score)
        self.update()
        if self._display_score >= self._score:
            self._anim.stop()

    def _pulse_glow(self):
        self._glow_phase += 0.04
        self.update()

    def paintEvent(self, event):
        from PySide6.QtGui import QPainter, QFont, QPen, QColor as QCol, QLinearGradient, QRadialGradient
        import math

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        cx = self._ring_size // 2
        cy = self._ring_size // 2
        r = (self._ring_size // 2) - 18
        has_score = self._display_score > 0
        score = self._display_score if has_score else 0
        color = QCol(score_color(self._display_score)) if has_score else QCol(Color.MUTED)

        # Glow amplitude (pulsing)
        glow_amp = 0.5 + 0.5 * math.sin(self._glow_phase)

        # Outer ring background (dark glass)
        pen = QPen(QCol(Color.LINE), 10)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(cx - r, cy - r, r * 2, r * 2, 1440, 2880)

        # Progress arc with glow
        if has_score:
            span = int(2880 * (score / 100))
            start_angle = 1440

            # Glow layer (larger, blurred, transparent)
            glow_pen = QPen(QCol(color.red(), color.green(), color.blue(), int(60 * glow_amp)), 16)
            glow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(glow_pen)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, start_angle, span)

            # Main arc
            main_pen = QPen(color, 10)
            main_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(main_pen)
            p.drawArc(cx - r, cy - r, r * 2, r * 2, start_angle, span)

        # Segmented tick marks around the ring
        tick_pen = QPen(QCol(Color.LINE_HOVER), 1)
        tick_r = r + 14
        for i in range(24):
            angle = 1440 + i * (2880 // 24)
            tick_len = 4 if i % 6 == 0 else 2
            inner_tick = tick_r - tick_len
            x1 = cx + inner_tick * math.cos(math.radians(angle / 16))
            y1 = cy + inner_tick * math.sin(math.radians(angle / 16))
            x2 = cx + tick_r * math.cos(math.radians(angle / 16))
            y2 = cy + tick_r * math.sin(math.radians(angle / 16))
            p.drawLine(int(x1), int(y1), int(x2), int(y2))

        # Glass center circle
        center_gradient = QRadialGradient(cx, cy, r * 0.35)
        center_gradient.setColorAt(0, QCol(20, 25, 35, 200))
        center_gradient.setColorAt(1, QCol(15, 18, 26, 240))
        p.setBrush(center_gradient)
        p.setPen(QPen(QCol(Color.LINE), 1))
        center_r = int(r * 0.65)
        p.drawEllipse(cx - center_r, cy - center_r, center_r * 2, center_r * 2)

        # Score number
        score_str = str(score) if has_score else "--"
        font = QFont(Type.FAMILY.split(",")[0].strip('"'), 44, Type.WEIGHTS["bold"])
        p.setFont(font)
        p.setPen(color if has_score else QCol(Color.MUTED))
        p.drawText(0, cy - 32, self._ring_size, 50, Qt.AlignCenter, score_str)

        # "MIX SCORE" label
        font2 = QFont(Type.FAMILY.split(",")[0].strip('"'), 9, Type.WEIGHTS["semibold"])
        p.setFont(font2)
        p.setPen(QCol(Color.MUTED))
        p.drawText(0, cy + 18, self._ring_size, 16, Qt.AlignCenter, "MIX SCORE")

        p.end()


# ===============================================================
# Metric Tile (for bottom metrics bar)
# ===============================================================

class MetricTile(QFrame):
    """Compact metric display: label on top, large value below, optional delta."""

    def __init__(self, label: str, value: str, delta: str | None = None):
        super().__init__()
        self.setStyleSheet(f"background: transparent; border: none;")
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
            ("LUFS", "LUFS", "--"),
            ("True Peak", "dBTP", "--"),
            ("Dynamic Range", "dB", "--"),
            ("Stereo Width", "%", "--"),
            ("Phase", "", "--"),
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

        self._tiles["LUFS"].set_value(f"{lm.get('integrated_lufs', '--')} LUFS" if lm.get('integrated_lufs') is not None else "-- LUFS")
        self._tiles["True Peak"].set_value(f"{lm.get('true_peak_dbtp', '--')} dBTP" if lm.get('true_peak_dbtp') is not None else "-- dBTP")
        self._tiles["Dynamic Range"].set_value(f"{m.get('dynamic_range_db', '--')} dB" if m.get('dynamic_range_db') is not None else "-- dB")
        self._tiles["Stereo Width"].set_value(f"{m.get('stereo_width_percent', '--')}%" if m.get('stereo_width_percent') is not None else "--%")
        self._tiles["Phase"].set_value(f"{m.get('phase_correlation', '--')}" if m.get('phase_correlation') is not None else "--")


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
        self._canvas.setStyleSheet(f"background: {Color.BG}; border-radius: {Radius.SM}px; border: 1px solid {Color.LINE};")
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
        self._canvas.setStyleSheet(f"background: {Color.BG}; border-radius: {Radius.SM}px; border: 1px solid {Color.LINE};")
        lay.addWidget(self._canvas)


# ===============================================================
# Export Card
# ===============================================================

class ExportCard(QFrame):
    """Report export buttons: HTML / JSON / TXT."""

    exportRequested = Signal(str)

    def __init__(self):
        super().__init__()
        self.setGraphicsEffect(_shadow(Elevation.LOW, 30))
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

        for fmt, icon_name in [("HTML", "external"), ("JSON", "download"), ("TXT", "report")]:
            btn = QPushButton(f"  {fmt}")
            btn.setCursor(Qt.PointingHandCursor)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background: transparent; border: 1px solid {Color.LINE};
                    border-radius: {Radius.MD}px; padding: {Space.SM}px {Space.MD}px;
                    font-size: {Type.CAPTION}px; font-weight: {Type.WEIGHTS['medium']}; color: {Color.TEXT};
                    text-align: left;
                }}
                QPushButton:hover {{ background: {Color.HOVER}; border-color: {Color.ACCENT}; color: {Color.ACCENT}; }}
            """)
            btn.clicked.connect(lambda checked, f=fmt.lower(): self.exportRequested.emit(f))
            lay.addWidget(btn)


# Backward compatibility alias
MetricCard = MetricRow
