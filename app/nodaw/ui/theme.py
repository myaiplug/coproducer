# -*- coding: utf-8 -*-
"""
CoProducer Design System -- Theme tokens.

Typography, color, spacing, elevation, and motion.
Design target: FabFilter, iZotope, Arc Browser, Apple Pro Apps.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve
from PySide6.QtGui import QColor


# Colors

class Color:
    BG = "#0B0F14"          # Background -- deepest layer
    SURFACE = "#14181E"     # Surface -- sidebar, panels
    ELEVATED = "#1A1F27"    # Elevated Surface -- cards
    HOVER = "#222832"       # Card / surface hover
    ACCENT = "#4A9EFF"      # Primary accent (restrained blue)
    SUCCESS = "#3CCB6E"     # Release ready, positive
    WARNING = "#E6A23C"     # Needs attention
    ERROR = "#E8594A"       # Blocking, critical
    TEXT = "#E6EAF0"        # Primary text
    MUTED = "#7C879A"       # Secondary text, captions
    WHITE = "#FFFFFF"
    GLASS = "rgba(26, 31, 39, 0.75)"  # Glass surface
    GLASS_BORDER = "rgba(74, 158, 255, 0.12)"  # Glass border glow
    GLOW = "rgba(74, 158, 255, 0.06)"  # Subtle accent glow
    LINE = "#262D37"        # Borders, dividers
    LINE_HOVER = "#333C48"  # Border hover
    SHADOW = "#000000"

    @staticmethod
    def with_alpha(hex_color: str, alpha: float = 0.15) -> str:
        c = QColor(hex_color)
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"


# Typography

class Type:
    DISPLAY_XL = 56   # Hero numbers (score)
    DISPLAY_L = 42    # Page hero titles
    H1 = 28           # Section headers
    H2 = 22           # Card titles
    TITLE = 22        # Drop zone, empty state titles
    SUBTITLE = 16     # Secondary text
    BODY = 14         # Body copy
    CAPTION = 12      # Labels, badges, overline
    TINY = 10         # Eyebrow, overline

    WEIGHTS = {
        "light": 300,
        "regular": 400,
        "medium": 500,
        "semibold": 600,
        "bold": 700,
    }
    FAMILY = '"Inter", system-ui, sans-serif'
    MONO = '"JetBrains Mono", "Cascadia Code", "Consolas", monospace'


# Spacing (8-point grid)

class Space:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32
    XXXL = 48
    HUGE = 56
    MASSIVE = 64
    SECTION = 80


# Motion

class Duration:
    HOVER = 120
    FADE = 200
    SLIDE = 300
    SCORE = 800


class Easing:
    STANDARD = QEasingCurve.Type.OutCubic
    SCORE = QEasingCurve.Type.OutQuart
    DECELERATE = QEasingCurve.Type.OutExpo
    SPRING = QEasingCurve.Type.OutBack


# Elevation (shadow blur radius in px)

class Elevation:
    FLAT = 0
    LOW = 8
    MED = 14
    HIGH = 20
    OVERLAY = 28


# Corner radii

class Radius:
    SM = 4
    MD = 8
    LG = 10
    XL = 12
    XXL = 14
    XXXL = 16
    PILL = 9999
    CARD = 14
    BUTTON = 8
    DROP_ZONE = 16


# Mapping helpers

def score_color(score: int | None) -> str:
    if score is None:
        return Color.MUTED
    if score >= 85:
        return Color.SUCCESS
    if score >= 65:
        return Color.ACCENT
    if score >= 40:
        return Color.WARNING
    return Color.ERROR


def score_rating(score: int | None) -> str:
    if score is None:
        return "No Analysis"
    if score >= 90:
        return "Release Ready"
    if score >= 75:
        return "Good -- Minor Corrections"
    if score >= 60:
        return "Usable -- Technical Corrections"
    if score >= 40:
        return "Major Corrections Required"
    return "Not Release Ready"
