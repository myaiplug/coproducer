# -*- coding: utf-8 -*-
"""CoProducer Design System — Motion utilities.

Reusable animation helpers for fade, slide, hover elevation, and score counting.
"""

from PySide6.QtCore import QPropertyAnimation, QTimer
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

from .theme import Duration, Easing


def fade_in(widget: QWidget, duration: int = Duration.FADE) -> QPropertyAnimation:
    """Animate widget opacity from 0 to 1."""
    eff = QGraphicsOpacityEffect(widget)
    eff.setOpacity(0.0)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity")
    anim.setDuration(duration)
    anim.setStartValue(0.0)
    anim.setEndValue(1.0)
    anim.setEasingCurve(Easing.STANDARD)
    anim.start()
    return anim


class ScoreCounter:
    """Count-up animation for score values.

    Usage:
        counter = ScoreCounter(on_tick=lambda val: label.setText(str(val)))
        counter.start(0, 85)
    """

    def __init__(self, on_tick: callable, interval: int = 20):
        self._on_tick = on_tick
        self._interval = interval
        self._current = 0
        self._target = 0
        self._timer = QTimer()
        self._timer.timeout.connect(self._tick)

    def start(self, current: int, target: int):
        self._current = current
        self._target = target
        self._timer.start(self._interval)

    def stop(self):
        self._timer.stop()

    def _tick(self):
        if self._current >= self._target:
            self._current = self._target
            self._on_tick(self._current)
            self._timer.stop()
            return
        step = max(1, int((self._target - self._current) / 5))
        self._current = min(self._current + step, self._target)
        self._on_tick(self._current)
