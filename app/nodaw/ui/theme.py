"""
CoProducer Design System -- Theme tokens + live skin application.

Skins: app/nodaw/ui/skins.py  (NoDAW 5 + Liquid Logic + Cyber-HUD)
Brand: D:\\Projects\\IMPORTANT BRAND STYLING FOR MYAIPLUG, NODAW, THEBEATMOB\\
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve
from PySide6.QtGui import QColor, QFont, QFontDatabase

from .skins import DEFAULT_SKIN, SKIN_ORDER, SKINS, get_skin, list_skins

# Colors - mutated by apply_skin(); start as Artifact Scan (analyzer default)


class Color:
    BG = "#03080a"
    SURFACE = "#041216"
    ELEVATED = "#081c20"
    HOVER = "#0c2830"
    ACCENT = "#00ffaa"
    ACCENT_SOFT = "#00d4ff"
    ACCENT_DIM = "#00a878"
    GOLD = "#9eff00"
    SUCCESS = "#00ffaa"
    WARNING = "#ffb000"
    ERROR = "#ff4466"
    SCORE_EXCELLENT = "#00ffaa"
    SCORE_GOOD = "#00d4ff"
    SCORE_FAIR = "#ffb000"
    SCORE_POOR = "#ff4466"
    TEXT = "#dfffee"
    MUTED = "#71a894"
    WHITE = "#FFFFFF"
    GLASS = "rgba(8, 28, 32, 0.82)"
    GLASS_BORDER = "rgba(0, 255, 170, 0.22)"
    GLOW = "rgba(0, 255, 170, 0.10)"
    LINE = "#0a3d32"
    LINE_HOVER = "#0f5a4a"
    SHADOW = "#000000"
    DIALOG_BG = "#041216"
    DIALOG_TEXT = "#dfffee"
    # Active skin id (not a color)
    _SKIN_ID = DEFAULT_SKIN

    @staticmethod
    def with_alpha(hex_color: str, alpha: float = 0.15) -> str:
        c = QColor(hex_color)
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"

    @staticmethod
    def wave_stops() -> tuple[str, str, str]:
        """
        Three gradient stops for waveforms — always follow the active skin.
        start (soft) → mid (primary accent) → end (dim / secondary).
        """
        return (Color.ACCENT_SOFT, Color.ACCENT, Color.ACCENT_DIM)

    @staticmethod
    def wave_edge() -> str:
        """Bright crest stroke for waveforms (skin-aware)."""
        return Color.ACCENT_SOFT


# Typography / spacing / layout - mutated by apply_skin()


class Type:
    DISPLAY_XL = 52
    DISPLAY_L = 34
    H1 = 24
    H2 = 18
    TITLE = 17
    SUBTITLE = 14
    BODY = 13
    CAPTION = 11
    TINY = 10

    WEIGHTS = {
        "light": 300,
        "regular": 400,
        "medium": 500,
        "semibold": 600,
        "bold": 700,
    }

    FAMILY = '"Segoe UI Variable", "Segoe UI", system-ui, sans-serif'
    DISPLAY = '"Bahnschrift", "Segoe UI", sans-serif'
    MONO = '"Cascadia Code", "Consolas", monospace'
    OVERLINE_TRACKING = "1.6px"
    TITLE_TRACKING = "-0.3px"


class Space:
    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 20
    XXL = 28
    XXXL = 40
    HUGE = 48
    MASSIVE = 56
    SECTION = 64


class Radius:
    SM = 4
    MD = 8
    LG = 10
    XL = 12
    XXL = 14
    XXXL = 16
    PILL = 9999
    CARD = 12
    BUTTON = 8
    DROP_ZONE = 14


class Layout:
    """Brand-specific layout / copy (not just color)."""

    SIDEBAR_WIDTH = 204
    RING_SIZE = 160
    DENSITY = "comfortable"
    PRODUCT_TAG = "CORE ANALYZER"
    HERO_TITLE = ""
    HERO_SUB = ""
    SCORE_LABEL = "OVERALL MIX SCORE"
    SHOW_EXTRA_METRICS = True
    SHOW_DEEP_READOUT = True
    CHARTS_WF_WEIGHT = 3
    CHARTS_SP_WEIGHT = 2
    # Frame language per skin (Card / panels)
    CARD_SHAPE = "glass"  # hud | glass | soft | hard | prism | liquid | cyber
    BUTTON_STYLE = "solid"  # solid | outline | hud | dual | soft | liquid | cyber
    CARD_SHADOW = 28


def apply_skin(skin_id: str | None) -> str:
    """Apply color + typography + spacing + layout tokens for a brand skin."""
    skin = get_skin(skin_id)
    sid = skin["id"]
    Color.BG = skin["bg"]
    Color.SURFACE = skin["surface"]
    Color.ELEVATED = skin["elevated"]
    Color.HOVER = skin["hover"]
    Color.ACCENT = skin["accent"]
    Color.ACCENT_SOFT = skin["accent_soft"]
    Color.ACCENT_DIM = skin["accent_dim"]
    Color.GOLD = skin["gold"]
    Color.SUCCESS = skin["success"]
    Color.WARNING = skin["warning"]
    Color.ERROR = skin["error"]
    Color.SCORE_EXCELLENT = skin["score_excellent"]
    Color.SCORE_GOOD = skin["score_good"]
    Color.SCORE_FAIR = skin["score_fair"]
    Color.SCORE_POOR = skin["score_poor"]
    Color.TEXT = skin["text"]
    Color.MUTED = skin["muted"]
    Color.LINE = skin["line"]
    Color.LINE_HOVER = skin["line_hover"]
    Color.DIALOG_BG = skin["surface"]
    Color.DIALOG_TEXT = skin["text"]
    Color.GLASS = Color.with_alpha(skin["elevated"], 0.88)
    Color.GLASS_BORDER = Color.with_alpha(skin["accent"], 0.22)
    Color.GLOW = Color.with_alpha(skin["accent"], 0.10)
    Color.SHADOW = "#000000"
    Color.WHITE = "#FFFFFF"
    Color._SKIN_ID = sid

    # Typography
    Type.FAMILY = skin.get("font_family", Type.FAMILY)
    Type.DISPLAY = skin.get("font_display", Type.DISPLAY)
    Type.MONO = skin.get("font_mono", Type.MONO)
    Type.BODY = int(skin.get("type_body", Type.BODY))
    Type.H1 = int(skin.get("type_h1", Type.H1))
    Type.H2 = int(skin.get("type_h2", Type.H2))
    Type.CAPTION = int(skin.get("type_caption", Type.CAPTION))
    Type.DISPLAY_XL = int(skin.get("type_display_xl", Type.DISPLAY_XL))
    Type.DISPLAY_L = max(28, Type.H1 + 8)
    Type.TITLE = Type.H2
    Type.SUBTITLE = max(12, Type.BODY + 1)
    Type.TINY = max(9, Type.CAPTION - 1)
    Type.OVERLINE_TRACKING = str(skin.get("overline_tracking", Type.OVERLINE_TRACKING))
    Type.TITLE_TRACKING = str(skin.get("title_tracking", Type.TITLE_TRACKING))

    # Spacing scale
    sc = float(skin.get("space_scale", 1.0))
    Space.XS = max(2, int(round(4 * sc)))
    Space.SM = max(4, int(round(8 * sc)))
    Space.MD = max(6, int(round(12 * sc)))
    Space.LG = max(8, int(round(16 * sc)))
    Space.XL = max(12, int(round(20 * sc)))
    Space.XXL = max(16, int(round(28 * sc)))
    Space.XXXL = max(20, int(round(40 * sc)))
    Space.HUGE = max(24, int(round(48 * sc)))
    Space.MASSIVE = max(28, int(round(56 * sc)))
    Space.SECTION = max(32, int(round(64 * sc)))

    # Radii
    rc = float(skin.get("radius_scale", 1.0))
    Radius.SM = max(2, int(round(4 * rc)))
    Radius.MD = max(3, int(round(8 * rc)))
    Radius.LG = max(4, int(round(10 * rc)))
    Radius.XL = max(4, int(round(12 * rc)))
    Radius.XXL = max(6, int(round(14 * rc)))
    Radius.XXXL = max(6, int(round(16 * rc)))
    Radius.CARD = int(skin.get("card_radius", max(0, int(round(8 * rc)))))
    Radius.BUTTON = int(skin.get("button_radius", max(0, int(round(6 * rc)))))
    Radius.DROP_ZONE = max(Radius.CARD, int(round(10 * rc)))
    Radius.PILL = 9999

    # Layout / copy
    # Floor width so category titles (NAVIGATE / APPEARANCE) never clip
    # Slim rail — just past the CoProducer mark (~180px at 32px height)
    Layout.SIDEBAR_WIDTH = max(192, min(228, int(skin.get("sidebar_width", 204))))
    Layout.RING_SIZE = int(skin.get("ring_size", 160))
    Layout.DENSITY = str(skin.get("density", "comfortable"))
    Layout.PRODUCT_TAG = str(skin.get("product_tag", "CORE ANALYZER"))
    Layout.HERO_TITLE = str(skin.get("hero_title", Layout.HERO_TITLE))
    Layout.HERO_SUB = str(skin.get("hero_sub", Layout.HERO_SUB))
    Layout.SCORE_LABEL = str(skin.get("score_label", Layout.SCORE_LABEL))
    Layout.SHOW_EXTRA_METRICS = bool(skin.get("show_extra_metrics", True))
    Layout.SHOW_DEEP_READOUT = bool(skin.get("show_deep_readout", True))
    Layout.CHARTS_WF_WEIGHT = int(skin.get("charts_wf_weight", 3))
    Layout.CHARTS_SP_WEIGHT = int(skin.get("charts_sp_weight", 2))
    Layout.CARD_SHAPE = str(skin.get("card_shape", "glass"))
    Layout.BUTTON_STYLE = str(skin.get("button_style", "solid"))
    Layout.CARD_SHADOW = int(skin.get("card_shadow", 28))
    return sid


def current_skin_id() -> str:
    return getattr(Color, "_SKIN_ID", DEFAULT_SKIN) or DEFAULT_SKIN


# Apply default at import so first paint is themed
apply_skin(DEFAULT_SKIN)


def pick_ui_font(point_size: int = 10, weight: int = QFont.Weight.Normal) -> QFont:
    families = QFontDatabase.families()
    for family in ("Manrope", "Segoe UI Variable", "Segoe UI", "Inter", "Arial"):
        if family in families or family in ("Segoe UI", "Arial"):
            f = QFont(family, point_size)
            f.setWeight(weight)
            f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
            f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            return f
    f = QFont()
    f.setPointSize(point_size)
    return f


def pick_display_font(point_size: int = 28, weight: int = QFont.Weight.Bold) -> QFont:
    families = QFontDatabase.families()
    for family in ("Rajdhani", "Bahnschrift", "Segoe UI Variable", "Segoe UI"):
        if family in families or family in ("Bahnschrift", "Segoe UI"):
            f = QFont(family, point_size)
            f.setWeight(weight)
            f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
            f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            return f
    return pick_ui_font(point_size, weight)


def pick_mono_font(point_size: int = 10, weight: int = QFont.Weight.Medium) -> QFont:
    families = QFontDatabase.families()
    for family in ("JetBrains Mono", "Cascadia Code", "Consolas"):
        if family in families or family == "Consolas":
            f = QFont(family, point_size)
            f.setWeight(weight)
            return f
    return pick_ui_font(point_size, weight)


class Duration:
    HOVER = 100
    FADE = 180
    SLIDE = 260
    SCORE = 700


class Easing:
    STANDARD = QEasingCurve.Type.OutCubic
    SCORE = QEasingCurve.Type.OutQuart
    DECELERATE = QEasingCurve.Type.OutExpo
    SPRING = QEasingCurve.Type.OutBack


class Elevation:
    FLAT = 0
    LOW = 10
    MED = 18
    HIGH = 28
    OVERLAY = 36


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def score_color(score: int | float | None) -> str:
    """
    Unified quality scale for overall score AND every metric (same algorithm).

    5% steps: 0 dark red → mid yellow → 100 bright green.
    Red = worse, yellow = middle, green = great.
    """
    if score is None:
        return Color.MUTED
    try:
        s = float(score)
    except (TypeError, ValueError):
        return Color.MUTED
    # Snap to 5% band center for distinct steps (0,5,10…100)
    s = max(0.0, min(100.0, s))
    band = int(round(s / 5.0) * 5)
    band = max(0, min(100, band))
    t = band / 100.0

    # Piecewise: dark red → red → orange → yellow (0-50) → lime → green (50-100)
    if band <= 50:
        u = band / 50.0
        # #5c0000 → #ef4444 → #eab308
        if u < 0.5:
            v = u / 0.5
            r, g, b = _lerp(92, 239, v), _lerp(0, 68, v), _lerp(0, 68, v)
        else:
            v = (u - 0.5) / 0.5
            r, g, b = _lerp(239, 234, v), _lerp(68, 179, v), _lerp(68, 8, v)
    else:
        u = (band - 50) / 50.0
        # #eab308 → #84cc16 → #22c55e → #4ade80
        if u < 0.5:
            v = u / 0.5
            r, g, b = _lerp(234, 132, v), _lerp(179, 204, v), _lerp(8, 22, v)
        else:
            v = (u - 0.5) / 0.5
            r, g, b = _lerp(132, 34, v), _lerp(204, 197, v), _lerp(22, 94, v)
            # final tip toward brighter green
            r, g, b = _lerp(r, 74, v * 0.35), _lerp(g, 222, v * 0.35), _lerp(b, 128, v * 0.35)

    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"


def score_rating(score: int | None) -> str:
    if score is None:
        return "No Analysis"
    if score >= 90:
        return "Professional Quality"
    if score >= 75:
        return "Good - Minor Corrections"
    if score >= 60:
        return "Usable - Technical Corrections"
    if score >= 40:
        return "Major Corrections Required"
    return "Not Release Ready"


def dialog_stylesheet() -> str:
    return f"""
        QMessageBox {{
            background-color: {Color.DIALOG_BG};
            color: {Color.DIALOG_TEXT};
            font-family: {Type.FAMILY};
            font-size: {Type.BODY}px;
        }}
        QMessageBox QLabel {{
            color: {Color.DIALOG_TEXT};
            background: transparent;
            min-width: 160px;
            max-width: 280px;
            font-size: {Type.BODY}px;
        }}
        QMessageBox QPushButton {{
            background-color: {Color.ELEVATED};
            color: {Color.DIALOG_TEXT};
            border: 1px solid {Color.LINE_HOVER};
            border-radius: 6px;
            padding: 8px 14px;
            min-width: 72px;
            font-weight: 600;
            font-family: {Type.MONO};
        }}
        QMessageBox QPushButton:hover {{
            background-color: {Color.HOVER};
            border-color: {Color.ACCENT};
            color: {Color.WHITE};
        }}
        QMessageBox QPushButton:default {{
            background-color: {Color.ACCENT};
            color: {Color.BG};
            border: none;
        }}
    """


__all__ = [
    "DEFAULT_SKIN",
    "SKINS",
    "SKIN_ORDER",
    "Color",
    "Duration",
    "Easing",
    "Elevation",
    "Radius",
    "Space",
    "Type",
    "apply_skin",
    "current_skin_id",
    "dialog_stylesheet",
    "get_skin",
    "list_skins",
    "pick_display_font",
    "pick_mono_font",
    "pick_ui_font",
    "score_color",
    "score_rating",
]
