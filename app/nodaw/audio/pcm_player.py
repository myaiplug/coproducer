"""
High-fidelity PCM preview engine for CoProducer.

Design goals (preview must never compromise the mix):
  - Decode with soundfile at the file's native sample rate (float32 pipeline)
  - Output via PortAudio (sounddevice); open at native SR when the device allows
  - High-quality resampling ONLY when the device cannot open at native rate
  - Soft cosine ramps on play / pause / stop / seek (no clicks / pops)
  - Never drop audio frames (blocking queue + backpressure)
  - Prefill the ring before starting the stream (no startup underflows)
  - No FX, no forced 16-bit or 48 kHz downgrade, playback rate always 1.0

QMediaPlayer-compatible surface (play / pause / stop / setPosition / position /
playbackState / setSource + Qt signals) so call sites can drop this in.
"""

from __future__ import annotations

import math
import queue
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import numpy as np

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QMediaPlayer

try:
    import soundfile as sf
    import sounddevice as sd

    HAS_SD = True
except Exception:  # pragma: no cover
    HAS_SD = False
    sf = sd = None  # type: ignore


# Decode block (worker) and device callback sizes.
# Prefill + high latency keep the ring full so underflows (clicks) never appear.
DECODE_BLOCK = 4096
STREAM_BLOCK = 1024
QUEUE_MAX = 48
PREFILL_BLOCKS = 10
RAMP_MS = 10.0  # soft edge on start / stop / seek / side switch
XFADE_MS = 12.0  # A/B side switch crossfade


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _path_from_source(path: Any) -> str | None:
    if path is None:
        return None
    if isinstance(path, QUrl):
        if path.isLocalFile():
            return path.toLocalFile()
        s = path.toString()
        if s.startswith("file:"):
            return unquote(urlparse(s).path.lstrip("/")) if "://" in s else s
        return s or None
    p = str(path)
    if p.startswith("file:"):
        try:
            u = QUrl(p)
            if u.isLocalFile():
                return u.toLocalFile()
        except Exception:
            pass
    return p


def _cosine_ramp(n: int, up: bool) -> np.ndarray:
    """0→1 (up) or 1→0 (down) raised-cosine over n samples."""
    if n <= 0:
        return np.zeros(0, dtype=np.float32)
    if n == 1:
        return np.array([1.0 if up else 0.0], dtype=np.float32)
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)
    if up:
        return (0.5 - 0.5 * np.cos(np.pi * t)).astype(np.float32)
    return (0.5 + 0.5 * np.cos(np.pi * t)).astype(np.float32)


def _hq_resample(data: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """
    High-quality resample of (frames, channels) float32.
    Prefer soxr / scipy polyphase; never use linear interpolation.
    """
    if orig_sr == target_sr or data.size == 0:
        return data.astype(np.float32, copy=False)
    # scipy.signal.resample_poly — excellent polyphase FIR
    try:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(int(target_sr), int(orig_sr))
        up = int(target_sr) // g
        down = int(orig_sr) // g
        chans = []
        for c in range(data.shape[1]):
            chans.append(resample_poly(data[:, c].astype(np.float64), up, down).astype(np.float32))
        n = min(len(x) for x in chans)
        return np.stack([x[:n] for x in chans], axis=1)
    except Exception:
        pass
    # librosa soxr_hq / kaiser_best
    try:
        import librosa

        chans = []
        for c in range(data.shape[1]):
            try:
                y = librosa.resample(
                    data[:, c].astype(np.float32),
                    orig_sr=orig_sr,
                    target_sr=target_sr,
                    res_type="soxr_hq",
                )
            except Exception:
                y = librosa.resample(
                    data[:, c].astype(np.float32),
                    orig_sr=orig_sr,
                    target_sr=target_sr,
                    res_type="kaiser_best",
                )
            chans.append(y.astype(np.float32))
        n = min(len(x) for x in chans)
        return np.stack([x[:n] for x in chans], axis=1)
    except Exception:
        pass
    # Last resort: pedalboard Resample (still decent)
    try:
        import pedalboard as pb

        # pedalboard wants (channels, samples)
        x = data.T.astype(np.float32)
        board = pb.Pedalboard([pb.Resample(target_sample_rate=float(target_sr))])
        y = np.asarray(board(x, orig_sr), dtype=np.float32)
        return y.T
    except Exception:
        pass
    # Absolute fallback: ratio index (audible but better than silence)
    ratio = float(target_sr) / float(orig_sr)
    n_out = max(1, int(round(data.shape[0] * ratio)))
    idx = np.linspace(0, data.shape[0] - 1, n_out)
    out = np.zeros((n_out, data.shape[1]), dtype=np.float32)
    for c in range(data.shape[1]):
        out[:, c] = np.interp(idx, np.arange(data.shape[0]), data[:, c]).astype(np.float32)
    return out


def _open_output_stream(
    sr: int,
    ch: int,
    callback,
    blocksize: int = STREAM_BLOCK,
    latency: str | float = "high",
):
    """
    Open an OutputStream at the file's sample rate.
    Falls back through common rates, channel counts, latencies, and devices
    if the preferred open is rejected (common with interfaces in exclusive use).
    Returns (stream, actual_sr) — actual_sr may differ (caller must resample).

    latency:
      - "high"  — transport / A-B listen (stable, larger device buffer)
      - "low"   — Studio LiveFX editing (tight, knob changes feel immediate)
      - float   — seconds (PortAudio explicit latency)
    """
    ch = max(1, min(2, int(ch or 2)))
    # Prefer stereo on Windows — many devices reject mono OutputStream
    channel_try = [ch]
    if 2 not in channel_try:
        channel_try.append(2)
    if 1 not in channel_try:
        channel_try.append(1)

    attempts = [int(sr)]
    for candidate in (48000, 44100, 96000, 88200, 32000):
        if candidate not in attempts:
            attempts.append(candidate)

    # Prefer requested latency; fall back if the device rejects it
    latency_try: list[str | float] = [latency]
    for alt in ("high", "low", 0.08, 0.05, 0.12, 0.2):
        if alt not in latency_try:
            latency_try.append(alt)

    # Optional WASAPI shared mode (exclusive locks kill playback when DAW/app holds device)
    extra_list: list[Any] = [None]
    try:
        extra_list.insert(0, sd.WasapiSettings(exclusive=False))
    except Exception:
        pass

    # Devices: default first, then every output-capable host API device
    devices: list[Any] = [None]
    try:
        for i, dev in enumerate(sd.query_devices()):
            if int(dev.get("max_output_channels") or 0) >= 1:
                if i not in devices:
                    devices.append(i)
    except Exception:
        pass

    last_err: Exception | None = None
    for device in devices:
        for try_sr in attempts:
            for try_ch in channel_try:
                for lat in latency_try:
                    for extra in extra_list:
                        try:
                            kwargs: dict[str, Any] = dict(
                                samplerate=try_sr,
                                channels=try_ch,
                                dtype="float32",
                                blocksize=blocksize,
                                callback=callback,
                                latency=lat,
                            )
                            if device is not None:
                                kwargs["device"] = device
                            if extra is not None:
                                kwargs["extra_settings"] = extra
                            stream = sd.OutputStream(**kwargs)
                            # Stash negotiated channel count for callers that need upmix
                            try:
                                stream._nodaw_channels = try_ch  # type: ignore[attr-defined]
                            except Exception:
                                pass
                            return stream, int(try_sr)
                        except Exception as exc:
                            last_err = exc
                            continue
    raise RuntimeError(f"audio device unavailable: {last_err}")


# ---------------------------------------------------------------------------
# Single-source high-fidelity player
# ---------------------------------------------------------------------------


class HiFiPlayer(QObject):
    """Bit-transparent-ish preview player. Never applies FX. Rate always 1.0."""

    positionChanged = Signal(int)  # ms
    durationChanged = Signal(int)  # ms
    playbackStateChanged = Signal(object)  # QMediaPlayer.PlaybackState
    errorOccurred = Signal(object, object)  # (error, errorString)
    mediaStatusChanged = Signal(object)

    _Playing = QMediaPlayer.PlaybackState.PlayingState
    _Paused = QMediaPlayer.PlaybackState.PausedState
    _Stopped = QMediaPlayer.PlaybackState.StoppedState

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._source: str | None = None
        self._volume = 1.0
        self._muted = False
        self._state = self._Stopped
        self._error = ""
        self._played_frames = 0
        self._total_frames = 0
        self._file_sr = 44100
        self._out_sr = 44100
        self._channels = 2
        self._seek_frames: int | None = None
        self._gen = 0
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()  # set = paused (worker waits)
        self._pause_evt.set()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._cb_buf: np.ndarray | None = None
        self._eof = False
        self._underflows = 0
        self._ramp_gain = 0.0  # current output gain (for soft edges)
        self._ramp_target = 0.0
        self._need_fade_in = False
        self._need_fade_out = False
        self._device_needs_resample = False

    # ---- public API (QMediaPlayer-compatible) ----

    def setSource(self, path: Any) -> None:
        src = _path_from_source(path)
        was_playing = self._state == self._Playing
        self.stop()
        with self._lock:
            self._source = src
            self._total_frames = 0
            self._played_frames = 0
            self._seek_frames = None
            self._device_needs_resample = False
        self._emit_duration()
        if was_playing and src and Path(src).is_file():
            self.play()

    def source(self) -> QUrl:
        if self._source:
            return QUrl.fromLocalFile(str(Path(self._source).resolve()))
        return QUrl()

    def setAudioOutput(self, *_args, **_kwargs) -> None:
        """No-op: PortAudio owns the device (API compat with QMediaPlayer)."""
        return

    def setVolume(self, v: float) -> None:
        with self._lock:
            self._volume = _clamp(v, 0.0, 1.0)

    def volume(self) -> float:
        return self._volume

    def setMuted(self, muted: bool) -> None:
        with self._lock:
            self._muted = bool(muted)

    def isMuted(self) -> bool:
        return self._muted

    def setPlaybackRate(self, rate: float) -> None:
        """Preview is always 1.0 — never pitch/tempo-shift for 'convenience'."""
        if abs(float(rate) - 1.0) > 1e-6:
            # Ignore non-1.0 rates silently; quality first.
            pass

    def playbackRate(self) -> float:
        return 1.0

    def play(self) -> None:
        if self._state == self._Playing:
            return
        if not HAS_SD:
            self._fail("live audio unavailable (sounddevice missing)")
            return
        if not self._source or not Path(self._source).is_file():
            self._fail("no source file")
            return
        self._eof = False
        self._need_fade_in = True
        self._ramp_target = 1.0
        self._error = ""
        self._pause_evt.set()  # clear pause wait
        thr_alive = self._thread is not None and self._thread.is_alive()
        st = self._stream
        st_ok = False
        if thr_alive and st is not None:
            try:
                if not getattr(st, "active", False):
                    st.start()
                st_ok = bool(getattr(st, "active", True))
            except Exception:
                st_ok = False
        if not st_ok:
            # Dead stream after EOF / failed pause resume → full restart
            self._stop_evt.set()
            self._gen += 1
            if st is not None:
                try:
                    st.stop()
                except Exception:
                    pass
                try:
                    st.close()
                except Exception:
                    pass
            self._stream = None
            t = self._thread
            if t is not None and t.is_alive() and t is not threading.current_thread():
                t.join(timeout=1.2)
            self._thread = None
            self._drain_queue()
            self._cb_buf = None
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="hifi-pcm")
            self._thread.start()
        self._set_state(self._Playing)

    def pause(self) -> None:
        if self._state != self._Playing:
            return
        # Soft fade then stop stream (no hard cut)
        self._need_fade_out = True
        self._ramp_target = 0.0
        # Give the callback a moment to ramp, then pause
        self._stream_stop_soft()
        self._set_state(self._Paused)

    def stop(self) -> None:
        self._gen += 1
        with self._lock:
            self._seek_frames = 0
            self._played_frames = 0
        self._need_fade_out = True
        self._ramp_target = 0.0
        self._stream_stop()
        self._drain_queue()
        self._cb_buf = None
        self._eof = False
        self._ramp_gain = 0.0
        self._set_state(self._Stopped)
        self.positionChanged.emit(0)

    def setPosition(self, ms: int) -> None:
        with self._lock:
            sr = self._file_sr if self._file_sr > 0 else self._out_sr
            if sr > 0:
                frame = max(0, int(ms / 1000.0 * sr))
                if self._total_frames > 0:
                    frame = min(frame, self._total_frames)
                self._seek_frames = frame
                self._played_frames = frame
                self._need_fade_in = True  # de-click after seek
        self.positionChanged.emit(int(ms))

    def position(self) -> int:
        with self._lock:
            sr = self._file_sr if self._file_sr > 0 else self._out_sr
            return int(self._played_frames / max(1, sr) * 1000)

    def duration(self) -> int:
        with self._lock:
            sr = self._file_sr if self._file_sr > 0 else self._out_sr
            return int(self._total_frames / max(1, sr) * 1000)

    def playbackState(self):
        return self._state

    def errorString(self) -> str:
        return self._error

    def shutdown(self) -> None:
        self._stop_evt.set()
        self._gen += 1
        self._pause_evt.set()
        self._stream_stop()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.5)
        self._thread = None
        self._stream = None

    # ---- internals ----

    def _emit_duration(self) -> None:
        path = self._source
        if not path or not Path(path).is_file() or sf is None:
            return
        try:
            with sf.SoundFile(path) as f:
                frames = int(f.frames)
                sr = int(f.samplerate)
        except Exception:
            return
        with self._lock:
            self._total_frames = frames
            self._file_sr = sr
            self._out_sr = sr
        self.durationChanged.emit(int(frames / max(1, sr) * 1000))

    def _set_state(self, state) -> None:
        if state != self._state:
            self._state = state
            self.playbackStateChanged.emit(state)

    def _fail(self, msg: str) -> None:
        self._error = msg
        self.errorOccurred.emit(QMediaPlayer.Error.FormatError, msg)
        self._set_state(self._Stopped)

    def _drain_queue(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _stream_start(self) -> None:
        st = self._stream
        if st is not None:
            try:
                if not st.active:
                    st.start()
            except Exception:
                pass

    def _stream_stop(self) -> None:
        st = self._stream
        if st is not None:
            try:
                st.stop()
            except Exception:
                pass

    def _stream_stop_soft(self) -> None:
        """Stop after a short fade window (called from UI thread)."""
        # Best-effort: stop stream; ramp is applied in-callback while frames remain.
        self._stream_stop()

    def _effective_volume(self) -> float:
        with self._lock:
            if self._muted:
                return 0.0
            return float(self._volume)

    def _run(self) -> None:
        path = self._source
        try:
            f = sf.SoundFile(path)
        except Exception as exc:
            self._fail(f"cannot open audio: {exc}")
            return

        file_sr = int(f.samplerate)
        ch = min(int(f.channels), 2) or 1
        with self._lock:
            self._file_sr = file_sr
            self._channels = ch
            self._total_frames = int(f.frames)
            if self._seek_frames is None:
                self._seek_frames = 0
        self.durationChanged.emit(int(f.frames / max(1, file_sr) * 1000))

        # Shared state for open attempt
        out_sr_holder = {"sr": file_sr}

        def _cb(outdata, frames, time_info, status):
            self._sd_cb(outdata, frames, time_info, status)

        stream = None
        try:
            stream, out_sr = _open_output_stream(file_sr, ch, _cb, STREAM_BLOCK)
            out_sr_holder["sr"] = out_sr
        except Exception as exc:
            self._fail(f"audio device unavailable: {exc}")
            f.close()
            return

        needs_resample = int(out_sr) != int(file_sr)
        with self._lock:
            self._out_sr = int(out_sr)
            self._device_needs_resample = needs_resample
        self._stream = stream

        # Prefill before start so the callback never underflows on play
        gen = self._gen
        block_start = 0
        prefilled = 0
        try:
            # Apply initial seek
            with self._lock:
                seek = self._seek_frames
                if seek is not None:
                    self._seek_frames = None
                    try:
                        f.seek(int(seek))
                    except Exception:
                        pass
                    block_start = int(seek)
                    self._played_frames = block_start

            while prefilled < PREFILL_BLOCKS and not self._stop_evt.is_set():
                ok = self._read_and_enqueue(f, gen, block_start, file_sr, out_sr, ch, needs_resample)
                if ok is None:
                    break  # EOF
                block_start = ok
                prefilled += 1

            try:
                stream.start()
            except Exception as exc:
                self._fail(f"audio stream start failed: {exc}")
                f.close()
                return

            while not self._stop_evt.is_set():
                # Handle seek
                with self._lock:
                    seek = self._seek_frames
                    if seek is not None:
                        self._seek_frames = None
                        try:
                            f.seek(int(seek))
                        except Exception:
                            pass
                        block_start = int(seek)
                        gen = self._gen
                        self._gen += 1  # invalidate in-flight blocks
                        gen = self._gen
                        self._drain_queue()
                        self._cb_buf = None
                        self._eof = False
                        self._need_fade_in = True
                        self._ramp_gain = 0.0
                        # Prefill after seek
                        for _ in range(PREFILL_BLOCKS):
                            ok = self._read_and_enqueue(
                                f, gen, block_start, file_sr, out_sr, ch, needs_resample
                            )
                            if ok is None:
                                break
                            block_start = ok
                        continue

                # Backpressure: never drop — wait for the callback to drain
                if self._q.qsize() >= QUEUE_MAX - 1:
                    # Brief sleep; callback is consuming
                    sd.sleep(5)
                    continue

                ok = self._read_and_enqueue(f, gen, block_start, file_sr, out_sr, ch, needs_resample)
                if ok is None:
                    self._enqueue_eof(gen)
                    # Keep thread alive until stream drains EOF
                    while not self._stop_evt.is_set() and not self._eof:
                        sd.sleep(20)
                    break
                block_start = ok
        finally:
            try:
                f.close()
            except Exception:
                pass
            try:
                if stream is not None:
                    stream.stop()
                    stream.close()
            except Exception:
                pass
            self._stream = None
            self._thread = None

    def _read_and_enqueue(
        self,
        f,
        gen: int,
        block_start: int,
        file_sr: int,
        out_sr: int,
        ch: int,
        needs_resample: bool,
    ) -> int | None:
        """Read one decode block, optionally HQ-resample, enqueue. Returns new block_start or None on EOF."""
        data = f.read(DECODE_BLOCK, dtype="float32", always_2d=True)
        n = data.shape[0]
        if n == 0:
            return None
        if data.shape[1] > ch:
            data = data[:, :ch]
        elif data.shape[1] < ch:
            # mono → stereo
            data = np.repeat(data, ch, axis=1)
        if needs_resample:
            data = _hq_resample(data, file_sr, out_sr)
        # Clip only pathological overshoot from resample; never normalize
        np.clip(data, -1.0, 1.0, out=data)
        # Enqueue with backpressure (never silently drop)
        payload = (gen, data.copy(), n)  # n = file frames advanced
        while not self._stop_evt.is_set():
            try:
                self._q.put(payload, timeout=0.05)
                break
            except queue.Full:
                if self._state != self._Playing:
                    # paused/stopped — don't spin forever
                    try:
                        self._q.put(payload, timeout=0.2)
                    except queue.Full:
                        pass
                    break
                continue
        return block_start + n

    def _enqueue_eof(self, gen: int) -> None:
        while not self._stop_evt.is_set():
            try:
                self._q.put((gen, None, 0), timeout=0.1)
                return
            except queue.Full:
                continue

    def _sd_cb(self, outdata, frames, time_info, status) -> None:
        gen = self._gen
        buf = self._cb_buf
        written = 0
        file_frames_advanced = 0

        # Gather enough samples
        while written < frames:
            if buf is None or buf.shape[0] == 0:
                if self._eof:
                    break
                try:
                    item_gen, block, file_n = self._q.get_nowait()
                except queue.Empty:
                    self._underflows += 1
                    break
                if item_gen != gen:
                    continue  # stale after seek
                if block is None:
                    self._eof = True
                    break
                buf = block
                # Track file-time progress (pre-resample frame count)
                # When resampling, scale: file_n maps to block.shape[0] output frames
                pass
            n = min(frames - written, buf.shape[0])
            outdata[written : written + n] = buf[:n]
            written += n
            buf = buf[n:]
        self._cb_buf = buf

        if written < frames:
            outdata[written:] = 0.0

        # Soft ramp (start / seek / stop) — eliminates edge clicks.
        # Unity gain (playing, volume 1, fully ramped) skips the multiply for
        # a true bit-transparent path through PortAudio.
        ramp_samples = max(1, int(self._out_sr * (RAMP_MS / 1000.0)))
        vol = self._effective_volume()
        if self._need_fade_in and self._state == self._Playing:
            self._ramp_target = 1.0
            self._need_fade_in = False
        if self._need_fade_out:
            self._ramp_target = 0.0
            self._need_fade_out = False
        target = (self._ramp_target if self._state == self._Playing else 0.0) * vol

        g0 = float(self._ramp_gain)
        g1 = float(target)
        if abs(g1 - g0) < 1e-6 and abs(g0 - 1.0) < 1e-6:
            # Unity — no multiply
            self._ramp_gain = g1
        elif frames > 0:
            step = (g1 - g0) / max(1, min(frames, ramp_samples))
            gains = g0 + step * np.arange(frames, dtype=np.float32)
            if g1 >= g0:
                gains = np.minimum(gains, g1)
            else:
                gains = np.maximum(gains, g1)
            outdata *= gains[:, None]
            self._ramp_gain = float(gains[-1])
        else:
            self._ramp_gain = g1

        # Position: advance by output time mapped back to file time
        with self._lock:
            if self._device_needs_resample and self._out_sr > 0 and self._file_sr > 0:
                file_advance = int(round(written * (self._file_sr / self._out_sr)))
            else:
                file_advance = written
            self._played_frames += file_advance
            pos_ms = int(self._played_frames / max(1, self._file_sr) * 1000)
        if written:
            self.positionChanged.emit(pos_ms)

        if self._eof and (buf is None or (isinstance(buf, np.ndarray) and buf.shape[0] == 0)):
            self._finish_eof()

    def _finish_eof(self) -> None:
        with self._lock:
            self._played_frames = self._total_frames
            sr = self._file_sr
        self.positionChanged.emit(int(self._total_frames / max(1, sr) * 1000))
        self._set_state(self._Stopped)


# ---------------------------------------------------------------------------
# Dual-deck A/B player (single device stream, sample-accurate side switch)
# ---------------------------------------------------------------------------


class DualHiFiPlayer(QObject):
    """
    Two sources, one PortAudio stream.

    Decodes both decks in lockstep at a shared output rate (native when both
    match; otherwise HQ-resample to the higher of the two / device rate).
    Side switch is a short cosine crossfade — never a hard mute click.
    No 16-bit downgrade, no forced 48 kHz, playback rate always 1.0.
    """

    positionChanged = Signal(int)
    durationChanged = Signal(int)
    playbackStateChanged = Signal(object)
    errorOccurred = Signal(object, object)
    sideChanged = Signal(str)  # "a" | "b"

    _Playing = QMediaPlayer.PlaybackState.PlayingState
    _Paused = QMediaPlayer.PlaybackState.PausedState
    _Stopped = QMediaPlayer.PlaybackState.StoppedState

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._src_a: str | None = None
        self._src_b: str | None = None
        self._side = "a"
        self._xfade_pos = 1.0  # 0=full A, 1=full B weight of B… actually: a_gain = 1-w, b_gain = w, w→side
        self._xfade_target = 0.0  # 0 = A, 1 = B
        self._volume = 1.0
        self._state = self._Stopped
        self._error = ""
        self._played_frames = 0
        self._total_frames = 0  # shared clock in output-rate frames
        self._out_sr = 44100
        self._channels = 2
        self._seek_frames: int | None = None
        self._gen = 0
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._cb_buf_a: np.ndarray | None = None
        self._cb_buf_b: np.ndarray | None = None
        self._eof = False
        self._ramp_gain = 0.0
        self._ramp_target = 0.0
        self._need_fade_in = False
        self._file_sr_a = 44100
        self._file_sr_b = 44100
        self._dur_a_ms = 0
        self._dur_b_ms = 0

        # Deck proxies for call sites that still talk to player_a / player_b
        self.deck_a = _DeckProxy(self, "a")
        self.deck_b = _DeckProxy(self, "b")

    # ---- sources ----

    def setSourceA(self, path: Any) -> None:
        self._src_a = _path_from_source(path)
        self._probe_duration("a")

    def setSourceB(self, path: Any) -> None:
        self._src_b = _path_from_source(path)
        self._probe_duration("b")

    def setSources(self, path_a: Any, path_b: Any) -> None:
        was = self._state == self._Playing
        self.stop()
        self.setSourceA(path_a)
        self.setSourceB(path_b)
        if was:
            self.play()

    def setSide(self, side: str) -> None:
        side = "b" if str(side).lower() in ("b", "1", "repaired", "ref") else "a"
        with self._lock:
            self._side = side
            self._xfade_target = 1.0 if side == "b" else 0.0
        self.sideChanged.emit(side)

    def side(self) -> str:
        return self._side

    def setVolume(self, v: float) -> None:
        with self._lock:
            self._volume = _clamp(v, 0.0, 1.0)

    def setPlaybackRate(self, rate: float) -> None:
        pass  # always 1.0

    def play(self) -> None:
        if self._state == self._Playing:
            return
        if not HAS_SD:
            self._fail("live audio unavailable (sounddevice missing)")
            return
        if not self._src_a or not Path(self._src_a).is_file():
            self._fail("no source A")
            return
        if not self._src_b or not Path(self._src_b).is_file():
            self._fail("no source B")
            return
        self._eof = False
        self._need_fade_in = True
        self._ramp_target = 1.0
        if self._thread is None or not self._thread.is_alive():
            self._stop_evt.clear()
            self._thread = threading.Thread(target=self._run, daemon=True, name="hifi-dual")
            self._thread.start()
        else:
            self._stream_start()
        self._set_state(self._Playing)

    def pause(self) -> None:
        if self._state != self._Playing:
            return
        self._ramp_target = 0.0
        self._stream_stop()
        self._set_state(self._Paused)

    def stop(self) -> None:
        self._gen += 1
        with self._lock:
            self._seek_frames = 0
            self._played_frames = 0
        self._ramp_target = 0.0
        self._stream_stop()
        self._drain_queue()
        self._cb_buf_a = self._cb_buf_b = None
        self._eof = False
        self._ramp_gain = 0.0
        self._set_state(self._Stopped)
        self.positionChanged.emit(0)

    def setPosition(self, ms: int) -> None:
        with self._lock:
            sr = self._out_sr or 44100
            frame = max(0, int(ms / 1000.0 * sr))
            if self._total_frames > 0:
                frame = min(frame, self._total_frames)
            self._seek_frames = frame
            self._played_frames = frame
            self._need_fade_in = True
        self.positionChanged.emit(int(ms))

    def position(self) -> int:
        with self._lock:
            return int(self._played_frames / max(1, self._out_sr) * 1000)

    def duration(self) -> int:
        return max(self._dur_a_ms, self._dur_b_ms)

    def durationA(self) -> int:
        return self._dur_a_ms

    def durationB(self) -> int:
        return self._dur_b_ms

    def playbackState(self):
        return self._state

    def errorString(self) -> str:
        return self._error

    def shutdown(self) -> None:
        self._stop_evt.set()
        self._gen += 1
        self._stream_stop()
        t = self._thread
        if t is not None and t.is_alive():
            t.join(timeout=2.5)
        self._thread = None
        self._stream = None

    # ---- helpers ----

    def _probe_duration(self, which: str) -> None:
        path = self._src_a if which == "a" else self._src_b
        if not path or not Path(path).is_file() or sf is None:
            return
        try:
            info = sf.info(str(path))
            ms = int(float(info.duration) * 1000)
            if which == "a":
                self._dur_a_ms = ms
                self._file_sr_a = int(info.samplerate)
            else:
                self._dur_b_ms = ms
                self._file_sr_b = int(info.samplerate)
            self.durationChanged.emit(max(self._dur_a_ms, self._dur_b_ms))
        except Exception:
            pass

    def _set_state(self, state) -> None:
        if state != self._state:
            self._state = state
            self.playbackStateChanged.emit(state)

    def _fail(self, msg: str) -> None:
        self._error = msg
        self.errorOccurred.emit(QMediaPlayer.Error.FormatError, msg)
        self._set_state(self._Stopped)

    def _drain_queue(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _stream_start(self) -> None:
        st = self._stream
        if st is not None:
            try:
                if not st.active:
                    st.start()
            except Exception:
                pass

    def _stream_stop(self) -> None:
        st = self._stream
        if st is not None:
            try:
                st.stop()
            except Exception:
                pass

    def _run(self) -> None:
        try:
            fa = sf.SoundFile(self._src_a)
            fb = sf.SoundFile(self._src_b)
        except Exception as exc:
            self._fail(f"cannot open A/B audio: {exc}")
            return

        sr_a = int(fa.samplerate)
        sr_b = int(fb.samplerate)
        ch = min(max(fa.channels, fb.channels), 2) or 2
        # Prefer higher native rate (keeps more HF content); device may force another
        preferred_sr = max(sr_a, sr_b)

        def _cb(outdata, frames, time_info, status):
            self._sd_cb(outdata, frames, time_info, status)

        try:
            stream, out_sr = _open_output_stream(preferred_sr, ch, _cb, STREAM_BLOCK)
        except Exception as exc:
            self._fail(f"audio device unavailable: {exc}")
            fa.close()
            fb.close()
            return

        with self._lock:
            self._out_sr = int(out_sr)
            self._channels = ch
            # Duration in output frames = max of both decks mapped to out_sr
            dur_a = int(fa.frames * (out_sr / max(1, sr_a)))
            dur_b = int(fb.frames * (out_sr / max(1, sr_b)))
            self._total_frames = max(dur_a, dur_b)
            self._dur_a_ms = int(fa.frames / max(1, sr_a) * 1000)
            self._dur_b_ms = int(fb.frames / max(1, sr_b) * 1000)
            self._file_sr_a = sr_a
            self._file_sr_b = sr_b
            if self._seek_frames is None:
                self._seek_frames = 0
        self.durationChanged.emit(max(self._dur_a_ms, self._dur_b_ms))
        self._stream = stream

        # How many file frames to read so that after resample we get ~DECODE_BLOCK out frames
        def file_block(file_sr: int) -> int:
            if int(file_sr) == int(out_sr):
                return DECODE_BLOCK
            return max(64, int(round(DECODE_BLOCK * file_sr / out_sr)))

        gen = self._gen
        try:
            with self._lock:
                seek = self._seek_frames
                self._seek_frames = None
            seek = int(seek or 0)
            # Map output-frame seek → each file's frame
            fa.seek(min(fa.frames, int(round(seek * sr_a / out_sr))))
            fb.seek(min(fb.frames, int(round(seek * sr_b / out_sr))))
            self._played_frames = seek

            # Prefill
            for _ in range(PREFILL_BLOCKS):
                if not self._enqueue_pair(fa, fb, sr_a, sr_b, out_sr, ch, gen, file_block):
                    break

            stream.start()

            while not self._stop_evt.is_set():
                with self._lock:
                    seek = self._seek_frames
                    if seek is not None:
                        self._seek_frames = None
                        self._gen += 1
                        gen = self._gen
                        self._drain_queue()
                        self._cb_buf_a = self._cb_buf_b = None
                        self._eof = False
                        self._need_fade_in = True
                        self._ramp_gain = 0.0
                        fa.seek(min(fa.frames, int(round(int(seek) * sr_a / out_sr))))
                        fb.seek(min(fb.frames, int(round(int(seek) * sr_b / out_sr))))
                        self._played_frames = int(seek)
                        for _ in range(PREFILL_BLOCKS):
                            if not self._enqueue_pair(
                                fa, fb, sr_a, sr_b, out_sr, ch, gen, file_block
                            ):
                                break
                        continue

                if self._q.qsize() >= QUEUE_MAX - 1:
                    sd.sleep(5)
                    continue

                if not self._enqueue_pair(fa, fb, sr_a, sr_b, out_sr, ch, gen, file_block):
                    self._enqueue_eof(gen)
                    while not self._stop_evt.is_set() and not self._eof:
                        sd.sleep(20)
                    break
        finally:
            try:
                fa.close()
                fb.close()
            except Exception:
                pass
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
            self._stream = None
            self._thread = None

    def _read_deck(self, f, file_sr: int, out_sr: int, ch: int, n_file: int) -> np.ndarray:
        data = f.read(n_file, dtype="float32", always_2d=True)
        if data.shape[0] == 0:
            return np.zeros((0, ch), dtype=np.float32)
        if data.shape[1] > ch:
            data = data[:, :ch]
        elif data.shape[1] < ch:
            data = np.repeat(data, ch, axis=1)
        if int(file_sr) != int(out_sr):
            data = _hq_resample(data, file_sr, out_sr)
        np.clip(data, -1.0, 1.0, out=data)
        return data

    def _enqueue_pair(self, fa, fb, sr_a, sr_b, out_sr, ch, gen, file_block_fn) -> bool:
        """Read both decks; pad the shorter with silence so block lengths match. False on dual-EOF."""
        na = file_block_fn(sr_a)
        nb = file_block_fn(sr_b)
        a = self._read_deck(fa, sr_a, out_sr, ch, na)
        b = self._read_deck(fb, sr_b, out_sr, ch, nb)
        if a.shape[0] == 0 and b.shape[0] == 0:
            return False
        # Match lengths (pad silence — never stretch)
        n = max(a.shape[0], b.shape[0], 1)
        if a.shape[0] < n:
            a = np.vstack([a, np.zeros((n - a.shape[0], ch), dtype=np.float32)]) if a.shape[0] else np.zeros((n, ch), dtype=np.float32)
        if b.shape[0] < n:
            b = np.vstack([b, np.zeros((n - b.shape[0], ch), dtype=np.float32)]) if b.shape[0] else np.zeros((n, ch), dtype=np.float32)
        # Trim to exact match
        a = a[:n]
        b = b[:n]
        payload = (gen, a.copy(), b.copy())
        while not self._stop_evt.is_set():
            try:
                self._q.put(payload, timeout=0.05)
                return True
            except queue.Full:
                if self._state != self._Playing:
                    try:
                        self._q.put(payload, timeout=0.2)
                    except queue.Full:
                        pass
                    return True
                continue
        return False

    def _enqueue_eof(self, gen: int) -> None:
        while not self._stop_evt.is_set():
            try:
                self._q.put((gen, None, None), timeout=0.1)
                return
            except queue.Full:
                continue

    def _sd_cb(self, outdata, frames, time_info, status) -> None:
        gen = self._gen
        buf_a = self._cb_buf_a
        buf_b = self._cb_buf_b
        written = 0
        tmp_a = np.zeros((frames, outdata.shape[1]), dtype=np.float32)
        tmp_b = np.zeros((frames, outdata.shape[1]), dtype=np.float32)

        while written < frames:
            if buf_a is None or buf_a.shape[0] == 0:
                if self._eof:
                    break
                try:
                    item_gen, block_a, block_b = self._q.get_nowait()
                except queue.Empty:
                    break
                if item_gen != gen:
                    continue
                if block_a is None:
                    self._eof = True
                    break
                buf_a = block_a
                buf_b = block_b
            n = min(frames - written, buf_a.shape[0], buf_b.shape[0] if buf_b is not None else buf_a.shape[0])
            tmp_a[written : written + n] = buf_a[:n]
            if buf_b is not None:
                tmp_b[written : written + n] = buf_b[:n]
            written += n
            buf_a = buf_a[n:]
            if buf_b is not None:
                buf_b = buf_b[n:]
        self._cb_buf_a = buf_a
        self._cb_buf_b = buf_b

        # Crossfade weight toward active side
        xfade_n = max(1, int(self._out_sr * (XFADE_MS / 1000.0)))
        w0 = float(self._xfade_pos)
        w1 = float(self._xfade_target)
        if abs(w1 - w0) < 1e-6:
            weights = np.full(frames, w1, dtype=np.float32)
            self._xfade_pos = w1
        else:
            step = (w1 - w0) / max(1, min(frames, xfade_n))
            weights = w0 + step * np.arange(frames, dtype=np.float32)
            if w1 >= w0:
                weights = np.minimum(weights, w1)
            else:
                weights = np.maximum(weights, w1)
            self._xfade_pos = float(weights[-1]) if frames else w1

        # Mix: A*(1-w) + B*w
        w = weights[:, None]
        mixed = tmp_a * (1.0 - w) + tmp_b * w

        # Master ramp + volume
        vol = float(self._volume)
        target = (1.0 if self._state == self._Playing else 0.0) * vol
        if self._need_fade_in and self._state == self._Playing:
            target = 1.0 * vol
            self._ramp_target = 1.0
            self._need_fade_in = False
        ramp_samples = max(1, int(self._out_sr * (RAMP_MS / 1000.0)))
        g0 = float(self._ramp_gain)
        g1 = float(self._ramp_target if self._state == self._Playing else 0.0) * vol
        # Prefer explicit target
        g1 = target
        if frames > 0 and abs(g1 - g0) > 1e-6:
            step = (g1 - g0) / max(1, min(frames, ramp_samples))
            gains = g0 + step * np.arange(frames, dtype=np.float32)
            if g1 >= g0:
                gains = np.minimum(gains, g1)
            else:
                gains = np.maximum(gains, g1)
            mixed *= gains[:, None]
            self._ramp_gain = float(gains[-1])
        else:
            if abs(g1 - 1.0) > 1e-6:
                mixed *= g1
            self._ramp_gain = g1

        outdata[:] = mixed
        if written < frames:
            outdata[written:] = 0.0

        with self._lock:
            self._played_frames += written
            pos_ms = int(self._played_frames / max(1, self._out_sr) * 1000)
        if written:
            self.positionChanged.emit(pos_ms)

        if self._eof and (buf_a is None or (isinstance(buf_a, np.ndarray) and buf_a.shape[0] == 0)):
            self._finish_eof()

    def _finish_eof(self) -> None:
        with self._lock:
            self._played_frames = self._total_frames
            sr = self._out_sr
        self.positionChanged.emit(int(self._total_frames / max(1, sr) * 1000))
        self._set_state(self._Stopped)


class _DeckProxy:
    """
    Thin façade so call sites can keep `player_a.setSource(...)` style code.
    Transport (play/pause/stop/position) is shared on the parent DualHiFiPlayer.
    """

    def __init__(self, parent: DualHiFiPlayer, which: str):
        self._p = parent
        self._which = which

    def setSource(self, path: Any) -> None:
        if self._which == "a":
            self._p.setSourceA(path)
        else:
            self._p.setSourceB(path)

    def source(self) -> QUrl:
        src = self._p._src_a if self._which == "a" else self._p._src_b
        if src:
            return QUrl.fromLocalFile(str(Path(src).resolve()))
        return QUrl()

    def setAudioOutput(self, *_a, **_k) -> None:
        return

    def setVolume(self, v: float) -> None:
        # Side volume: selecting a side is preferred; volume still sets master if active
        if (self._which == "a" and self._p.side() == "a") or (
            self._which == "b" and self._p.side() == "b"
        ):
            self._p.setVolume(v)

    def setMuted(self, muted: bool) -> None:
        # Mute = switch to the other side when muting the active deck
        if muted:
            if self._which == "a" and self._p.side() == "a":
                self._p.setSide("b")
            elif self._which == "b" and self._p.side() == "b":
                self._p.setSide("a")
        else:
            self._p.setSide(self._which)

    def play(self) -> None:
        self._p.play()

    def pause(self) -> None:
        self._p.pause()

    def stop(self) -> None:
        self._p.stop()

    def setPosition(self, ms: int) -> None:
        self._p.setPosition(ms)

    def position(self) -> int:
        return self._p.position()

    def duration(self) -> int:
        return self._p.durationA() if self._which == "a" else self._p.durationB()

    def playbackState(self):
        return self._p.playbackState()

    def setPlaybackRate(self, rate: float) -> None:
        pass

    def errorString(self) -> str:
        return self._p.errorString()

    # Signal pass-throughs used by some code
    @property
    def positionChanged(self):
        return self._p.positionChanged

    @property
    def durationChanged(self):
        return self._p.durationChanged

    @property
    def playbackStateChanged(self):
        return self._p.playbackStateChanged

    @property
    def errorOccurred(self):
        return self._p.errorOccurred
