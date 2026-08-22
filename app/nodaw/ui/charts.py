"""
High-quality waveform + spectrum + spectrogram canvases for Home / Report.

- Visible empty state ("-") before analysis
- Filled from report peaks / spectral dict, or computed from file via soundfile/librosa
- HD mel spectrogram (accurate STFT → mel → dB) with Balance ↔ Spectrogram toggle
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QPoint, QPointF, QRectF, Qt, QThread, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .icons import IconWidget
from .metric_status import metric_status, value_color
from .theme import Color, Radius, Space, Type

# ---------------------------------------------------------------------------
# HD spectrogram generation (shared by Home, Report, A/B)
# ---------------------------------------------------------------------------


def _heat_lut() -> np.ndarray:
    """256×3 turbo/magma hybrid LUT for spectrogram display (uint8 RGB)."""
    lut = np.zeros((256, 3), dtype=np.uint8)
    for i in range(256):
        t = i / 255.0
        # dark navy → indigo → magenta → orange → yellow → white
        if t < 0.15:
            u = t / 0.15
            r, g, b = int(8 + u * 30), int(4 + u * 10), int(28 + u * 90)
        elif t < 0.35:
            u = (t - 0.15) / 0.20
            r, g, b = int(38 + u * 90), int(14 + u * 20), int(118 + u * 70)
        elif t < 0.55:
            u = (t - 0.35) / 0.20
            r, g, b = int(128 + u * 90), int(34 + u * 40), int(188 - u * 40)
        elif t < 0.72:
            u = (t - 0.55) / 0.17
            r, g, b = int(218 + u * 30), int(74 + u * 100), int(148 - u * 100)
        elif t < 0.88:
            u = (t - 0.72) / 0.16
            r, g, b = 248, int(174 + u * 60), int(48 + u * 40)
        else:
            u = (t - 0.88) / 0.12
            r, g, b = int(248 + u * 7), int(234 + u * 21), int(88 + u * 160)
        lut[i] = (min(255, r), min(255, g), min(255, b))
    return lut


_HEAT_LUT = _heat_lut()


def compute_hd_spectrogram(
    path: Path | str | None,
    *,
    target_sr: int = 44100,
    n_mels: int = 256,
    n_fft: int = 4096,
    time_bins: int = 1600,
    fmin: float = 20.0,
    fmax: float | None = 20000.0,
    max_duration_s: float | None = 720.0,
    top_db: float = 80.0,
) -> tuple[QImage | None, dict[str, Any]]:
    """
    High-definition mel spectrogram for an audio file.

    Uses full-resolution decode (soundfile when possible), resamples to
    target_sr, STFT n_fft=4096, 256 mel bands, 80 dB dynamic range.
    Returns (QImage RGB888 or None, metadata dict).
    """
    meta: dict[str, Any] = {
        "path": str(path) if path else None,
        "duration_s": 0.0,
        "sr": target_sr,
        "n_mels": n_mels,
        "n_fft": n_fft,
        "fmin": fmin,
        "fmax": fmax,
        "top_db": top_db,
        "error": None,
    }
    if not path:
        meta["error"] = "no path"
        return None, meta
    p = Path(path)
    if not p.is_file():
        meta["error"] = "missing file"
        return None, meta

    try:
        import librosa
    except Exception as exc:
        meta["error"] = f"librosa: {exc}"
        return None, meta

    try:
        y: np.ndarray | None = None
        sr = target_sr
        # Prefer soundfile for bit-accurate PCM decode
        try:
            import soundfile as sf

            info = sf.info(str(p))
            sr0 = int(info.samplerate) or target_sr
            frames = int(info.frames) if info.frames else 0
            stop = None
            if max_duration_s and frames > 0 and frames / max(sr0, 1) > max_duration_s:
                stop = int(max_duration_s * sr0)
            data, sr0 = sf.read(str(p), dtype="float32", always_2d=True, stop=stop)
            if data.size == 0:
                meta["error"] = "empty audio"
                return None, meta
            y = data.mean(axis=1).astype(np.float32, copy=False)
            sr = int(sr0)
        except Exception:
            y = None

        if y is None:
            dur = max_duration_s if max_duration_s else None
            y, sr = librosa.load(str(p), sr=target_sr, mono=True, duration=dur)
            y = y.astype(np.float32, copy=False)

        if y is None or len(y) < 512:
            meta["error"] = "too short"
            return None, meta

        if max_duration_s and len(y) / max(sr, 1) > max_duration_s:
            y = y[: int(max_duration_s * sr)]

        # Resample to analysis rate for consistent mel mapping
        if int(sr) != int(target_sr):
            y = librosa.resample(y, orig_sr=sr, target_sr=target_sr)
            sr = target_sr

        duration_s = float(len(y) / max(sr, 1))
        meta["duration_s"] = round(duration_s, 3)
        meta["sr"] = sr

        nyquist = sr // 2
        fhi = float(fmax) if fmax else float(min(20000, nyquist - 1))
        fhi = min(fhi, float(nyquist - 1))
        flo = float(max(1.0, fmin))
        if flo >= fhi:
            flo = 20.0
            fhi = float(min(20000, nyquist - 1))
        meta["fmin"] = flo
        meta["fmax"] = fhi

        # Linear hop for target frame count — avoid power-of-two rounding that
        # warps time scale differently per track (looked like B-side artifacts).
        hop = max(64, int(round(len(y) / max(64, time_bins))))
        hop = max(64, min(hop, max(64, n_fft // 2)))
        meta["hop_length"] = hop

        S = librosa.feature.melspectrogram(
            y=y,
            sr=sr,
            n_fft=n_fft,
            hop_length=hop,
            n_mels=n_mels,
            fmin=flo,
            fmax=fhi,
            power=2.0,
            # center=False reduces edge padding bars that read as “glitch” edges
            center=False,
        )
        S_db = librosa.power_to_db(S, ref=np.max, top_db=top_db)
        # Fixed dynamic range for accurate comparison across tracks
        S_db = np.clip(S_db.astype(np.float32), -float(top_db), 0.0)
        # Mild temporal smooth (3-frame) kills STFT hash without blurring structure
        if S_db.shape[1] >= 3:
            pad = np.pad(S_db, ((0, 0), (1, 1)), mode="edge")
            S_db = (
                0.25 * pad[:, :-2] + 0.5 * pad[:, 1:-1] + 0.25 * pad[:, 2:]
            ).astype(np.float32)
        norm = (S_db + float(top_db)) / float(top_db)
        norm = np.clip(norm, 0.0, 1.0)
        # Low frequencies at bottom (music standard)
        norm = np.flipud(norm)

        idx = (norm * 255.0).astype(np.uint8)
        rgb = np.ascontiguousarray(_HEAT_LUT[idx], dtype=np.uint8)
        h, w, _ = rgb.shape
        if h < 2 or w < 2:
            meta["error"] = "empty spectrogram"
            return None, meta
        meta["shape"] = (int(h), int(w))
        # Own buffer copy — never share numpy memory with QImage (B-side glitches)
        img = QImage(rgb.data, int(w), int(h), int(rgb.strides[0]), QImage.Format.Format_RGB888).copy()
        return img, meta
    except Exception as exc:
        meta["error"] = str(exc)
        return None, meta


def compute_spectral_image(
    path: Path | str | None,
    *,
    target_sr: int = 22050,
    n_fft: int = 2048,
    time_bins: int = 1200,
    n_freq_bins: int = 256,
    max_duration_s: float = 720.0,
) -> tuple[QImage | None, dict[str, Any]]:
    """
    Stereo spectral image: time × frequency.
    Color = L/R balance (theme soft=left, accent=center, dim=right).
    Brightness = energy. Follows active skin colors.
    """
    meta: dict[str, Any] = {
        "path": str(path) if path else None,
        "duration_s": 0.0,
        "kind": "spectral_image",
        "error": None,
    }
    if not path or not Path(path).is_file():
        meta["error"] = "missing file"
        return None, meta
    try:
        import librosa
        import soundfile as sf
    except Exception as exc:
        meta["error"] = str(exc)
        return None, meta
    try:
        p = Path(path)
        data, sr0 = sf.read(str(p), dtype="float32", always_2d=True)
        if data.size == 0:
            meta["error"] = "empty"
            return None, meta
        if data.shape[1] == 1:
            L = R = data[:, 0]
        else:
            L, R = data[:, 0], data[:, 1]
        sr = int(sr0)
        max_n = int(max_duration_s * sr) if max_duration_s else None
        if max_n and len(L) > max_n:
            L, R = L[:max_n], R[:max_n]
        if sr != target_sr:
            L = librosa.resample(L, orig_sr=sr, target_sr=target_sr)
            R = librosa.resample(R, orig_sr=sr, target_sr=target_sr)
            sr = target_sr
        meta["duration_s"] = round(float(len(L) / max(sr, 1)), 3)
        meta["sr"] = sr
        hop = max(64, int(round(len(L) / max(64, time_bins))))
        hop = max(64, min(hop, n_fft // 2))
        SL = np.abs(librosa.stft(L, n_fft=n_fft, hop_length=hop, center=False))
        SR = np.abs(librosa.stft(R, n_fft=n_fft, hop_length=hop, center=False))
        n_full = SL.shape[0]
        if n_full > n_freq_bins:
            edges = np.linspace(0, n_full, n_freq_bins + 1).astype(int)

            def pool(S: np.ndarray) -> np.ndarray:
                rows = []
                for a, b in zip(edges[:-1], edges[1:]):
                    rows.append(S[a:b].mean(axis=0) if b > a else S[min(a, S.shape[0] - 1)])
                return np.stack(rows, axis=0)

            SL, SR = pool(SL), pool(SR)
        eps = 1e-8
        energy = SL + SR + eps
        balance = (SR - SL) / energy
        power_db = 20.0 * np.log10(np.maximum(energy, eps))
        power_db = power_db - float(np.max(power_db))
        power_db = np.clip(power_db, -70.0, 0.0)
        bright = (power_db + 70.0) / 70.0
        balance = np.flipud(balance)
        bright = np.flipud(bright)
        left_c = QColor(Color.ACCENT_SOFT)
        mid_c = QColor(Color.ACCENT)
        right_c = QColor(Color.ACCENT_DIM)
        bg_c = QColor(Color.BG)
        h, w = balance.shape
        # Vectorized blend for speed
        b = np.clip(balance, -1.0, 1.0)
        v = np.clip(bright, 0.0, 1.0)
        # left mix when b<0, right when b>0
        t = np.abs(b)
        use_left = b < 0
        r = np.where(
            use_left,
            mid_c.red() * (1 - t) + left_c.red() * t,
            mid_c.red() * (1 - t) + right_c.red() * t,
        )
        g = np.where(
            use_left,
            mid_c.green() * (1 - t) + left_c.green() * t,
            mid_c.green() * (1 - t) + right_c.green() * t,
        )
        bl = np.where(
            use_left,
            mid_c.blue() * (1 - t) + left_c.blue() * t,
            mid_c.blue() * (1 - t) + right_c.blue() * t,
        )
        r = bg_c.red() * (1 - v) + r * v
        g = bg_c.green() * (1 - v) + g * v
        bl = bg_c.blue() * (1 - v) + bl * v
        rgb = np.stack([r, g, bl], axis=-1).astype(np.uint8)
        rgb = np.ascontiguousarray(rgb)
        img = QImage(
            rgb.data, int(w), int(h), int(rgb.strides[0]), QImage.Format.Format_RGB888
        ).copy()
        meta["shape"] = (int(h), int(w))
        meta["fmin"] = 20.0
        meta["fmax"] = float(sr // 2)
        return img, meta
    except Exception as exc:
        meta["error"] = str(exc)
        return None, meta


class _SpectrogramWorker(QObject):
    """Background HD spectrogram or spectral-image job."""

    finished = Signal(str, object, object, str)  # path, QImage|None, meta, kind

    def run(self, path: str, opts: dict | None = None, kind: str = "spectrogram"):
        opts = opts or {}
        if kind == "image":
            img, meta = compute_spectral_image(path)
        else:
            img, meta = compute_hd_spectrogram(path, **opts)
        self.finished.emit(path, img, meta, kind)


def load_waveform_peaks(path: Path | str | None, n_bins: int = 400) -> list[float]:
    """
    Peak envelope for display.

    Evenly partitions the full file into n_bins (no zero-pad tail, which used
    to create flat dead zones / visual artifacts on deck B when length % bins != 0).
    Soft-clips extreme spikes so one click does not flatten the rest of the wave.
    """
    if not path:
        return []
    p = Path(path)
    if not p.is_file():
        return []
    n_bins = max(32, int(n_bins))

    def _normalize(peaks: list[float]) -> list[float]:
        if not peaks:
            return []
        arr = np.asarray(peaks, dtype=np.float64)
        # Soft ceiling: ignore pathological single-sample spikes
        if arr.size >= 8:
            ceiling = float(np.percentile(arr, 99.5))
            if ceiling > 1e-9:
                arr = np.minimum(arr, ceiling * 1.05)
        mx = float(np.max(arr)) or 1.0
        return [float(x / mx) for x in arr]

    def _peaks_from_mono(mono: np.ndarray) -> list[float]:
        if mono.size == 0:
            return []
        # Even bins across entire length (handles remainder without zero tail)
        edges = np.linspace(0, mono.size, n_bins + 1, dtype=np.int64)
        peaks: list[float] = []
        for i in range(n_bins):
            a, b = int(edges[i]), int(edges[i + 1])
            if b <= a:
                b = min(mono.size, a + 1)
            chunk = mono[a:b]
            peaks.append(float(np.max(np.abs(chunk))) if chunk.size else 0.0)
        return _normalize(peaks)

    try:
        import soundfile as sf

        data, _sr = sf.read(str(p), dtype="float32", always_2d=True)
        if data.size == 0:
            return []
        mono = np.mean(data, axis=1)
        return _peaks_from_mono(mono)
    except Exception:
        try:
            import librosa

            y, _ = librosa.load(str(p), sr=22050, mono=True)
            return _peaks_from_mono(np.asarray(y, dtype=np.float32))
        except Exception:
            return []


def load_spectrum_bands(path: Path | str | None) -> dict[str, float]:
    """7-band energy relative dB for spectrum bars (presentation-only)."""
    bands = {
        "SUB": (20, 60),
        "BASS": (60, 150),
        "LOW MID": (150, 400),
        "MID": (400, 1200),
        "PRES": (1200, 4000),
        "HIGH": (4000, 10000),
        "AIR": (10000, 18000),
    }
    if not path:
        return dict.fromkeys(bands)  # type: ignore
    p = Path(path)
    if not p.is_file():
        return dict.fromkeys(bands)  # type: ignore
    try:
        import librosa

        y, sr = librosa.load(str(p), sr=22050, mono=True, duration=90)
        if len(y) < 512:
            return dict.fromkeys(bands)  # type: ignore
        S = np.abs(librosa.stft(y, n_fft=2048))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
        power = (S**2).mean(axis=1)
        out: dict[str, float] = {}
        for name, (lo, hi) in bands.items():
            mask = (freqs >= lo) & (freqs < hi)
            if not np.any(mask):
                out[name] = -80.0
                continue
            band_p = float(np.mean(power[mask]))
            out[name] = float(10 * np.log10(max(band_p, 1e-12)))
        # normalize relative to max band
        mx = max(out.values()) if out else 0.0
        return {k: round(v - mx, 1) for k, v in out.items()}
    except Exception:
        return dict.fromkeys(bands)  # type: ignore


class WaveformCanvas(QWidget):
    """Waveform with optional studio interaction: click-seek, playhead, lookahead.

    Modes:
      progress_reveal (default for Home): body starts blackish; as playhead
        advances, 0→playhead fills with cyan→pink-purple gradient.
      solid_selected (A/B): full secondary fill when set_selected(True).

    Click emits activated + seekRequested so decks can swap at the same playhead.
    """

    seekRequested = Signal(float)  # seconds
    activated = Signal()          # this deck was clicked / selected

    def __init__(
        self,
        parent=None,
        interactive: bool = False,
        progress_reveal: bool = True,
        *,
        wheel_seeks: bool = True,
        always_colored: bool = False,
    ):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._peaks: list[float] = []
        self._empty = True
        self._interactive = interactive
        self._progress_reveal = bool(progress_reveal)
        # When False (A/B page), wheel scrolls the report — does NOT seek audio
        self._wheel_seeks = bool(wheel_seeks)
        # When True (A/B decks), always paint full cyan→purple (not grey when unselected)
        self._always_colored = bool(always_colored)
        self._duration = 0.0
        self._position = 0.0
        self._lookahead = 0.35
        self._hover_x: float | None = None
        self._selected = False
        if interactive:
            self.setMouseTracking(True)
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def clear(self):
        self._peaks = []
        self._empty = True
        self._duration = 0.0
        self._position = 0.0
        self.update()

    def set_peaks(self, peaks: list[float] | None, duration: float | None = None):
        self._peaks = list(peaks or [])
        self._empty = not bool(self._peaks)
        if duration is not None:
            self._duration = max(0.0, float(duration))
        elif self._peaks and self._duration <= 0:
            self._duration = 1.0  # unit length until real duration arrives
        self.update()

    def set_duration(self, seconds: float):
        self._duration = max(0.0, float(seconds))
        self.update()

    def set_position(self, seconds: float):
        self._position = max(0.0, min(float(seconds), self._duration or 0.0))
        self.update()

    def set_lookahead(self, seconds: float):
        self._lookahead = max(0.0, float(seconds))
        self.update()

    def set_selected(self, selected: bool):
        """Highlight this deck (secondary color fill inside the waveform)."""
        sel = bool(selected)
        if sel == self._selected:
            return
        self._selected = sel
        self.update()

    def set_progress_reveal(self, enabled: bool):
        """Home-style: unplayed = blackish, played = cyan→purple gradient."""
        self._progress_reveal = bool(enabled)
        self.update()

    def is_selected(self) -> bool:
        return self._selected

    @staticmethod
    def _theme_stops() -> tuple[str, str, str]:
        """Live skin stops (soft → accent → dim)."""
        return Color.wave_stops()

    def _x_to_time(self, x: float) -> float:
        if not self._duration:
            return 0.0
        return max(0.0, min(self._duration, (x / max(1, self.width())) * self._duration))

    def _time_to_x(self, t: float) -> float:
        if not self._duration:
            return 0.0
        return (t / self._duration) * self.width()

    def _build_wave_paths(self, w: float, mid: float, amp: float):
        n = len(self._peaks)
        path_t = QPainterPath()
        path_b = QPainterPath()
        path_t.moveTo(0, mid)
        path_b.moveTo(0, mid)
        for i, pk in enumerate(self._peaks):
            x = (i / max(1, n - 1)) * (w - 1)
            y = abs(pk) * amp
            path_t.lineTo(x, mid - y)
            path_b.lineTo(x, mid + y)
        path_t.lineTo(w - 1, mid)
        path_b.lineTo(w - 1, mid)
        path_t.closeSubpath()
        path_b.closeSubpath()
        return path_t, path_b

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        mid = h / 2.0
        sel = self._selected
        # Home: progress reveal. A/B (always_colored): never grey out unselected deck.
        reveal = self._progress_reveal and not self._always_colored and not sel

        # Background
        bg = QLinearGradient(0, 0, 0, h)
        if sel:
            bg.setColorAt(0, QColor(Color.with_alpha(Color.ACCENT_SOFT, 0.10)))
            bg.setColorAt(1, QColor(Color.SURFACE))
        else:
            bg.setColorAt(0, QColor("#050508"))
            bg.setColorAt(1, QColor(Color.SURFACE if Color.SURFACE else "#0c0c10"))
        p.fillRect(self.rect(), bg)

        # Border
        if sel:
            p.setPen(QPen(QColor(Color.ACCENT_SOFT), 2.0))
        else:
            p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.9)), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)

        p.setPen(QPen(QColor(Color.with_alpha(Color.LINE, 0.55)), 1))
        for frac in (0.25, 0.5, 0.75):
            p.drawLine(0, int(h * frac), w, int(h * frac))

        if self._empty or not self._peaks:
            p.setPen(QColor(Color.MUTED))
            font = QFont()
            font.setPointSize(11)
            font.setBold(True)
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignCenter, "-")
            p.end()
            return

        n = len(self._peaks)
        amp = h * 0.42
        path_t, path_b = self._build_wave_paths(w, mid, amp)

        if reveal:
            # --- Unplayed / full body: blackish silhouette ---
            dim = QColor("#1a1a22")
            dim.setAlpha(220)
            edge_dim = QColor("#2a2a35")
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(dim)
            p.drawPath(path_t)
            p.drawPath(path_b)
            p.setPen(QPen(edge_dim, 1.0))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(n - 1):
                x0 = (i / max(1, n - 1)) * (w - 1)
                x1 = ((i + 1) / max(1, n - 1)) * (w - 1)
                p.drawLine(
                    QPointF(x0, mid - abs(self._peaks[i]) * amp),
                    QPointF(x1, mid - abs(self._peaks[i + 1]) * amp),
                )

            # --- Played region 0 → playhead: cyan → pink-purple gradient ---
            if self._duration > 0 and self._position > 0.001:
                x_end = self._time_to_x(self._position)
                p.save()
                p.setClipRect(0, 0, max(1, int(x_end) + 1), h)
                # Horizontal gradient along the song — theme soft → accent → dim
                c0, c1, c2 = self._theme_stops()
                hgrad = QLinearGradient(0, 0, w, 0)
                hgrad.setColorAt(0.0, QColor(c0))
                hgrad.setColorAt(0.5, QColor(c1))
                hgrad.setColorAt(1.0, QColor(c2))
                # Soft vertical depth
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(hgrad)
                p.setOpacity(0.88)
                p.drawPath(path_t)
                p.drawPath(path_b)
                p.setOpacity(1.0)
                # Bright edge on played crest
                p.setPen(QPen(QColor(Color.wave_edge()), 1.15))
                p.setBrush(Qt.BrushStyle.NoBrush)
                last_i = max(1, int((self._position / self._duration) * (n - 1)))
                for i in range(min(last_i, n - 1)):
                    x0 = (i / max(1, n - 1)) * (w - 1)
                    x1 = ((i + 1) / max(1, n - 1)) * (w - 1)
                    p.drawLine(
                        QPointF(x0, mid - abs(self._peaks[i]) * amp),
                        QPointF(x1, mid - abs(self._peaks[i + 1]) * amp),
                    )
                p.restore()

            # Lookahead tip glow (advancing “frontier”)
            if self._duration > 0 and self._lookahead > 0:
                x_ph = self._time_to_x(self._position)
                x_la = self._time_to_x(min(self._duration, self._position + self._lookahead))
                if x_la > x_ph:
                    c0, c1, _ = self._theme_stops()
                    tip = QLinearGradient(x_ph, 0, x_la, 0)
                    tip.setColorAt(0, QColor(Color.with_alpha(c0, 0.35)))
                    tip.setColorAt(1, QColor(Color.with_alpha(c1, 0.02)))
                    p.fillRect(int(x_ph), 0, max(1, int(x_la - x_ph)), h, tip)
        else:
            # Solid fill — A/B always theme gradient; selection only thickens ring
            c0, c1, c2 = self._theme_stops()
            if self._always_colored or sel:
                glow = QColor(c2)
                glow.setAlpha(55)
                hgrad = QLinearGradient(0, 0, w, 0)
                hgrad.setColorAt(0.0, QColor(c0))
                hgrad.setColorAt(0.5, QColor(c1))
                hgrad.setColorAt(1.0, QColor(c2))
                edge = QColor(Color.wave_edge())
                edge_w = 1.35 if sel else 1.1
            else:
                glow = QColor(Color.MUTED)
                glow.setAlpha(28)
                hgrad = QLinearGradient(0, 0, 0, h)
                hgrad.setColorAt(0, QColor(Color.with_alpha(Color.MUTED, 0.45)))
                hgrad.setColorAt(0.5, QColor(Color.with_alpha(Color.ACCENT_DIM, 0.28)))
                hgrad.setColorAt(1, QColor(Color.with_alpha(Color.MUTED, 0.4)))
                edge = QColor(Color.with_alpha(Color.MUTED, 0.65))
                edge_w = 1.0

            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawPath(path_t)
            p.drawPath(path_b)

            p.setOpacity(0.9 if (self._always_colored or sel) else 1.0)
            p.setBrush(hgrad)
            p.drawPath(path_t)
            p.drawPath(path_b)
            p.setOpacity(1.0)

            p.setPen(QPen(edge, edge_w))
            p.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(n - 1):
                x0 = (i / max(1, n - 1)) * (w - 1)
                x1 = ((i + 1) / max(1, n - 1)) * (w - 1)
                p.drawLine(
                    QPointF(x0, mid - abs(self._peaks[i]) * amp),
                    QPointF(x1, mid - abs(self._peaks[i + 1]) * amp),
                )

            if self._duration > 0 and self._lookahead > 0:
                x_ph = self._time_to_x(self._position)
                x_la = self._time_to_x(min(self._duration, self._position + self._lookahead))
                if x_la > x_ph:
                    grad = QLinearGradient(x_ph, 0, x_la, 0)
                    la_c = c0 if (self._always_colored or sel) else Color.ACCENT
                    grad.setColorAt(0, QColor(Color.with_alpha(la_c, 0.30 if sel else 0.18)))
                    grad.setColorAt(1, QColor(Color.with_alpha(c1, 0.02)))
                    p.fillRect(int(x_ph), 0, max(1, int(x_la - x_ph)), h, grad)

        p.setPen(QPen(QColor(Color.with_alpha(Color.MUTED, 0.4)), 1))
        p.drawLine(0, int(mid), w, int(mid))

        # Playhead
        if self._duration > 0:
            x = int(self._time_to_x(self._position))
            p.setPen(QPen(QColor(Color.WHITE), 2))
            p.drawLine(x, 0, x, h)
            c0, c1, _ = self._theme_stops()
            tip_c = QColor(
                c0 if (reveal or self._always_colored or sel) else Color.ACCENT
            )
            p.setBrush(tip_c)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(x - 4, 2, 8, 8)

        if self._interactive and self._hover_x is not None:
            p.setPen(QPen(QColor(Color.with_alpha(Color.MUTED, 0.5)), 1, Qt.PenStyle.DashLine))
            p.drawLine(int(self._hover_x), 0, int(self._hover_x), h)

        p.end()

    def mousePressEvent(self, event: QMouseEvent):
        if self._interactive and event.button() == Qt.MouseButton.LeftButton:
            # Always activate this deck (instant A/B switch for parent)
            self.activated.emit()
            if self._duration > 0:
                t = self._x_to_time(event.position().x())
                self.seekRequested.emit(t)
                self.set_position(t)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._interactive:
            self._hover_x = event.position().x()
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._hover_x = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        """
        Default: wheel seeks (Home).
        A/B pages set wheel_seeks=False so scrolling the report does NOT skip audio.
        Ctrl+wheel still seeks when wheel_seeks is False (optional fine scrub).
        """
        if not self._interactive or self._duration <= 0:
            super().wheelEvent(event)
            return

        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if self._wheel_seeks or ctrl:
            delta = event.angleDelta().y()
            step = self._duration * 0.01 * (-1 if delta < 0 else 1)
            t = max(0.0, min(self._duration, self._position + step))
            self.seekRequested.emit(t)
            self.set_position(t)
            event.accept()
            return

        # Let parent QScrollArea scroll the A/B report — do not touch playhead
        event.ignore()


class HomeWaveformPanel(QFrame):
    """
    Main-page waveform with studio basics:
    click-seek, lookahead, play/pause/stop/rewind, open full Studio editor.
    """

    openEditorRequested = Signal()
    playToggled = Signal()
    stopRequested = Signal()
    rewindRequested = Signal()
    seekRequested = Signal(float)
    lookaheadChanged = Signal(float)
    eqApplyRequested = Signal(float, float, float)  # low, mid, high dB (0,0,0 if power off)
    eqDownloadRequested = Signal(float, float, float)  # export EQ'd file

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HomeWaveformPanel")
        self.setStyleSheet(f"""
            QFrame#HomeWaveformPanel {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        self.setMinimumHeight(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.MD)
        lay.setSpacing(Space.SM)

        hdr = QHBoxLayout()
        title = QLabel("WAVEFORM  ·  black → cyan/purple as playhead advances  ·  click to seek")
        title.setStyleSheet(
            f"font-size: {Type.TINY}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"letter-spacing: 1.2px; color: {Color.MUTED}; background: transparent;"
        )
        hdr.addWidget(title)
        hdr.addStretch()

        la = QLabel("Lookahead")
        la.setStyleSheet(f"font-size: 10px; color: {Color.MUTED}; background: transparent;")
        hdr.addWidget(la)
        self.lookahead_spin = QDoubleSpinBox()
        self.lookahead_spin.setRange(0.0, 5.0)
        self.lookahead_spin.setSingleStep(0.05)
        self.lookahead_spin.setDecimals(2)
        self.lookahead_spin.setSuffix(" s")
        self.lookahead_spin.setValue(0.35)
        self.lookahead_spin.setFixedWidth(88)
        self.lookahead_spin.setToolTip("Glow region ahead of the playhead (visual lead-in).")
        self.lookahead_spin.valueChanged.connect(self._on_lookahead)
        hdr.addWidget(self.lookahead_spin)

        self._badge = QLabel("STANDBY")
        self._badge.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
            f"color: {Color.MUTED}; background: {Color.with_alpha(Color.MUTED, 0.12)}; "
            f"border-radius: 4px; padding: 2px 6px;"
        )
        hdr.addWidget(self._badge)
        lay.addLayout(hdr)

        self.canvas = WaveformCanvas(interactive=True, progress_reveal=True)
        self.canvas.setMinimumHeight(120)
        self.canvas.seekRequested.connect(self.seekRequested.emit)
        lay.addWidget(self.canvas, 1)

        # Transport
        transport = QHBoxLayout()
        transport.setSpacing(Space.SM)
        transport.addStretch()

        def tbtn(icon: str, tip: str, slot, primary: bool = False) -> QToolButton:
            b = QToolButton()
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(40, 40)
            if primary:
                b.setFixedSize(48, 48)
                b.setStyleSheet(f"""
                    QToolButton {{
                        background: {Color.ACCENT};
                        border: none;
                        border-radius: 24px;
                    }}
                    QToolButton:hover {{ background: {Color.ACCENT_SOFT}; }}
                """)
                color = Color.BG
            else:
                b.setStyleSheet(f"""
                    QToolButton {{
                        background: {Color.SURFACE};
                        border: 1px solid {Color.LINE};
                        border-radius: 10px;
                    }}
                    QToolButton:hover {{
                        border-color: {Color.ACCENT};
                        background: {Color.HOVER};
                    }}
                """)
                color = Color.TEXT
            wrap = QVBoxLayout(b)
            wrap.setContentsMargins(0, 0, 0, 0)
            wrap.setAlignment(Qt.AlignCenter)
            ico = IconWidget(icon, size=18 if not primary else 20, color=color)
            wrap.addWidget(ico, 0, Qt.AlignCenter)
            b._icon = ico
            b.clicked.connect(slot)
            return b

        self.btn_rew = tbtn("rewind", "Rewind to start", self.rewindRequested.emit)
        self.btn_stop = tbtn("stop", "Stop", self.stopRequested.emit)
        self.btn_play = tbtn("play", "Play / Pause", self.playToggled.emit, primary=True)
        transport.addWidget(self.btn_rew)
        transport.addWidget(self.btn_stop)
        transport.addWidget(self.btn_play)
        transport.addSpacing(12)

        self.btn_editor = QPushButton("Open Studio Editor")
        self.btn_editor.setCursor(Qt.PointingHandCursor)
        self.btn_editor.setToolTip(
            "Open the full Studio Player modal (trim, convert, selection, specs)."
        )
        self.btn_editor.setStyleSheet(f"""
            QPushButton {{
                background: {Color.SURFACE};
                border: 1px solid {Color.LINE};
                border-radius: 8px;
                padding: 8px 14px;
                color: {Color.TEXT};
                font-weight: 600;
                font-size: 12px;
            }}
            QPushButton:hover {{
                border-color: {Color.ACCENT};
                color: {Color.WHITE};
            }}
        """)
        self.btn_editor.clicked.connect(self.openEditorRequested.emit)
        transport.addWidget(self.btn_editor)
        transport.addStretch()
        lay.addLayout(transport)

        # Creative LOW / MID / HIGH tone sculpt knobs
        try:
            from .eq_knobs import CreativeEqStrip

            self.eq_strip = CreativeEqStrip(compact=True)

            def _emit_apply():
                if self.eq_strip.is_powered():
                    self.eqApplyRequested.emit(*self.eq_strip.gains())
                else:
                    self.eqApplyRequested.emit(0.0, 0.0, 0.0)

            self.eq_strip.applyRequested.connect(_emit_apply)
            self.eq_strip.downloadRequested.connect(
                lambda: self.eqDownloadRequested.emit(*self.eq_strip.gains())
            )
            lay.addWidget(self.eq_strip)
        except Exception as exc:
            print("eq strip:", exc)
            self.eq_strip = None

        self._time = QLabel("0:00  /  0:00")
        self._time.setAlignment(Qt.AlignCenter)
        self._time.setStyleSheet(
            f"font-family: {Type.MONO}; font-size: 11px; color: {Color.MUTED}; background: transparent;"
        )
        lay.addWidget(self._time)

    def _on_lookahead(self, v: float):
        self.canvas.set_lookahead(v)
        self.lookaheadChanged.emit(v)

    def set_live(self, live: bool):
        if live:
            self._badge.setText("LIVE")
            self._badge.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
                f"color: {Color.ACCENT}; background: {Color.with_alpha(Color.ACCENT, 0.12)}; "
                f"border-radius: 4px; padding: 2px 6px;"
            )
        else:
            self._badge.setText("STANDBY")
            self._badge.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
                f"color: {Color.MUTED}; background: {Color.with_alpha(Color.MUTED, 0.12)}; "
                f"border-radius: 4px; padding: 2px 6px;"
            )

    def set_peaks(self, peaks: list[float] | None, duration: float | None = None):
        self.canvas.set_peaks(peaks, duration)

    def set_position_ms(self, ms: int, duration_ms: int = 0):
        sec = ms / 1000.0
        self.canvas.set_position(sec)
        if duration_ms > 0:
            self.canvas.set_duration(duration_ms / 1000.0)
            self._time.setText(self._fmt(ms) + "  /  " + self._fmt(duration_ms))
        else:
            self._time.setText(self._fmt(ms) + "  /  -")

    def set_playing(self, playing: bool):
        self.btn_play._icon.set_name("pause" if playing else "play")
        self.btn_play._icon.set_color(Color.BG)

    def clear(self):
        self.canvas.clear()
        self.set_live(False)
        self._time.setText("0:00  /  0:00")
        self.set_playing(False)

    @staticmethod
    def _fmt(ms: int) -> str:
        ms = max(0, int(ms))
        s = ms // 1000
        m, s = divmod(s, 60)
        return f"{m}:{s:02d}"


class SpectrumCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._bands: dict[str, float | None] = {}
        self._empty = True

    def clear(self):
        self._bands = {}
        self._empty = True
        self.update()

    def set_bands(self, bands: dict[str, float | None] | None):
        self._bands = dict(bands or {})
        self._empty = not any(v is not None for v in self._bands.values())
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor(Color.BG))
        bg.setColorAt(1, QColor(Color.SURFACE))
        p.fillRect(self.rect(), bg)

        if self._empty or not self._bands:
            p.setPen(QColor(Color.MUTED))
            font = QFont()
            font.setPointSize(11)
            font.setBold(True)
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignCenter, "-")
            p.end()
            return

        names = list(self._bands.keys())
        vals = [self._bands[n] for n in names]
        finite = [v for v in vals if v is not None]
        if not finite:
            p.setPen(QColor(Color.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "-")
            p.end()
            return

        # map dB relative (usually 0 to -40) to bar height
        lo = min(finite)
        hi = max(finite)
        span = max(6.0, hi - lo)

        n = len(names)
        pad = 10
        gap = 6
        usable = w - pad * 2 - gap * (n - 1)
        bw = usable / max(1, n)
        base = h - 22
        top = 10
        max_h = base - top

        for i, name in enumerate(names):
            v = vals[i]
            x = pad + i * (bw + gap)
            if v is None:
                bar_h = 4
                col = QColor(Color.MUTED)
            else:
                norm = (v - lo) / span
                bar_h = max(4, int(norm * max_h))
                # color by relative energy: hot highs get fair/poor if extreme
                band, _, _ = metric_status("centroid", 2000 + (1 - norm) * 1000)
                col = QColor(value_color("centroid", 1500 + norm * 4000))

            y = base - bar_h
            grad = QLinearGradient(x, y, x, base)
            grad.setColorAt(0, col)
            c2 = QColor(col)
            c2.setAlpha(90)
            grad.setColorAt(1, c2)
            p.setBrush(grad)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x, y, bw, bar_h), 3, 3)

            p.setPen(QColor(Color.MUTED))
            font = QFont()
            font.setPointSize(7)
            font.setBold(True)
            p.setFont(font)
            p.drawText(QRectF(x - 2, base + 2, bw + 4, 16), Qt.AlignHCenter, name[:7])

        p.end()


class SpectrogramCanvas(QWidget):
    """High-definition mel spectrogram display with Hz / time axes + playhead."""

    seekRequested = Signal(float)

    def __init__(self, parent=None, interactive: bool = True):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._image: QImage | None = None
        self._empty = True
        self._loading = False
        self._duration = 0.0
        self._position = 0.0
        self._fmin = 20.0
        self._fmax = 20000.0
        self._status = ""
        self._interactive = interactive
        if interactive:
            self.setMouseTracking(True)
            self.setCursor(Qt.CursorShape.CrossCursor)

    def clear(self):
        self._image = None
        self._empty = True
        self._loading = False
        self._duration = 0.0
        self._position = 0.0
        self._status = ""
        self.update()

    def set_loading(self, loading: bool, status: str = "Computing HD spectrogram…"):
        self._loading = bool(loading)
        self._status = status if loading else self._status
        self.update()

    def set_image(
        self,
        image: QImage | None,
        *,
        duration_s: float = 0.0,
        fmin: float = 20.0,
        fmax: float = 20000.0,
        status: str = "",
    ):
        self._image = image
        self._empty = image is None or image.isNull()
        self._loading = False
        self._duration = max(0.0, float(duration_s or 0.0))
        self._fmin = float(fmin or 20.0)
        self._fmax = float(fmax or 20000.0)
        self._status = status or ""
        self.update()

    def set_position(self, seconds: float):
        self._position = max(0.0, min(float(seconds), self._duration or 0.0))
        self.update()

    def set_duration(self, seconds: float):
        self._duration = max(0.0, float(seconds))
        self.update()

    def _plot_rect(self) -> QRectF:
        # Leave room for left Hz labels + bottom time labels
        return QRectF(36, 6, max(1, self.width() - 44), max(1, self.height() - 22))

    def _x_to_time(self, x: float) -> float:
        r = self._plot_rect()
        if not self._duration or r.width() <= 0:
            return 0.0
        t = (x - r.left()) / r.width() * self._duration
        return max(0.0, min(self._duration, t))

    def mousePressEvent(self, event: QMouseEvent):
        if self._interactive and event.button() == Qt.MouseButton.LeftButton and self._duration:
            self.seekRequested.emit(self._x_to_time(event.position().x()))
        super().mousePressEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        bg = QLinearGradient(0, 0, 0, h)
        bg.setColorAt(0, QColor(Color.BG))
        bg.setColorAt(1, QColor(Color.SURFACE))
        p.fillRect(self.rect(), bg)

        plot = self._plot_rect()

        if self._loading:
            p.setPen(QColor(Color.MUTED))
            font = QFont()
            font.setPointSize(10)
            font.setBold(True)
            p.setFont(font)
            p.drawText(self.rect(), Qt.AlignCenter, self._status or "Computing HD spectrogram…")
            p.end()
            return

        if self._empty or self._image is None or self._image.isNull():
            p.setPen(QColor(Color.MUTED))
            font = QFont()
            font.setPointSize(11)
            font.setBold(True)
            p.setFont(font)
            msg = self._status or "-"
            p.drawText(self.rect(), Qt.AlignCenter, msg)
            p.end()
            return

        # Smooth scale into plot (nearest-neighbor stretch caused blocky B-side artifacts)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawImage(plot, self._image)

        # Frequency ticks (mel space is nonlinear; labels are approximate end-points)
        font = QFont()
        font.setPointSize(7)
        font.setBold(True)
        p.setFont(font)
        p.setPen(QColor(Color.MUTED))
        labels = [
            (0.0, f"{int(self._fmax / 1000)}k" if self._fmax >= 1000 else f"{int(self._fmax)}"),
            (0.5, "mid"),
            (1.0, f"{int(self._fmin)}"),
        ]
        for frac, text in labels:
            y = plot.top() + frac * plot.height()
            p.drawText(QRectF(2, y - 7, 32, 14), Qt.AlignRight | Qt.AlignVCenter, text)

        # Time ticks
        if self._duration > 0:
            for frac, label in ((0.0, "0:00"), (0.5, None), (1.0, None)):
                if label is None:
                    t = self._duration * frac
                    m, s = int(t // 60), int(t % 60)
                    label = f"{m}:{s:02d}"
                x = plot.left() + frac * plot.width()
                p.drawText(
                    QRectF(x - 18, plot.bottom() + 2, 36, 14),
                    Qt.AlignHCenter | Qt.AlignTop,
                    label,
                )

        # Playhead
        if self._duration > 0 and self._position >= 0:
            x = plot.left() + (self._position / self._duration) * plot.width()
            pen = QPen(QColor(Color.ACCENT))
            pen.setWidth(1)
            p.setPen(pen)
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        p.end()


class ChartPanel(QFrame):
    """Labeled glass panel wrapping a chart canvas."""

    def __init__(self, title: str, canvas: QWidget):
        super().__init__()
        self.setStyleSheet(f"""
            QFrame#ChartPanel {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        self.setObjectName("ChartPanel")
        self.setMinimumHeight(168)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.MD)
        lay.setSpacing(Space.XS)

        hdr = QHBoxLayout()
        self._title = QLabel(title)
        self._title.setStyleSheet(
            f"font-size: {Type.TINY}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"letter-spacing: 1.4px; color: {Color.MUTED}; background: transparent;"
        )
        hdr.addWidget(self._title)
        hdr.addStretch()
        self._badge = QLabel("STANDBY")
        self._badge.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
            f"color: {Color.MUTED}; background: {Color.with_alpha(Color.MUTED, 0.12)}; "
            f"border-radius: 4px; padding: 2px 6px;"
        )
        hdr.addWidget(self._badge)
        lay.addLayout(hdr)

        self.canvas = canvas
        lay.addWidget(self.canvas, 1)

    def set_live(self, live: bool):
        if live:
            self._badge.setText("LIVE")
            self._badge.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
                f"color: {Color.ACCENT}; background: {Color.with_alpha(Color.ACCENT, 0.12)}; "
                f"border-radius: 4px; padding: 2px 6px;"
            )
        else:
            self._badge.setText("STANDBY")
            self._badge.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
                f"color: {Color.MUTED}; background: {Color.with_alpha(Color.MUTED, 0.12)}; "
                f"border-radius: 4px; padding: 2px 6px;"
            )


class SpectralChartPanel(QFrame):
    """
    Spectral Balance · HD Spectrogram · Stereo Spectral Image.

    Save exports PNG under exports/spectral/.
    """

    seekRequested = Signal(float)

    def __init__(self, title: str = "SPECTRAL"):
        super().__init__()
        self.setObjectName("ChartPanel")
        self.setStyleSheet(f"""
            QFrame#ChartPanel {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.XL}px;
            }}
        """)
        self.setMinimumHeight(168)
        self._path: str | None = None
        self._mode = "spectrogram"  # balance | spectrogram | image
        self._sg_ready = False
        self._img_ready = False
        self._thread: QThread | None = None
        self._worker: _SpectrogramWorker | None = None
        self._job_token = 0
        self._last_sg_image: QImage | None = None
        self._last_img_image: QImage | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(Space.MD, Space.SM, Space.MD, Space.MD)
        lay.setSpacing(Space.XS)

        hdr = QHBoxLayout()
        self._title = QLabel("SPECTROGRAM" if title in ("SPECTRAL", "SPECTRAL BALANCE") else title)
        self._title.setStyleSheet(
            f"font-size: {Type.TINY}px; font-weight: {Type.WEIGHTS['semibold']}; "
            f"letter-spacing: 1.4px; color: {Color.MUTED}; background: transparent;"
        )
        hdr.addWidget(self._title)
        hdr.addStretch()

        btn_style = f"""
            QToolButton {{
                font-size: 9px; font-weight: 700; letter-spacing: 0.8px;
                color: {Color.MUTED}; background: transparent;
                border: 1px solid {Color.LINE}; border-radius: 4px;
                padding: 3px 8px;
            }}
            QToolButton:checked {{
                color: {Color.BG}; background: {Color.ACCENT};
                border-color: {Color.ACCENT};
            }}
            QToolButton:hover:!checked {{
                color: {Color.TEXT}; border-color: {Color.ACCENT};
            }}
        """
        self._btn_balance = QToolButton()
        self._btn_balance.setText("BALANCE")
        self._btn_balance.setCheckable(True)
        self._btn_balance.setToolTip("7-band relative spectral balance (SUB→AIR)")
        self._btn_sg = QToolButton()
        self._btn_sg.setText("SPECTROGRAM")
        self._btn_sg.setCheckable(True)
        self._btn_sg.setChecked(True)
        self._btn_sg.setToolTip("HD mel spectrogram (energy over time & frequency).")
        self._btn_img = QToolButton()
        self._btn_img.setText("IMAGE")
        self._btn_img.setCheckable(True)
        self._btn_img.setToolTip(
            "Stereo spectral image: color = L/R balance (theme soft=left, accent=center, dim=right), "
            "brightness = energy. Useful for spotting side-heavy highs or mono bass."
        )
        self._btn_save = QToolButton()
        self._btn_save.setText("SAVE")
        self._btn_save.setToolTip("Save current spectrogram / spectral image as PNG")
        self._btn_save.clicked.connect(self.save_current_png)
        for b in (self._btn_balance, self._btn_sg, self._btn_img, self._btn_save):
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(btn_style)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._btn_balance)
        self._mode_group.addButton(self._btn_sg)
        self._mode_group.addButton(self._btn_img)
        self._btn_balance.clicked.connect(lambda: self.set_mode("balance"))
        self._btn_sg.clicked.connect(lambda: self.set_mode("spectrogram"))
        self._btn_img.clicked.connect(lambda: self.set_mode("image"))
        hdr.addWidget(self._btn_balance)
        hdr.addWidget(self._btn_sg)
        hdr.addWidget(self._btn_img)
        hdr.addWidget(self._btn_save)

        self._badge = QLabel("STANDBY")
        self._badge.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
            f"color: {Color.MUTED}; background: {Color.with_alpha(Color.MUTED, 0.12)}; "
            f"border-radius: 4px; padding: 2px 6px;"
        )
        hdr.addWidget(self._badge)
        lay.addLayout(hdr)

        self.balance_canvas = SpectrumCanvas()
        self.spectrogram_canvas = SpectrogramCanvas(interactive=True)
        self.image_canvas = SpectrogramCanvas(interactive=True)
        self.spectrogram_canvas.seekRequested.connect(self.seekRequested.emit)
        self.image_canvas.seekRequested.connect(self.seekRequested.emit)

        self._stack = QStackedWidget()
        self._stack.addWidget(self.balance_canvas)  # 0
        self._stack.addWidget(self.spectrogram_canvas)  # 1
        self._stack.addWidget(self.image_canvas)  # 2
        self._stack.setCurrentIndex(1)
        lay.addWidget(self._stack, 1)

        self.canvas = self.balance_canvas

    def set_mode(self, mode: str):
        if mode not in ("balance", "spectrogram", "image"):
            mode = "spectrogram"
        self._mode = mode
        self._btn_balance.setChecked(mode == "balance")
        self._btn_sg.setChecked(mode == "spectrogram")
        self._btn_img.setChecked(mode == "image")
        idx = {"balance": 0, "spectrogram": 1, "image": 2}[mode]
        self._stack.setCurrentIndex(idx)
        titles = {
            "balance": "SPECTRAL BALANCE",
            "spectrogram": "SPECTROGRAM",
            "image": "SPECTRAL IMAGE  ·  L/R",
        }
        self._title.setText(titles[mode])
        if mode == "spectrogram" and self._path and not self._sg_ready:
            self._start_job(self._path, "spectrogram")
        if mode == "image" and self._path and not self._img_ready:
            self._start_job(self._path, "image")

    def set_bands(self, bands: dict[str, float | None] | None):
        self.balance_canvas.set_bands(bands)

    def set_audio_path(self, path: Path | str | None, *, auto_compute: bool = True):
        """Point spectrogram/image at analyzed audio; compute in background."""
        p = str(Path(path).resolve()) if path and Path(path).is_file() else None
        if p == self._path and self._sg_ready:
            self.set_mode("spectrogram")
            return
        self._path = p
        self._sg_ready = False
        self._img_ready = False
        self._last_sg_image = None
        self._last_img_image = None
        self.spectrogram_canvas.clear()
        self.image_canvas.clear()
        if not p:
            return
        self.set_mode("spectrogram")
        if auto_compute or self._mode == "spectrogram":
            self._start_job(p, "spectrogram")

    def set_position(self, seconds: float):
        self.spectrogram_canvas.set_position(seconds)
        self.image_canvas.set_position(seconds)

    def clear(self):
        self._job_token += 1
        self._path = None
        self._sg_ready = False
        self._img_ready = False
        self._last_sg_image = None
        self._last_img_image = None
        self.balance_canvas.clear()
        self.spectrogram_canvas.clear()
        self.image_canvas.clear()
        self.set_live(False)

    def set_live(self, live: bool):
        if live:
            self._badge.setText("LIVE")
            self._badge.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
                f"color: {Color.ACCENT}; background: {Color.with_alpha(Color.ACCENT, 0.12)}; "
                f"border-radius: 4px; padding: 2px 6px;"
            )
        else:
            self._badge.setText("STANDBY")
            self._badge.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
                f"color: {Color.MUTED}; background: {Color.with_alpha(Color.MUTED, 0.12)}; "
                f"border-radius: 4px; padding: 2px 6px;"
            )

    def save_current_png(self):
        """Export active spectrogram or spectral image to exports/spectral/."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        img: QImage | None = None
        kind = self._mode
        if kind == "spectrogram":
            img = self._last_sg_image or getattr(self.spectrogram_canvas, "_image", None)
        elif kind == "image":
            img = self._last_img_image or getattr(self.image_canvas, "_image", None)
        else:
            # capture balance widget
            pix = self.balance_canvas.grab()
            img = pix.toImage() if pix else None
        if img is None or img.isNull():
            QMessageBox.information(
                self,
                "Save",
                "Nothing to save yet — analyze a track and open SPECTROGRAM or IMAGE.",
            )
            return
        out_dir = Path(__file__).resolve().parents[3] / "exports" / "spectral"
        out_dir.mkdir(parents=True, exist_ok=True)
        stem = Path(self._path).stem if self._path else "spectral"
        default = out_dir / f"{stem}_{kind}.png"
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save spectral PNG", str(default), "PNG (*.png)"
        )
        if not dest:
            return
        if not dest.lower().endswith(".png"):
            dest += ".png"
        ok = img.save(dest, "PNG")
        if ok:
            QMessageBox.information(self, "Saved", f"Saved:\n{dest}")
            try:
                import os

                os.startfile(str(Path(dest).parent))
            except Exception:
                pass
        else:
            QMessageBox.critical(self, "Save failed", f"Could not write {dest}")

    def _start_job(self, path: str, kind: str = "spectrogram"):
        self._job_token += 1
        token = self._job_token
        canvas = self.image_canvas if kind == "image" else self.spectrogram_canvas
        label = "spectral image" if kind == "image" else "HD spectrogram"
        canvas.set_loading(True, f"Computing {label}…")
        if self._mode in ("spectrogram", "image"):
            self._badge.setText("BUILD")
            self._badge.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1px; "
                f"color: {Color.WARNING}; background: {Color.with_alpha(Color.WARNING, 0.12)}; "
                f"border-radius: 4px; padding: 2px 6px;"
            )

        if self._thread is not None:
            try:
                self._thread.quit()
                self._thread.wait(50)
            except Exception:
                pass
            self._thread = None
            self._worker = None

        thread = QThread(self)
        worker = _SpectrogramWorker()
        worker.moveToThread(thread)
        self._thread = thread
        self._worker = worker

        def _run():
            worker.run(
                path,
                {
                    "target_sr": 44100,
                    "n_mels": 256,
                    "n_fft": 4096,
                    "time_bins": 1600,
                    "fmin": 20.0,
                    "fmax": 20000.0,
                    "max_duration_s": 720.0,
                    "top_db": 80.0,
                },
                kind,
            )

        def _done(done_path: str, image: object, meta: object, done_kind: str):
            if token != self._job_token:
                return
            if done_path != self._path:
                return
            meta = meta if isinstance(meta, dict) else {}
            target = self.image_canvas if done_kind == "image" else self.spectrogram_canvas
            if image is not None and isinstance(image, QImage) and not image.isNull():
                if done_kind == "image":
                    self._img_ready = True
                    self._last_img_image = image
                else:
                    self._sg_ready = True
                    self._last_sg_image = image
                target.set_image(
                    image,
                    duration_s=float(meta.get("duration_s") or 0.0),
                    fmin=float(meta.get("fmin") or 20.0),
                    fmax=float(meta.get("fmax") or 20000.0),
                )
                self.set_live(True)
            else:
                if done_kind == "image":
                    self._img_ready = False
                else:
                    self._sg_ready = False
                err = (meta or {}).get("error") if isinstance(meta, dict) else None
                target.set_image(
                    None,
                    status=f"{label.capitalize()} unavailable{(': ' + str(err)) if err else ''}",
                )
                self.set_live(False)
            try:
                thread.quit()
            except Exception:
                pass

        thread.started.connect(_run)
        worker.finished.connect(_done)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _start_spectrogram_job(self, path: str):
        """Backward-compatible alias."""
        self._start_job(path, "spectrogram")


class _MetricComparePopup(QFrame):
    """Minimal floating Original / Repaired readout near the cursor."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {Color.LINE};
                border-radius: {Radius.MD}px;
            }}
            QLabel {{
                background: transparent;
                border: none;
            }}
        """)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        def _row():
            box = QVBoxLayout()
            box.setSpacing(1)
            cap = QLabel()
            cap.setStyleSheet(
                f"font-size: 9px; font-weight: 600; letter-spacing: 1.0px; color: {Color.MUTED};"
            )
            val = QLabel()
            val.setStyleSheet(
                f"font-size: 16px; font-weight: 700; font-family: {Type.DISPLAY}; "
                f"letter-spacing: -0.3px; color: {Color.MUTED};"
            )
            box.addWidget(cap)
            box.addWidget(val)
            return box, cap, val

        o_box, self.o_cap, self.o_val = _row()
        r_box, self.r_cap, self.r_val = _row()
        self.o_cap.setText("Original")
        self.r_cap.setText("Repaired")
        lay.addLayout(o_box)
        lay.addLayout(r_box)

    def show_compare(self, orig, rep, kind: str, unit: str = ""):
        def _fmt(v):
            if v is None or v == "":
                return "-"
            if isinstance(v, float):
                return f"{v:.1f}" if abs(v) < 1000 else f"{v:.0f}"
            return str(v)

        def _style(lbl: QLabel, v, k: str):
            if v is None or v == "":
                lbl.setText("-")
                lbl.setStyleSheet(
                    f"font-size: 16px; font-weight: 700; font-family: {Type.DISPLAY}; "
                    f"letter-spacing: -0.3px; color: {Color.MUTED};"
                )
                return
            col = value_color(k, v)
            text = _fmt(v)
            if unit:
                text = f"{text} {unit}".strip()
            lbl.setText(text)
            lbl.setStyleSheet(
                f"font-size: 16px; font-weight: 700; font-family: {Type.DISPLAY}; "
                f"letter-spacing: -0.3px; color: {col};"
            )

        _style(self.o_val, orig, kind)
        _style(self.r_val, rep, kind)
        self.adjustSize()
        pos = QCursor.pos() + QPoint(14, 16)
        self.move(pos)
        self.show()
        self.raise_()


class MetricTile(QFrame):
    """Equal-size metric card with score-band colored value + optional O/R hover."""

    _popup: _MetricComparePopup | None = None

    def __init__(self, key: str, label: str, unit: str):
        super().__init__()
        self.key = key
        self._unit = unit
        self._kind = key
        self._value = None
        self._orig = None
        self._rep = None
        self._compare_active = False
        self.setMinimumHeight(92)
        self.setMaximumHeight(100)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setMouseTracking(True)
        self._restyle_frame(Color.LINE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.setSpacing(4)
        lay.setAlignment(Qt.AlignVCenter)

        self.lbl = QLabel(label)
        self.lbl.setAlignment(Qt.AlignHCenter)
        self.lbl.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.1px; "
            f"color: {Color.MUTED}; background: transparent;"
        )
        lay.addWidget(self.lbl)

        self.val = QLabel("-")
        self.val.setAlignment(Qt.AlignHCenter)
        self.val.setStyleSheet(
            f"font-size: 20px; font-weight: 700; font-family: {Type.DISPLAY}; "
            f"color: {Color.MUTED}; letter-spacing: -0.4px; background: transparent;"
        )
        lay.addWidget(self.val)

        self.unit = QLabel(unit)
        self.unit.setAlignment(Qt.AlignHCenter)
        self.unit.setStyleSheet(f"font-size: 9px; color: {Color.MUTED}; background: transparent;")
        lay.addWidget(self.unit)

    def _restyle_frame(self, border: str):
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.ELEVATED};
                border: 1px solid {border};
                border-radius: {Radius.LG}px;
            }}
        """)

    @classmethod
    def _get_popup(cls) -> _MetricComparePopup:
        if cls._popup is None:
            cls._popup = _MetricComparePopup()
        return cls._popup

    def set_value(self, value: Any, kind: str | None = None, unit_override: str | None = None):
        kind = kind or self.key
        self._kind = kind
        self._value = value
        if unit_override is not None:
            self._unit = unit_override
            self.unit.setText(unit_override)
        if value is None or value == "":
            self.val.setText("-")
            self.val.setStyleSheet(
                f"font-size: 20px; font-weight: 700; font-family: {Type.DISPLAY}; "
                f"color: {Color.MUTED}; letter-spacing: -0.4px; background: transparent;"
            )
            self._restyle_frame(Color.LINE)
            return

        if isinstance(value, float):
            text = f"{value:.1f}" if abs(value) < 1000 else f"{value:.0f}"
        else:
            text = str(value)

        col = value_color(kind, value)
        self.val.setText(text)
        self.val.setStyleSheet(
            f"font-size: 20px; font-weight: 700; font-family: {Type.DISPLAY}; "
            f"color: {col}; letter-spacing: -0.4px; background: transparent;"
        )
        self._restyle_frame(Color.with_alpha(col, 0.35))

    def set_compare(self, original: Any, repaired: Any, kind: str | None = None):
        """Enable Original/Repaired hover popup (only after a real repair)."""
        self._orig = original
        self._rep = repaired
        self._compare_active = original is not None or repaired is not None
        if kind:
            self._kind = kind
        # Display repaired (current) value
        self.set_value(repaired if repaired is not None else original, self._kind)

    def clear_compare(self):
        self._orig = None
        self._rep = None
        self._compare_active = False
        try:
            pop = MetricTile._popup
            if pop is not None and pop.isVisible():
                pop.hide()
        except Exception:
            pass

    def enterEvent(self, event):
        if self._compare_active:
            pop = self._get_popup()
            pop.show_compare(self._orig, self._rep, self._kind, self._unit or "")
        super().enterEvent(event)

    def leaveEvent(self, event):
        try:
            if MetricTile._popup is not None:
                MetricTile._popup.hide()
        except Exception:
            pass
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._compare_active and MetricTile._popup is not None and MetricTile._popup.isVisible():
            MetricTile._popup.move(QCursor.pos() + QPoint(14, 16))
        super().mouseMoveEvent(event)

    def clear(self):
        self.clear_compare()
        self.set_value(None)
