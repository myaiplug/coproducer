"""
CoProducer Design System - SVG Icon Library.

Icons are loaded from app/nodaw/ui/assets/icons/*.svg
Fallback: a simple SVG circle (professional, no unicode).

Style: stroke-based, rounded caps/joins, consistent 1.5px stroke.
Source: Material Symbols Rounded / Phosphor-inspired.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray
from PySide6.QtSvgWidgets import QSvgWidget

from .theme import Color

_ICONS_DIR = Path(__file__).resolve().parent / "assets" / "icons"

_FALLBACK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
    'fill="none" stroke="currentColor" stroke-width="1.5" '
    'stroke-linecap="round" stroke-linejoin="round">'
    '<circle cx="12" cy="12" r="8"/>'
    '<path d="M12 8v4"/>'
    '<path d="M12 16h.01"/>'
    "</svg>"
)

_CACHE: dict[str, str] = {}


def _load(name: str) -> str:
    if name in _CACHE:
        return _CACHE[name]

    path = _ICONS_DIR / f"{name}.svg"
    if path.is_file():
        try:
            svg = path.read_text(encoding="utf-8")
            _CACHE[name] = svg
            return svg
        except OSError:
            pass

    _CACHE[name] = _FALLBACK
    return _FALLBACK


def get(name: str) -> str:
    """Get SVG string by icon name. Falls back to generic circle icon."""
    return _load(name)


# ===============================================================
# IconWidget
# ===============================================================


class IconWidget(QSvgWidget):
    """SVG icon widget.

    Args:
        name: Icon name (matches filename in assets/icons/ without .svg)
        size: Width/height in pixels
        color: Stroke color (any CSS color string)
    """

    def __init__(self, name: str, size: int = 20, color: str | None = None):
        super().__init__()
        self._name = name
        self._color = color or Color.MUTED
        self.setFixedSize(size, size)
        self.setStyleSheet("background: transparent; border: none;")
        self._render()

    def _render(self):
        svg = _load(self._name)
        colored = svg.replace("currentColor", self._color)
        self.load(QByteArray(colored.encode("utf-8")))

    def set_color(self, color: str):
        self._color = color
        self._render()

    def set_name(self, name: str):
        self._name = name
        self._render()
