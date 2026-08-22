"""
Side-by-side Original vs Repaired analysis studio.

Visual + technical comparison using existing report metrics and
soundfile/librosa for waveform, spectrum, spectrogram, and level meters.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
)
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from nodaw.audio.pcm_player import DualHiFiPlayer

from .charts import (
    ChartPanel,
    SpectrogramCanvas,
    SpectrumCanvas,
    WaveformCanvas,
    compute_hd_spectrogram,
    load_spectrum_bands,
    load_waveform_peaks,
)
from .metric_status import metric_status, value_color
from .theme import Color, Radius, Space, Type, score_color, score_rating

# ---------------------------------------------------------------------------
# Multi-channel LED level meter (green → yellow → red)
# ---------------------------------------------------------------------------


class LevelMeter(QWidget):
    """Vertical multi-segment LED meter (digital console style)."""

    def __init__(self, channels: int = 2, parent=None):
        super().__init__(parent)
        self._channels = max(1, channels)
        self._levels = [0.0] * self._channels  # 0..1 linear
        self._peaks = [0.0] * self._channels
        self.setMinimumWidth(28 * self._channels + 8)
        self.setMinimumHeight(140)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

    def set_levels(self, levels: list[float]):
        self._levels = []
        self._peaks = list(self._peaks)
        while len(self._peaks) < len(levels):
            self._peaks.append(0.0)
        for i, lv in enumerate(levels):
            v = max(0.0, min(1.0, float(lv)))
            self._levels.append(v)
            self._peaks[i] = max(self._peaks[i] * 0.92, v)
        self._channels = len(self._levels) or 1
        self.update()

    def set_from_dbfs(self, dbfs_list: list[float | None]):
        """Map dBFS (-60..0) to 0..1."""
        levels = []
        for d in dbfs_list:
            if d is None:
                levels.append(0.0)
            else:
                # -60 → 0, 0 → 1
                levels.append(max(0.0, min(1.0, (float(d) + 60.0) / 60.0)))
        self.set_levels(levels)

    def clear(self):
        self._levels = [0.0] * self._channels
        self._peaks = [0.0] * self._channels
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        n = max(1, len(self._levels) or self._channels)
        gap = 4
        mw = max(8, (w - gap * (n + 1)) // n)
        segs = 24
        seg_h = max(2, (h - 8) // segs - 1)

        for ch in range(n):
            x = gap + ch * (mw + gap)
            level = self._levels[ch] if ch < len(self._levels) else 0.0
            peak = self._peaks[ch] if ch < len(self._peaks) else 0.0
            lit = int(level * segs)
            peak_seg = int(peak * segs)

            for s in range(segs):
                # bottom = quiet, top = hot
                y = h - 4 - (s + 1) * (seg_h + 1)
                frac = s / max(1, segs - 1)
                if frac < 0.55:
                    col = QColor("#22c55e")  # green
                elif frac < 0.78:
                    col = QColor("#eab308")  # yellow
                else:
                    col = QColor("#ef4444")  # red

                if s < lit:
                    col.setAlpha(230)
                else:
                    col.setAlpha(35)

                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(col)
                p.drawRoundedRect(x, y, mw, seg_h, 1, 1)

            # peak hold pip
            if peak_seg > 0:
                py = h - 4 - (peak_seg) * (seg_h + 1)
                p.setBrush(QColor(Color.WHITE))
                p.drawRect(x, py, mw, 2)

        p.end()


class MetricCompareRow(QFrame):
    """One metric: Original | bar delta | Repaired + explanation."""

    def __init__(self, label: str, unit: str = ""):
        super().__init__()
        self.setStyleSheet("background: transparent; border: none;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(Space.SM)

        self.lbl = QLabel(label)
        self.lbl.setFixedWidth(110)
        self.lbl.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 0.8px; color: {Color.MUTED};"
        )
        lay.addWidget(self.lbl)

        self.a_val = QLabel("-")
        self.a_val.setAlignment(Qt.AlignCenter)
        self.a_val.setFixedWidth(72)
        self.a_val.setStyleSheet(f"font-family: {Type.MONO}; font-size: 13px; font-weight: 700;")
        lay.addWidget(self.a_val)

        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background: {Color.LINE}; border: none; border-radius: 4px; }}
            QProgressBar::chunk {{ background: {Color.ACCENT}; border-radius: 4px; }}
        """)
        lay.addWidget(self.bar, 1)

        self.b_val = QLabel("-")
        self.b_val.setAlignment(Qt.AlignCenter)
        self.b_val.setFixedWidth(72)
        self.b_val.setStyleSheet(f"font-family: {Type.MONO}; font-size: 13px; font-weight: 700;")
        lay.addWidget(self.b_val)

        self.delta = QLabel("")
        self.delta.setFixedWidth(64)
        self.delta.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.delta.setStyleSheet(
            f"font-family: {Type.MONO}; font-size: 11px; color: {Color.MUTED};"
        )
        lay.addWidget(self.delta)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"font-size: 10px; color: {Color.MUTED};")
        # hint sits full width under via outer layout if needed - compact inline
        lay.addWidget(self.hint, 1)

    def set_pair(self, a: Any, b: Any, kind: str, unit: str = ""):
        def fmt(v):
            if v is None:
                return "-"
            try:
                return f"{float(v):.1f}{unit}"
            except Exception:
                return str(v)

        self.a_val.setText(fmt(a))
        self.b_val.setText(fmt(b))
        ca = value_color(kind, a)
        cb = value_color(kind, b)
        self.a_val.setStyleSheet(
            f"font-family: {Type.MONO}; font-size: 13px; font-weight: 700; color: {ca};"
        )
        self.b_val.setStyleSheet(
            f"font-family: {Type.MONO}; font-size: 13px; font-weight: 700; color: {cb};"
        )

        # bar = quality of repaired (pseudo score)
        _, sc_b, hint_b = metric_status(kind, b)
        _, sc_a, hint_a = metric_status(kind, a)
        self.bar.setValue(int(sc_b or 0))
        col = score_color(sc_b)
        self.bar.setStyleSheet(f"""
            QProgressBar {{ background: {Color.LINE}; border: none; border-radius: 4px; }}
            QProgressBar::chunk {{ background: {col}; border-radius: 4px; }}
        """)

        try:
            if a is not None and b is not None:
                d = float(b) - float(a)
                sign = "+" if d >= 0 else ""
                improved = (sc_b or 0) >= (sc_a or 0)
                dcol = Color.SUCCESS if improved else Color.WARNING
                self.delta.setText(f"{sign}{d:.2f}")
                self.delta.setStyleSheet(
                    f"font-family: {Type.MONO}; font-size: 11px; font-weight: 600; color: {dcol};"
                )
            else:
                self.delta.setText("")
        except Exception:
            self.delta.setText("")

        note = hint_b or hint_a or ""
        if sc_a is not None and sc_b is not None:
            if sc_b > sc_a + 5:
                note = (note + "  ·  improved").strip(" ·")
            elif sc_b < sc_a - 5:
                note = (note + "  ·  regress").strip(" ·")
        self.hint.setText(note)


class SpectrogramView(SpectrogramCanvas):
    """HD mel spectrogram (shared accurate pipeline: 44.1 kHz / 4096 FFT / 256 mels)."""

    def __init__(self, parent=None):
        super().__init__(parent, interactive=False)
        self.setMinimumHeight(110)

    def load_path(self, path: Path | str | None):
        self.clear()
        if not path or not Path(path).is_file():
            return
        self.set_loading(True, "Computing HD spectrogram…")
        # A/B dual view: same pipeline both sides; linear hop (no power-of-2 warp)
        img, meta = compute_hd_spectrogram(
            path,
            target_sr=44100,
            n_mels=192,
            n_fft=2048,
            time_bins=1000,
            fmin=20.0,
            fmax=20000.0,
            max_duration_s=720.0,
            top_db=80.0,
        )
        if img is not None and not img.isNull():
            self.set_image(
                img,
                duration_s=float(meta.get("duration_s") or 0.0),
                fmin=float(meta.get("fmin") or 20.0),
                fmax=float(meta.get("fmax") or 20000.0),
            )
        else:
            err = (meta or {}).get("error")
            self.set_image(
                None,
                status=f"Spectrogram unavailable{(': ' + str(err)) if err else ''}",
            )


def _loud(track: dict) -> dict:
    m = (track or {}).get("metrics") or {}
    return (m.get("loudness") or {}) if isinstance(m, dict) else {}


def _metrics(track: dict) -> dict:
    m = (track or {}).get("metrics") or {}
    return m if isinstance(m, dict) else {}


def _extra(track: dict) -> dict:
    e = (track or {}).get("extra") or {}
    return e if isinstance(e, dict) else {}


def estimate_channel_levels(path: Path | None) -> list[float]:
    """RMS-based L/R (or mono) levels 0..1 for meters."""
    if not path or not path.is_file():
        return [0.0, 0.0]
    try:
        import soundfile as sf

        data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        # use middle 10s or full
        n = len(data)
        if n > sr * 10:
            mid = n // 2
            data = data[mid - sr * 5 : mid + sr * 5]
        chans = data.shape[1]
        levels = []
        for c in range(min(chans, 2)):
            rms = float(np.sqrt(np.mean(data[:, c] ** 2)))
            dbfs = 20 * np.log10(max(rms, 1e-12))
            levels.append(max(0.0, min(1.0, (dbfs + 60.0) / 60.0)))
        if len(levels) == 1:
            levels.append(levels[0])
        return levels
    except Exception:
        return [0.0, 0.0]


# ---------------------------------------------------------------------------
# A / B side toggle (custom-painted — no QSS font warping / clipping)
# ---------------------------------------------------------------------------


class ABSideButton(QWidget):
    """
    Equal square A/B deck switcher.

    Plain QPushButton + theme fonts clipped letter B (wider glyph than A) when
    borders thickened and padding was 0. This draws the letter with a fixed
    Segoe UI face and measured centering so A and B stay crisp and identical.
    """

    clicked = Signal()

    def __init__(self, letter: str, parent=None):
        super().__init__(parent)
        self._letter = (letter or "?").strip().upper()[:1] or "?"
        self._active = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(48)
        self.setMinimumWidth(72)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(
            f"Hear {self._letter} at the exact same playhead (instant, no pause)"
        )

    def set_active(self, active: bool):
        a = bool(active)
        if a == self._active:
            return
        self._active = a
        self.update()

    def is_active(self) -> bool:
        return self._active

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        r = self.rect().adjusted(1, 1, -1, -1)
        radius = float(max(4, min(10, int(Radius.BUTTON) if Radius.BUTTON else 6)))

        if self._active:
            p.setPen(QPen(QColor(Color.ACCENT_SOFT), 2.0))
            p.setBrush(QColor(Color.ACCENT))
            ink = QColor(Color.BG)
        else:
            p.setPen(QPen(QColor(Color.LINE), 1.0))
            p.setBrush(QColor(Color.ELEVATED))
            ink = QColor(Color.MUTED)

        p.drawRoundedRect(r, radius, radius)

        # Fixed face — never Impact / Georgia / mono skins
        font = QFont("Segoe UI", 17)
        font.setWeight(QFont.Weight.DemiBold)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        p.setFont(font)
        fm = QFontMetrics(font)
        # Optical center (B is wider; measure exact advance)
        tw = fm.horizontalAdvance(self._letter)
        th = fm.ascent()
        x = r.center().x() - tw / 2.0
        y = r.center().y() + th / 2.0 - fm.descent() / 2.0
        p.setPen(ink)
        p.drawText(int(round(x)), int(round(y)), self._letter)
        p.end()


# ---------------------------------------------------------------------------
# Full A/B page
# ---------------------------------------------------------------------------


class ABComparePage(QWidget):
    """Exhaustive A/B analysis panel (standalone page or embedded in Reference Match)."""

    # Emitted right before A/B starts playing — host should stop home / mini players
    requestStopOthers = Signal()

    def __init__(self, parent=None, embedded: bool = False):
        super().__init__(parent)
        self._orig_report: dict = {}
        self._rep_report: dict = {}
        self._orig_path: Path | None = None
        self._rep_path: Path | None = None
        # Source paths (played at native quality via DualHiFiPlayer)
        self._play_path_a: Path | None = None
        self._play_path_b: Path | None = None
        self._embedded = embedded
        self._ab_side = "a"
        self._env_a: list[float] = []
        self._env_b: list[float] = []
        self._ab_scrubbing = False
        self._last_hard_sync_ms = 0

        if embedded:
            root = self
            lay = QVBoxLayout(self)
            lay.setContentsMargins(0, Space.MD, 0, 0)
            lay.setSpacing(Space.MD)
        else:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setStyleSheet("background: transparent; border: none;")
            inner = QWidget()
            inner.setStyleSheet("background: transparent;")
            lay = QVBoxLayout(inner)
            lay.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.XL)
            lay.setSpacing(Space.MD)
            outer = QVBoxLayout(self)
            outer.setContentsMargins(0, 0, 0, 0)
            outer.addWidget(scroll)
            scroll.setWidget(inner)

        # Header
        self._title = QLabel("Side-by-side analysis" if embedded else "Repair A/B Studio")
        self._title.setStyleSheet(
            f"font-size: {Type.H1 if not embedded else Type.H2}px; font-weight: 700; "
            f"font-family: {Type.DISPLAY}; letter-spacing: -0.4px; color: {Color.TEXT};"
        )
        lay.addWidget(self._title)
        self.subtitle = QLabel(
            "Exhaustive comparison: scores, meters, dual A/B player, waveforms, spectrum, spectrograms, every metric."
            if embedded
            else "Side-by-side technical comparison: original mix vs repaired output."
        )
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet(f"font-size: {Type.BODY}px; color: {Color.MUTED};")
        lay.addWidget(self.subtitle)

        # Score + meters row
        top = QHBoxLayout()
        top.setSpacing(Space.MD)

        self.col_a = self._score_column("YOUR MIX" if embedded else "ORIGINAL")
        self.col_b = self._score_column("REFERENCE" if embedded else "REPAIRED")
        top.addWidget(self.col_a["frame"], 1)

        # Center meters cluster
        meters_box = QFrame()
        meters_box.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        ml = QVBoxLayout(meters_box)
        ml.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        mh = QLabel("LEVEL METERS")
        mh.setAlignment(Qt.AlignCenter)
        mh.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.4px; color: {Color.MUTED};"
        )
        ml.addWidget(mh)
        meters_row = QHBoxLayout()
        meters_row.setSpacing(Space.LG)
        def _meter_caption(text: str) -> QLabel:
            lab = QLabel(text)
            lab.setAlignment(Qt.AlignHCenter)
            lab.setStyleSheet(
                "font-family: 'Segoe UI', system-ui, sans-serif; "
                "font-size: 12px; font-weight: 700; letter-spacing: 0px; "
                f"color: {Color.MUTED}; background: transparent;"
            )
            return lab

        a_m = QVBoxLayout()
        a_m.addWidget(_meter_caption("A"), 0, Qt.AlignHCenter)
        self.meter_a = LevelMeter(2)
        a_m.addWidget(self.meter_a, 0, Qt.AlignHCenter)
        a_m.addWidget(_meter_caption("L  R"), 0, Qt.AlignHCenter)
        meters_row.addLayout(a_m)
        b_m = QVBoxLayout()
        b_m.addWidget(_meter_caption("B"), 0, Qt.AlignHCenter)
        self.meter_b = LevelMeter(2)
        b_m.addWidget(self.meter_b, 0, Qt.AlignHCenter)
        b_m.addWidget(_meter_caption("L  R"), 0, Qt.AlignHCenter)
        meters_row.addLayout(b_m)
        ml.addLayout(meters_row)
        self.delta_score_lbl = QLabel("Δ score  -")
        self.delta_score_lbl.setAlignment(Qt.AlignCenter)
        self.delta_score_lbl.setStyleSheet(
            f"font-family: {Type.MONO}; font-size: 14px; font-weight: 700; color: {Color.ACCENT};"
        )
        ml.addWidget(self.delta_score_lbl)
        top.addWidget(meters_box, 0)

        top.addWidget(self.col_b["frame"], 1)
        lay.addLayout(top)

        # Instant A/B audio player + side-by-side waveforms (click either to switch)
        lay.addWidget(self._build_ab_player())

        # Spectrum
        specs = QHBoxLayout()
        specs.setSpacing(Space.MD)
        self.sp_a = SpectrumCanvas()
        self.sp_b = SpectrumCanvas()
        self.sp_panel_a = ChartPanel("ORIGINAL SPECTRUM", self.sp_a)
        self.sp_panel_b = ChartPanel("REPAIRED SPECTRUM", self.sp_b)
        specs.addWidget(self.sp_panel_a, 1)
        specs.addWidget(self.sp_panel_b, 1)
        lay.addLayout(specs)

        # Spectrograms
        sgrams = QHBoxLayout()
        sgrams.setSpacing(Space.MD)
        self.sg_a = SpectrogramView()
        self.sg_b = SpectrogramView()
        sgrams.addWidget(self._wrap_chart("ORIGINAL MEL SPECTROGRAM", self.sg_a), 1)
        sgrams.addWidget(self._wrap_chart("REPAIRED MEL SPECTROGRAM", self.sg_b), 1)
        lay.addLayout(sgrams)

        # Metric comparison table
        table = QFrame()
        table.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        tl = QVBoxLayout(table)
        tl.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        th = QLabel("METRIC DELTAS  ·  original → repaired  ·  progress = repaired quality band")
        th.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.2px; color: {Color.MUTED};"
        )
        tl.addWidget(th)

        hdr = QHBoxLayout()
        for text, w in (
            ("METRIC", 110),
            ("ORIGINAL", 72),
            ("QUALITY", 0),
            ("REPAIRED", 72),
            ("Δ", 64),
            ("NOTES", 0),
        ):
            lab = QLabel(text)
            lab.setStyleSheet(f"font-size: 9px; color: {Color.MUTED}; font-weight: 600;")
            if w:
                lab.setFixedWidth(w)
            hdr.addWidget(lab, 1 if w == 0 else 0)
        tl.addLayout(hdr)

        self.rows: dict[str, MetricCompareRow] = {}
        for key, label, unit in [
            ("lufs", "LOUDNESS", ""),
            ("tp", "TRUE PEAK", ""),
            ("lra", "LRA", ""),
            ("peak", "PEAK", ""),
            ("rms", "RMS", ""),
            ("crest", "CREST", ""),
            ("dr", "DYN RANGE", ""),
            ("width", "STEREO WIDTH", ""),
            ("phase", "PHASE", ""),
            ("noise", "NOISE FLOOR", ""),
            ("clip", "CLIPPING", ""),
        ]:
            row = MetricCompareRow(label, unit)
            self.rows[key] = row
            tl.addWidget(row)
        lay.addWidget(table)

        # Explanation
        exp = QFrame()
        exp.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        el = QVBoxLayout(exp)
        el.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        eh = QLabel("ENGINEERING READOUT")
        eh.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.2px; color: {Color.MUTED};"
        )
        el.addWidget(eh)
        self.explain = QLabel("Run a repair to populate original vs repaired analysis.")
        self.explain.setWordWrap(True)
        self.explain.setStyleSheet(
            f"font-size: {Type.BODY}px; color: {Color.TEXT}; line-height: 1.45;"
        )
        el.addWidget(self.explain)
        lay.addWidget(exp)

        if not embedded:
            lay.addStretch()

        # Dual HiFi player: native SR, float32, sample-locked A/B, cosine switch.
        # Never forces 16-bit or 48 kHz (that was destroying preview quality).
        self._dual = DualHiFiPlayer(self)
        self._player_a = self._dual.deck_a
        self._player_b = self._dual.deck_b
        self._dual.positionChanged.connect(self._on_ab_position)
        self._dual.durationChanged.connect(self._on_ab_duration)
        self._dual.playbackStateChanged.connect(self._on_ab_state)
        self._dual.errorOccurred.connect(self._on_player_b_error)
        self._dur_a_ms = 0
        self._dur_b_ms = 0
        # UI clock only — DualHiFiPlayer is already sample-locked (no hard seeks)
        self._sync_timer = QTimer(self)
        self._sync_timer.setInterval(100)
        self._sync_timer.timeout.connect(self._sync_players)
        # Shared lookahead window on both decks
        self._ab_lookahead = 0.35
        try:
            self.wf_a.set_lookahead(self._ab_lookahead)
            self.wf_b.set_lookahead(self._ab_lookahead)
        except Exception:
            pass
        # Click waveform: seek both decks to that time + instant HQ crossfade switch
        self.wf_a.seekRequested.connect(lambda t: self._ab_seek_ms(int(t * 1000)))
        self.wf_b.seekRequested.connect(lambda t: self._ab_seek_ms(int(t * 1000)))
        self.wf_a.activated.connect(lambda: self._set_ab_side("a"))
        self.wf_b.activated.connect(lambda: self._set_ab_side("b"))
        self.wf_a.set_selected(True)
        self.wf_b.set_selected(False)

    def _build_ab_player(self) -> QFrame:
        box = QFrame()
        box.setObjectName("ABPlayer")
        box.setStyleSheet(f"""
            QFrame#ABPlayer {{
                background: {Color.SURFACE};
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.35)};
                border-radius: {Radius.XL}px;
            }}
        """)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        lay.setSpacing(Space.SM)

        hdr = QHBoxLayout()
        title = QLabel(
            "A/B LISTEN  ·  two waveforms · click either · instant switch · same playhead"
        )
        title.setStyleSheet(
            f"font-size: 9px; font-weight: 700; letter-spacing: 1.3px; color: {Color.MUTED};"
        )
        hdr.addWidget(title)
        hdr.addStretch()
        self.ab_time_lbl = QLabel("0:00.0  /  0:00.0")
        self.ab_time_lbl.setStyleSheet(
            f"font-family: {Type.MONO}; font-size: 12px; color: {Color.ACCENT_SOFT};"
        )
        hdr.addWidget(self.ab_time_lbl)
        lay.addLayout(hdr)

        # Side-by-side waveforms (core A/B surface)
        # A/B decks: always cyan→purple color; wheel scrolls page (no audio skip)
        self.wf_a = WaveformCanvas(
            interactive=True,
            progress_reveal=False,
            wheel_seeks=False,
            always_colored=True,
        )
        self.wf_b = WaveformCanvas(
            interactive=True,
            progress_reveal=False,
            wheel_seeks=False,
            always_colored=True,
        )
        self.wf_a.setMinimumHeight(132)
        self.wf_b.setMinimumHeight(132)
        self.wf_panel_a = ChartPanel("A  ·  YOUR MIX / ORIGINAL", self.wf_a)
        self.wf_panel_b = ChartPanel("B  ·  REFERENCE / REPAIRED", self.wf_b)
        waves = QHBoxLayout()
        waves.setSpacing(Space.MD)
        waves.addWidget(self.wf_panel_a, 1)
        waves.addWidget(self.wf_panel_b, 1)
        lay.addLayout(waves)

        # A / B deck toggles (custom paint — B no longer clips/warps)
        row = QHBoxLayout()
        row.setSpacing(Space.SM)
        self.btn_side_a = ABSideButton("A")
        self.btn_side_b = ABSideButton("B")
        self.btn_side_a.set_active(True)
        self.btn_side_b.set_active(False)
        self.btn_side_a.clicked.connect(lambda: self._set_ab_side("a"))
        self.btn_side_b.clicked.connect(lambda: self._set_ab_side("b"))
        row.addWidget(self.btn_side_a, 1)
        row.addWidget(self.btn_side_b, 1)

        self.btn_ab_play = QPushButton("Play")
        self.btn_ab_play.setCursor(Qt.PointingHandCursor)
        self.btn_ab_play.setFixedHeight(52)
        self.btn_ab_play.setMinimumWidth(100)
        self.btn_ab_play.setStyleSheet(f"""
            QPushButton {{
                background: {Color.ACCENT};
                color: {Color.BG};
                border: none;
                border-radius: {Radius.BUTTON}px;
                font-weight: 700;
                font-size: 14px;
            }}
            QPushButton:hover {{ background: {Color.ACCENT_SOFT}; }}
        """)
        self.btn_ab_play.clicked.connect(self._ab_toggle_play)
        row.addWidget(self.btn_ab_play)

        self.btn_ab_stop = QPushButton("Stop")
        self.btn_ab_stop.setCursor(Qt.PointingHandCursor)
        self.btn_ab_stop.setFixedHeight(52)
        self.btn_ab_stop.setStyleSheet(f"""
            QPushButton {{
                background: {Color.ELEVATED};
                color: {Color.TEXT};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.BUTTON}px;
                font-weight: 600;
            }}
            QPushButton:hover {{ border-color: {Color.ACCENT}; }}
        """)
        self.btn_ab_stop.clicked.connect(self._ab_stop)
        row.addWidget(self.btn_ab_stop)
        lay.addLayout(row)

        self.ab_slider = QSlider(Qt.Orientation.Horizontal)
        self.ab_slider.setRange(0, 1000)
        self.ab_slider.sliderMoved.connect(self._ab_seek_ms)
        self.ab_slider.sliderPressed.connect(lambda: setattr(self, "_ab_scrubbing", True))
        self.ab_slider.sliderReleased.connect(self._ab_slider_released)
        self._ab_scrubbing = False
        lay.addWidget(self.ab_slider)

        tip = QLabel(
            "Click A/B or a waveform to switch (mute only — no restart). "
            "Scroll the page freely — wheel over the waves will not skip audio "
            "(Ctrl+wheel seeks). Both decks stay full cyan→purple color."
        )
        tip.setStyleSheet(f"font-size: 11px; color: {Color.MUTED};")
        tip.setWordWrap(True)
        lay.addWidget(tip)
        return box

    def _style_ab_side_buttons(self):
        """Refresh A/B paint state + meter emphasis."""
        try:
            self.btn_side_a.set_active(self._ab_side == "a")
            self.btn_side_b.set_active(self._ab_side == "b")
        except Exception:
            pass
        try:
            active_border = f"2px solid {Color.ACCENT}"
            idle_border = f"1px solid {Color.LINE}"
            self.meter_a.setStyleSheet(
                f"border: {active_border if self._ab_side == 'a' else idle_border}; border-radius: 6px;"
            )
            self.meter_b.setStyleSheet(
                f"border: {active_border if self._ab_side == 'b' else idle_border}; border-radius: 6px;"
            )
        except Exception:
            pass

    def _set_ab_side(self, side: str):
        """Instant A↔B: cosine crossfade on the dual HiFi stream (no stop/seek)."""
        side = "b" if side == "b" else "a"
        self._ab_side = side
        try:
            self._dual.setSide(side)
        except Exception:
            pass
        # Secondary-color fill on the selected waveform
        try:
            self.wf_a.set_selected(side == "a")
            self.wf_b.set_selected(side == "b")
            if hasattr(self, "wf_panel_a"):
                self.wf_panel_a.set_live(side == "a")
            if hasattr(self, "wf_panel_b"):
                self.wf_panel_b.set_live(side == "b")
        except Exception:
            pass
        self._style_ab_side_buttons()
        try:
            self._update_live_meters(self._dual.position(), self._dual.duration())
        except Exception:
            pass

    def _ab_toggle_play(self):
        if not self._orig_path or not self._rep_path:
            return
        if self._dual.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._dual.pause()
            self._sync_timer.stop()
        else:
            # Stop Home / mini A/B so only this pair uses the audio device
            try:
                self.requestStopOthers.emit()
            except Exception:
                pass
            # Native-quality sources (float32, native SR — no 16-bit/48k downgrade)
            self._ensure_sources()
            self._set_ab_side(self._ab_side)
            self._dual.play()
            self._sync_timer.start()

    def _ab_stop(self):
        try:
            self._dual.stop()
        except Exception:
            pass
        self._sync_timer.stop()
        self.ab_slider.setValue(0)
        self.ab_time_lbl.setText(
            self._fmt_ms(0) + "  /  " + self._fmt_ms(self._dual.duration())
        )
        self.wf_a.set_position(0)
        self.wf_b.set_position(0)
        self.btn_ab_play.setText("Play")
        self.meter_a.set_levels([0.0, 0.0])
        self.meter_b.set_levels([0.0, 0.0])

    def _on_player_b_error(self, *args):
        """Surface dual-player errors without forcing a quality-killing re-encode."""
        try:
            err = self._dual.errorString() if hasattr(self._dual, "errorString") else str(args)
            print("A/B HiFi player error:", err)
        except Exception as exc:
            print("A/B player error handler:", exc)

    def _ab_seek_ms(self, ms: int):
        ms = max(0, int(ms))
        # Clamp to shared span; DualHiFiPlayer keeps both decks sample-locked
        dur_a = self._dur_a_ms or self._dual.durationA() or 0
        dur_b = self._dur_b_ms or self._dual.durationB() or 0
        span = max(dur_a, dur_b, 1)
        pos = min(ms, span)
        self._dual.setPosition(pos)
        self.wf_a.set_position(min(pos, dur_a if dur_a else pos) / 1000.0)
        self.wf_b.set_position(min(pos, dur_b if dur_b else pos) / 1000.0)
        if not self._ab_scrubbing:
            self.ab_slider.blockSignals(True)
            self.ab_slider.setValue(ms)
            self.ab_slider.blockSignals(False)
        self.ab_time_lbl.setText(self._fmt_ms(ms) + "  /  " + self._fmt_ms(span))
        self._update_live_meters(ms, span)

    def _ab_slider_released(self):
        self._ab_scrubbing = False
        self._ab_seek_ms(self.ab_slider.value())

    def _on_ab_position(self, pos: int):
        if self._ab_scrubbing:
            return
        self.ab_slider.blockSignals(True)
        self.ab_slider.setValue(pos)
        self.ab_slider.blockSignals(False)
        dur_a = self._dur_a_ms or self._dual.durationA() or 0
        dur_b = self._dur_b_ms or self._dual.durationB() or 0
        span = max(dur_a, dur_b, 1)
        self.ab_time_lbl.setText(self._fmt_ms(pos) + "  /  " + self._fmt_ms(span))
        # Each waveform maps playhead against its own duration (no B stretch)
        self.wf_a.set_position(pos / 1000.0)
        pos_b = min(pos, dur_b) if dur_b > 0 else pos
        self.wf_b.set_position(pos_b / 1000.0)
        self._update_live_meters(pos, span)

    def _on_ab_duration(self, dur: int):
        """Shared duration from DualHiFiPlayer (max of A/B)."""
        self._dur_a_ms = max(0, int(self._dual.durationA() or 0))
        self._dur_b_ms = max(0, int(self._dual.durationB() or 0))
        # If dual only reported a combined duration, fall back to it
        if self._dur_a_ms <= 0 and self._dur_b_ms <= 0 and dur > 0:
            self._dur_a_ms = self._dur_b_ms = int(dur)
        span = max(self._dur_a_ms, self._dur_b_ms, int(dur or 0), 1)
        self.ab_slider.setRange(0, span)
        if self._dur_a_ms > 0:
            self.wf_a.set_duration(self._dur_a_ms / 1000.0)
        if self._dur_b_ms > 0:
            self.wf_b.set_duration(self._dur_b_ms / 1000.0)
        self.ab_time_lbl.setText(
            self._fmt_ms(self._dual.position()) + "  /  " + self._fmt_ms(span)
        )

    def _on_ab_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.btn_ab_play.setText("Pause" if playing else "Play")
        if playing:
            self._sync_timer.start()
        else:
            self._sync_timer.stop()

    def _sync_players(self):
        """UI meter refresh only — DualHiFiPlayer is sample-locked (no hard seeks)."""
        if self._dual.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return
        try:
            pos = int(self._dual.position())
            span = max(self._dur_a_ms, self._dur_b_ms, self._dual.duration(), 1)
            self._update_live_meters(pos, span)
        except Exception:
            pass

    def _prepare_matched_playback_paths(self) -> None:
        """Use original files at native quality (no forced 16-bit/48k re-encode)."""
        self._play_path_a = self._orig_path
        self._play_path_b = self._rep_path

    def _ensure_sources(self):
        """Load native sources into the dual HiFi player."""
        self._prepare_matched_playback_paths()
        if (
            self._play_path_a
            and self._play_path_a.is_file()
            and self._play_path_b
            and self._play_path_b.is_file()
        ):
            self._dual.setSources(
                str(self._play_path_a.resolve()),
                str(self._play_path_b.resolve()),
            )
            self._dur_a_ms = self._dual.durationA()
            self._dur_b_ms = self._dual.durationB()

    def _load_level_envelopes(self):
        """Precompute loudness envelopes so meters track the playhead live."""
        self._env_a = self._envelope_for_path(self._orig_path)
        self._env_b = self._envelope_for_path(self._rep_path)

    @staticmethod
    def _envelope_for_path(path: Path | None, n: int = 256) -> list[float]:
        if not path or not path.is_file():
            return [0.0] * n
        try:
            import soundfile as sf

            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
            mono = np.mean(np.abs(data), axis=1)
            if len(mono) == 0:
                return [0.0] * n
            hop = max(1, len(mono) // n)
            env = []
            for i in range(n):
                chunk = mono[i * hop : (i + 1) * hop]
                env.append(float(np.max(chunk)) if len(chunk) else 0.0)
            mx = max(env) or 1.0
            return [min(1.0, v / mx) for v in env]
        except Exception:
            return [0.0] * n

    def _update_live_meters(self, pos_ms: int, dur_ms: int):
        """Drive LED meters from envelope at playhead; active side is full, other dimmed."""
        if dur_ms <= 0:
            return
        t = max(0.0, min(1.0, pos_ms / max(1, dur_ms)))
        ia = int(t * max(0, len(self._env_a) - 1)) if self._env_a else 0
        ib = int(t * max(0, len(self._env_b) - 1)) if self._env_b else 0
        la = self._env_a[ia] if self._env_a else 0.0
        lb = self._env_b[ib] if self._env_b else 0.0
        # Stereo-ish: slight L/R split for visual interest while still honest
        if self._ab_side == "a":
            self.meter_a.set_levels([la, max(0.0, la * 0.92)])
            self.meter_b.set_levels([lb * 0.25, lb * 0.22])  # dim inactive
        else:
            self.meter_b.set_levels([lb, max(0.0, lb * 0.92)])
            self.meter_a.set_levels([la * 0.25, la * 0.22])

    @staticmethod
    def _fmt_ms(ms: int) -> str:
        ms = max(0, int(ms))
        s, ms_r = divmod(ms, 1000)
        m, s = divmod(s, 60)
        return f"{m}:{s:02d}.{ms_r // 100}"

    def set_labels(self, left: str, right: str):
        """Update column headers (e.g. Your Mix / Reference)."""
        try:
            # score columns store frame; update first label child if present
            for col, text in ((self.col_a, left), (self.col_b, right)):
                frame = col.get("frame")
                if frame is None:
                    continue
                labels = frame.findChildren(QLabel)
                if labels:
                    labels[0].setText(text)
        except Exception:
            pass

    def _wrap_chart(self, title: str, widget: QWidget) -> QFrame:
        f = QFrame()
        f.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        l = QVBoxLayout(f)
        l.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.MD)
        t = QLabel(title)
        t.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.2px; color: {Color.MUTED};"
        )
        l.addWidget(t)
        widget.setMinimumHeight(110)
        l.addWidget(widget)
        return f

    def _score_column(self, title: str) -> dict:
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.MD)
        lay.setSpacing(6)
        h = QLabel(title)
        h.setAlignment(Qt.AlignCenter)
        h.setStyleSheet(
            f"font-size: 10px; font-weight: 600; letter-spacing: 1.5px; color: {Color.MUTED};"
        )
        lay.addWidget(h)
        name = QLabel("-")
        name.setAlignment(Qt.AlignCenter)
        name.setWordWrap(True)
        name.setStyleSheet(f"font-size: {Type.BODY}px; font-weight: 600; color: {Color.TEXT};")
        lay.addWidget(name)
        score = QLabel("-")
        score.setAlignment(Qt.AlignCenter)
        # Mono/sans for digits — display skins (Impact) warp score numerals
        score.setStyleSheet(
            f"font-size: 44px; font-weight: 700; font-family: {Type.MONO}; color: {Color.MUTED};"
        )
        lay.addWidget(score)
        rating = QLabel("")
        rating.setAlignment(Qt.AlignCenter)
        rating.setStyleSheet(f"font-size: 11px; color: {Color.MUTED};")
        lay.addWidget(rating)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(6)
        bar.setStyleSheet(f"""
            QProgressBar {{ background: {Color.LINE}; border: none; border-radius: 3px; }}
            QProgressBar::chunk {{ background: {Color.ACCENT}; border-radius: 3px; }}
        """)
        lay.addWidget(bar)
        summary = QLabel("")
        summary.setWordWrap(True)
        summary.setAlignment(Qt.AlignCenter)
        summary.setStyleSheet(f"font-size: 11px; color: {Color.MUTED};")
        lay.addWidget(summary)
        return {
            "frame": frame,
            "name": name,
            "score": score,
            "rating": rating,
            "bar": bar,
            "summary": summary,
        }

    def _fill_score_col(self, col: dict, report: dict, path: Path | None):
        track = report.get("track") if isinstance(report.get("track"), dict) else {}
        audio = (track or {}).get("audio") or {}
        name = audio.get("file_name") or (path.name if path else "-")
        col["name"].setText(str(name))
        sc = report.get("score")
        if sc is not None:
            col["score"].setText(str(int(sc)))
            c = score_color(int(sc))
            col["score"].setStyleSheet(
                f"font-size: 44px; font-weight: 700; font-family: {Type.MONO}; color: {c};"
            )
            col["bar"].setValue(int(sc))
            col["bar"].setStyleSheet(f"""
                QProgressBar {{ background: {Color.LINE}; border: none; border-radius: 3px; }}
                QProgressBar::chunk {{ background: {c}; border-radius: 3px; }}
            """)
        else:
            col["score"].setText("-")
            col["bar"].setValue(0)
        col["rating"].setText(
            str(report.get("rating") or score_rating(sc if isinstance(sc, int) else None))
        )
        col["summary"].setText(str(report.get("summary") or ""))

    def set_comparison(
        self,
        original_report: dict,
        repaired_report: dict,
        original_path: Path | str | None,
        repaired_path: Path | str | None,
    ):
        self._orig_report = original_report or {}
        self._rep_report = repaired_report or {}

        def _resolve(p: Path | str | None) -> Path | None:
            if not p:
                return None
            try:
                rp = Path(p).expanduser().resolve()
                return rp if rp.is_file() else None
            except Exception:
                return None

        self._orig_path = _resolve(original_path)
        self._rep_path = _resolve(repaired_path)
        # Stop any current A/B audio and rebuild matched PCM play caches
        try:
            self._ab_stop()
        except Exception:
            pass
        self._play_path_a = None
        self._play_path_b = None
        try:
            self._prepare_matched_playback_paths()
            self._ensure_sources()
        except Exception as exc:
            print("A/B matched playback prepare:", exc)

        self._fill_score_col(self.col_a, self._orig_report, self._orig_path)
        self._fill_score_col(self.col_b, self._rep_report, self._rep_path)

        sa = self._orig_report.get("score")
        sb = self._rep_report.get("score")
        if sa is not None and sb is not None:
            try:
                d = int(sb) - int(sa)
                sign = "+" if d >= 0 else ""
                col = Color.SUCCESS if d >= 0 else Color.ERROR
                self.delta_score_lbl.setText(f"Δ score  {sign}{d}")
                self.delta_score_lbl.setStyleSheet(
                    f"font-family: {Type.MONO}; font-size: 14px; font-weight: 700; color: {col};"
                )
            except (TypeError, ValueError):
                self.delta_score_lbl.setText("Δ score  -")
        else:
            self.delta_score_lbl.setText("Δ score  -")

        # Clear B (and A) canvases first so stale pixels never bleed into B
        for c in (self.wf_a, self.wf_b, self.sp_a, self.sp_b, self.sg_a, self.sg_b):
            try:
                c.clear()
            except Exception:
                pass

        # Static average levels + live envelopes for playhead-linked meters
        self.meter_a.set_levels(estimate_channel_levels(self._orig_path))
        self.meter_b.set_levels(estimate_channel_levels(self._rep_path))
        self._load_level_envelopes()
        self._ab_stop()
        self._ensure_sources()
        self._set_ab_side("a")

        # Waveforms / spectrum / spectrogram — identical pipeline both decks
        for path, wf, sp, sg, panel_w, panel_s in (
            (self._orig_path, self.wf_a, self.sp_a, self.sg_a, self.wf_panel_a, self.sp_panel_a),
            (self._rep_path, self.wf_b, self.sp_b, self.sg_b, self.wf_panel_b, self.sp_panel_b),
        ):
            try:
                peaks = load_waveform_peaks(path, 400) if path else []
                if peaks:
                    dur = None
                    try:
                        import soundfile as sf

                        if path and path.is_file():
                            dur = float(sf.info(str(path)).duration)
                    except Exception:
                        dur = None
                    wf.set_peaks(list(peaks), dur)
                    panel_w.set_live(True)
                else:
                    wf.clear()
                    panel_w.set_live(False)
            except Exception as exc:
                print("ab waveform load failed:", path, exc)
                try:
                    wf.clear()
                    panel_w.set_live(False)
                except Exception:
                    pass
            try:
                bands = load_spectrum_bands(path) if path else {}
                if bands and any(v is not None for v in bands.values()):
                    sp.set_bands(dict(bands))
                    panel_s.set_live(True)
                else:
                    sp.clear()
                    panel_s.set_live(False)
            except Exception as exc:
                print("ab spectrum load failed:", path, exc)
                try:
                    sp.clear()
                    panel_s.set_live(False)
                except Exception:
                    pass
            try:
                sg.load_path(path)
            except Exception as exc:
                print("ab spectrogram load failed:", path, exc)
                try:
                    sg.clear()
                except Exception:
                    pass

        # Metric rows
        ta = (
            self._orig_report.get("track")
            if isinstance(self._orig_report.get("track"), dict)
            else {}
        )
        tb = (
            self._rep_report.get("track") if isinstance(self._rep_report.get("track"), dict) else {}
        )
        ma, mb = _metrics(ta), _metrics(tb)
        la, lb = _loud(ta), _loud(tb)
        fa = (
            (_extra(ta).get("technical_faults") or {})
            if isinstance(_extra(ta).get("technical_faults"), dict)
            else {}
        )
        fb = (
            (_extra(tb).get("technical_faults") or {})
            if isinstance(_extra(tb).get("technical_faults"), dict)
            else {}
        )

        pairs = {
            "lufs": (la.get("integrated_lufs"), lb.get("integrated_lufs"), "lufs"),
            "tp": (la.get("true_peak_dbtp"), lb.get("true_peak_dbtp"), "tp"),
            "lra": (la.get("loudness_range_lu"), lb.get("loudness_range_lu"), "lra"),
            "peak": (ma.get("peak_dbfs"), mb.get("peak_dbfs"), "peak"),
            "rms": (ma.get("rms_dbfs"), mb.get("rms_dbfs"), "rms"),
            "crest": (ma.get("crest_factor"), mb.get("crest_factor"), "crest"),
            "dr": (ma.get("dynamic_range_db"), mb.get("dynamic_range_db"), "dr"),
            "width": (ma.get("stereo_width_percent"), mb.get("stereo_width_percent"), "width"),
            "phase": (ma.get("phase_correlation"), mb.get("phase_correlation"), "phase"),
            "noise": (ma.get("noise_floor_dbfs"), mb.get("noise_floor_dbfs"), "noise"),
            "clip": (
                ma.get("clipped_samples_estimate")
                if ma.get("clipped_samples_estimate") is not None
                else fa.get("clipped_samples"),
                mb.get("clipped_samples_estimate")
                if mb.get("clipped_samples_estimate") is not None
                else fb.get("clipped_samples"),
                "clip",
            ),
        }
        for key, (a, b, kind) in pairs.items():
            if key in self.rows:
                self.rows[key].set_pair(a, b, kind)

        self.explain.setText(self._build_explanation(ta, tb, sa, sb))

    def _build_explanation(self, ta, tb, sa, sb) -> str:
        lines = []
        if sa is not None and sb is not None:
            d = int(sb) - int(sa)
            if d > 0:
                lines.append(f"Overall mix score improved by {d} points after repair.")
            elif d < 0:
                lines.append(
                    f"Overall mix score dropped by {abs(d)} points - review metric deltas below."
                )
            else:
                lines.append(
                    "Overall mix score unchanged; inspect peaks and spectral balance for subtler shifts."
                )

        la, lb = _loud(ta), _loud(tb)
        try:
            if la.get("true_peak_dbtp") is not None and lb.get("true_peak_dbtp") is not None:
                if float(lb["true_peak_dbtp"]) < float(la["true_peak_dbtp"]):
                    lines.append(
                        f"True peak reduced ({la['true_peak_dbtp']} → {lb['true_peak_dbtp']} dBTP), "
                        "improving intersample headroom for streaming codecs."
                    )
                elif float(lb["true_peak_dbtp"]) > float(la["true_peak_dbtp"]):
                    lines.append(
                        "True peak increased - verify limiter settings on the repair chain."
                    )
        except Exception:
            pass

        try:
            if la.get("integrated_lufs") is not None and lb.get("integrated_lufs") is not None:
                lines.append(
                    f"Integrated loudness: {la['integrated_lufs']} → {lb['integrated_lufs']} LUFS "
                    "(Spotify/YouTube targets ≈ −14 LUFS; Apple ≈ −16)."
                )
        except Exception:
            pass

        ma, mb = _metrics(ta), _metrics(tb)
        ca = ma.get("clipped_samples_estimate")
        cb = mb.get("clipped_samples_estimate")
        if ca is not None and cb is not None:
            if cb < ca:
                lines.append(f"Clipping estimate improved ({ca} → {cb} samples).")
            elif cb > ca:
                lines.append(f"Clipping estimate rose ({ca} → {cb}) - listen for digital edge.")

        lines.append(
            "Waveforms show envelope energy over time; spectrum bars are relative band energy "
            "(SUB→AIR). Mel spectrograms (librosa) reveal time-frequency density - brighter regions "
            "are hotter. LED meters estimate L/R RMS loudness (green safe → yellow hot → red clip risk)."
        )
        lines.append(
            "Use Studio Player from the repair dialog to solo the repaired file, scrub, select, and trim."
        )
        return "\n\n".join(lines)

    def clear(self):
        self.set_comparison({}, {}, None, None)
