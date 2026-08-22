"""
LiveFX engine for CoProducer Studio Player.

Realtime streaming playback + FX chain that reacts to knob changes while
audio keeps playing (no render-and-restart). Threading model:

  - A worker thread decodes the file with soundfile, processes each block
    through a stateful pedalboard chain, and pushes PCM into a queue.
  - A PortAudio (sounddevice) callback pulls blocks and writes them to the
    output device. Position is tracked from consumed frames.
  - Settings live in a thread-safe snapshot. Structural changes (bypass,
    effect added/removed, VST swapped) rebuild the chain; parameter changes
    mutate the existing plugin objects in place so filter/compressor state
    (tails, envelopes) never glitches between blocks.

Quality contract (preview must never damage the mix):
  - Dry-by-default: no bleed gate, no declick/DC/edge until the user enables them
  - Native sample rate when the device allows; HQ resample only as fallback
  - Never silently drop frames under load; keep a *shallow* ring so knob edits
    are heard within ~50–80 ms (deep queues made LiveFX lag seconds behind)
  - Low device latency for Studio editing; soft ramps still kill edge clicks
  - Soft cosine ramps on play / pause / stop / seek
  - master_on=False is a true dry bypass (zero DSP)
  - Space-age Artifact Hunter live: RX multi-band / spectral patches are
    pre-baked off the audio thread and *spliced* in realtime (zero STFT under
    the clock). Cubic micro-repair is only a bridge until patches are ready.

The engine exposes a QMediaPlayer-compatible surface (play / pause / stop /
setPosition / position / playbackState / setSource + Qt signals) so the
existing StudioPlayerWindow transport code keeps working unchanged.
"""

from __future__ import annotations

import math
import queue
import threading
from pathlib import Path
from typing import Any

import numpy as np

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtMultimedia import QMediaPlayer

try:
    import soundfile as sf
    import sounddevice as sd
    import pedalboard as pb

    HAS_SD = True
except Exception:  # pragma: no cover
    HAS_SD = False
    sf = sd = pb = None  # type: ignore

from .studio_fx import (
    ArtifactHunt,
    json_effect_to_chain,
)

try:
    from .pcm_player import _hq_resample, _open_output_stream
except Exception:  # pragma: no cover
    _hq_resample = None  # type: ignore
    _open_output_stream = None  # type: ignore

# Studio LiveFX tuning — tight enough to edit, deep enough to avoid underruns.
# Old values (BLOCK=4096, PREFILL=10, QUEUE=48, latency=high) stacked to 1–4 s
# of buffered audio, so knobs felt "seconds behind".
BLOCK = 1024                 # ~21 ms @ 48 kHz per process step
STREAM_BLOCK = 256           # device callback period
QUEUE_MAX = 6                # hard cap ≈ 128 ms of decoded audio
QUEUE_TARGET = 3             # keep ≈ 64 ms ahead — knob edits land fast
PREFILL_BLOCKS = 3           # startup fill ≈ 64 ms
RAMP_MS = 5.0                # soft edge without long fade lag
PAD_SAMPLES = 8              # click-repair lookahead (cubic bridge only)
XFADE_SAMPLES = 128          # chain-rebuild crossfade (~2.7 ms @ 48 k)
# Preview defaults: all processing OFF until the user (or a scan) enables it.
# A noise gate or declicker running "for free" is the #1 source of preview artifacts.
DEFAULT_DECLICK = False
DEFAULT_DEDC = False
DEFAULT_DEEDGE = False
DEFAULT_BLEED_ON = False


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


# ---------------------------------------------------------------------------
# Artifact stages (stateful, block-safe)
# ---------------------------------------------------------------------------

class _RxLiveRepair:
    """
    Space-age live declick.

    Primary path: splice pre-baked RX multi-band / spectral patches (full
    offline quality) into the stream with a tiny delay line for alignment.
    Bridge path: cosine micro-repair for hits until patches finish baking.
    """

    def __init__(self, channels: int, pad: int = PAD_SAMPLES):
        self.pad = max(2, int(pad))
        self.channels = max(1, int(channels))
        self.hist = np.zeros((self.channels, self.pad), dtype=np.float32)
        self.hits: list[tuple[int, int]] = []  # absolute frames fallback
        self.patches: list[dict[str, Any]] = []  # {start, end, audio (ch,n)}
        self.mode: str = "bridge"  # bridge | rx
        self.n_rx = 0
        self.n_bridge = 0

    def reset_state(self) -> None:
        self.hist = np.zeros((self.channels, self.pad), dtype=np.float32)

    def set_clicks(self, clicks: list[tuple[int, int]]) -> None:
        self.hits = list(clicks or [])

    def set_patches(self, patches: list[dict[str, Any]] | None) -> None:
        ready: list[dict[str, Any]] = []
        for p in patches or []:
            try:
                s = int(p["start"])
                e = int(p["end"])
                a = np.asarray(p["audio"], dtype=np.float32)
                if a.ndim == 1:
                    a = a.reshape(1, -1)
                if a.shape[0] != self.channels:
                    if a.shape[0] == 1 and self.channels > 1:
                        a = np.repeat(a, self.channels, axis=0)
                    else:
                        a = a[: self.channels]
                if e < s or a.shape[1] < 2:
                    continue
                # Ensure length matches [start, end]
                need = e - s + 1
                if a.shape[1] != need:
                    if a.shape[1] > need:
                        a = a[:, :need]
                    else:
                        pad = np.zeros((a.shape[0], need - a.shape[1]), dtype=np.float32)
                        a = np.concatenate([a, pad], axis=1)
                ready.append({"start": s, "end": e, "audio": a, "method": p.get("method", "rx")})
            except Exception:
                continue
        ready.sort(key=lambda d: d["start"])
        self.patches = ready
        self.mode = "rx" if ready else "bridge"
        self.n_rx = len(ready)

    def process(self, x: np.ndarray, block_start: int) -> np.ndarray:
        """x: (ch, n). Blocks in order; block_start = first absolute frame of x."""
        if not self.patches and not self.hits:
            return x
        ch, n = x.shape
        if self.pad >= n:
            return x
        # Align channels if device flipped mid-stream
        if ch != self.channels:
            self.channels = ch
            self.hist = np.zeros((ch, self.pad), dtype=np.float32)
            # re-normalize patch channel count lazily in loop

        # Delay line: out[k] = source frame (block_start + k - pad)
        out = np.empty_like(x)
        out[:, self.pad :] = x[:, : -self.pad]
        out[:, : self.pad] = self.hist
        self.hist = x[:, -self.pad :].copy()

        # --- RX patches (primary) ---
        if self.patches:
            frame0 = block_start - self.pad
            frame1 = block_start - self.pad + n  # exclusive
            for p in self.patches:
                ps, pe = int(p["start"]), int(p["end"])
                if pe < frame0 or ps >= frame1:
                    continue
                # overlap in absolute frames
                a_abs = max(ps, frame0)
                b_abs = min(pe, frame1 - 1)
                if b_abs < a_abs:
                    continue
                # map to local indices in `out`
                a0 = a_abs - frame0
                b0 = b_abs - frame0
                p0 = a_abs - ps
                p1 = b_abs - ps + 1
                pa = p["audio"]
                if pa.shape[0] != ch:
                    if pa.shape[0] == 1:
                        pa = np.repeat(pa, ch, axis=0)
                    else:
                        pa = pa[:ch]
                take = min(b0 - a0 + 1, pa.shape[1] - p0, p1 - p0)
                if take <= 0:
                    continue
                out[:, a0 : a0 + take] = pa[:, p0 : p0 + take]
            return out

        # --- Bridge: cosine micro-repair until RX bake completes ---
        for (s, e) in self.hits:
            a0 = s - 3 - block_start + self.pad
            b0 = e + 3 + 1 - block_start + self.pad
            if b0 < 0 or a0 >= n:
                continue
            a0 = max(0, a0)
            b0 = min(n - 1, b0)
            seg = b0 - a0 + 1
            if seg < 2:
                continue
            t = np.linspace(0.0, 1.0, seg)
            w = (0.5 - 0.5 * np.cos(np.pi * t)).astype(np.float32)
            for c in range(ch):
                left = float(out[c, a0])
                right = float(out[c, b0])
                out[c, a0 : b0 + 1] = (left * (1.0 - w) + right * w).astype(np.float32)
        return out


# Back-compat alias
_ClickRepair = _RxLiveRepair


class _DropoutRepair:
    """Fade dropout-edge boundaries at known positions."""

    def __init__(self, channels: int, fade: int = 132):
        self.fade = fade
        self.edges: list[tuple[int, int]] = []

    def set_edges(self, edges: list[tuple[int, int]]) -> None:
        self.edges = edges

    def process(self, x: np.ndarray, block_start: int) -> np.ndarray:
        if not self.edges:
            return x
        ch, n = x.shape
        for (s, e) in self.edges:
            # fade-out into the silence: [s-fade, s) ramps 1 -> 0
            # fade-in after the silence:  [e+1, e+1+fade) ramps 0 -> 1
            if e + 1 + self.fade < block_start or s - self.fade >= block_start + n:
                continue
            for c in range(ch):
                for k in range(self.fade):
                    p = s - self.fade + k - block_start
                    if 0 <= p < n:
                        w = 0.5 * (1.0 + math.cos(math.pi * (k + 1) / self.fade))
                        x[c, p] *= w
                for k in range(self.fade):
                    p = e + 1 + k - block_start
                    if 0 <= p < n:
                        w = 0.5 * (1.0 - math.cos(math.pi * (k + 1) / self.fade))
                        x[c, p] *= w
        return x


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------

class LiveFxEngine(QObject):
    """Realtime playback + live FX. QMediaPlayer-compatible surface."""

    positionChanged = Signal(int)      # ms
    durationChanged = Signal(int)      # ms
    playbackStateChanged = Signal(object)  # QMediaPlayer.PlaybackState
    errorOccurred = Signal(object, object)  # (error, errorString)
    liveSummary = Signal(str)          # human-readable chain state

    _Playing = QMediaPlayer.PlaybackState.PlayingState
    _Paused = QMediaPlayer.PlaybackState.PausedState
    _Stopped = QMediaPlayer.PlaybackState.StoppedState

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._source: str | None = None
        self._settings: dict[str, Any] = {}
        self._tone = {"lo": 0.0, "mid": 0.0, "hi": 0.0, "power": False}
        # Multi-VST chain: [{path, plugin, params, bypass, loaded_path}, ...]
        # Legacy single-slot API maps to slot 0.
        self._vst_slots: list[dict[str, Any]] = []
        self._vst_path: str | None = None  # legacy: first active path
        self._vst_params: dict[str, Any] = {}
        self._vst_plugin: Any = None
        self._vst_loaded_path: str | None = None
        self._volume = 1.0
        self._state = self._Stopped
        self._error: str = ""
        self._played_frames = 0
        self._total_frames = 0
        self._sample_rate = 44100
        self._channels = 2
        self._file_channels = 2
        self._seek_frames: int | None = None
        self._gen = 0
        self._stop_evt = threading.Event()
        self._thread: threading.Thread | None = None
        self._stream = None
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self._chain = None
        self._struct_sig: Any = None
        self._cb_buf: np.ndarray | None = None  # remainder for the audio callback
        self._eof = False
        self._underflows = 0
        self._dc_channels: np.ndarray | None = None  # per-channel DC (worker thread)
        self._dc_cache: dict[str, np.ndarray] = {}
        self._sm: dict[str, Any] = {}  # smoothed param state (click-free)
        self._xfade_chain: list[Any] | None = None  # old chain during rebuild crossfade
        self._xfade_left = 0
        self._out_sr = 44100
        self._device_needs_resample = False
        self._ramp_gain = 0.0
        self._ramp_target = 0.0
        self._need_fade_in = False
        self._boards: dict[str, Any] = {}  # reusable Pedalboard instances per stage
        self._dc_measured = False
        # Space-age RX live patches (pre-baked multi-band / spectral)
        self._rx_patches: list[dict[str, Any]] = []
        self._rx_patch_key: tuple | None = None
        self._rx_patch_status: str = "idle"  # idle | baking | ready | error | bridge
        self._rx_bake_gen = 0
        self._rx_bake_thread: threading.Thread | None = None
        self._eq_band_cfg: list[dict[str, Any]] = []
        self._eq_dyn_env: np.ndarray | None = None

    # ------------------------------------------------------------- public

    def setSource(self, path: str | Path | None) -> None:
        with self._lock:
            self._source = str(path) if path else None
            self._total_frames = 0
            self._played_frames = 0
            self._seek_frames = None
            self._chain = None
            self._struct_sig = None
            self._dc_channels = None
            self._dc_measured = False
            self._xfade_chain = None
            self._xfade_left = 0
            self._boards = {}
            self._rx_patches = []
            self._rx_patch_key = None
            self._rx_patch_status = "idle"
            self._rx_bake_gen += 1
        self._emit_duration_later()

    def _emit_duration_later(self):
        path = self._source
        if not path or not Path(path).is_file():
            return
        try:
            with sf.SoundFile(path) as f:
                frames = f.frames
                sr = f.samplerate
        except Exception:
            return
        with self._lock:
            self._total_frames = frames
            self._sample_rate = int(sr)
        self.durationChanged.emit(int(frames / max(1, sr) * 1000))

    def setSettings(self, settings: dict[str, Any]) -> None:
        settings = dict(settings or {})
        # Optional multi-VST chain from Studio panel
        chain = settings.get("vst_chain")
        if isinstance(chain, list):
            self.setVstChain(chain)
        elif settings.get("vst_path"):
            # Back-compat single path in settings dict
            self.setVstPath(settings.get("vst_path"))
            if isinstance(settings.get("vst_params"), dict):
                self.setVstParams(settings.get("vst_params"))
        with self._lock:
            self._settings = settings
            # Shallow ring (QUEUE_TARGET) means this lands within ~60 ms —
            # no queue drop (dropping would skip audio / click).
        # Kick RX patch bake when artifact settings change (non-blocking)
        try:
            self._maybe_schedule_rx_bake()
        except Exception:
            pass

    def setTone(self, lo: float, mid: float, hi: float, power: bool) -> None:
        with self._lock:
            self._tone = {"lo": lo, "mid": mid, "hi": hi, "power": power}

    def setVstPath(self, path: str | None) -> None:
        """Legacy single-VST API — maps to a one-slot chain."""
        if path and Path(str(path)).exists():
            self.setVstChain([{"path": str(path), "params": dict(self._vst_params), "bypass": False}])
        else:
            self.setVstChain([])

    def setVstParams(self, params: dict[str, Any] | None = None) -> None:
        # Params-only: mutate slot 0 in place (legacy).
        with self._lock:
            self._vst_params = dict(params or {})
            if self._vst_slots:
                self._vst_slots[0]["params"] = dict(params or {})

    def setVstParamsFor(self, path: str, params: dict[str, Any] | None = None) -> None:
        with self._lock:
            key = str(path)
            for slot in self._vst_slots:
                if slot.get("path") == key:
                    slot["params"] = dict(params or {})
                    break

    def setVstBypass(self, path: str, bypass: bool) -> None:
        with self._lock:
            key = str(path)
            for slot in self._vst_slots:
                if slot.get("path") == key:
                    if bool(slot.get("bypass")) != bool(bypass):
                        slot["bypass"] = bool(bypass)
                        self._chain = None
                        self._struct_sig = None
                    break

    def setVstChain(self, chain: list[dict[str, Any]] | None) -> None:
        """
        Replace the multi-VST chain.

        Each item: {path: str, params?: dict, bypass?: bool}.
        Existing plugin instances for the same path are reused (no quality loss
        from reloading, no dropouts from hard swap when only order/params change).
        """
        chain = list(chain or [])
        with self._lock:
            old_by_path = {s.get("path"): s for s in self._vst_slots if s.get("path")}
            new_slots: list[dict[str, Any]] = []
            for item in chain:
                if not isinstance(item, dict):
                    continue
                p = item.get("path")
                if not p or not Path(str(p)).exists():
                    continue
                key = str(Path(p))
                prev = old_by_path.get(key) or {}
                new_slots.append({
                    "path": key,
                    "plugin": prev.get("plugin"),
                    "loaded_path": prev.get("loaded_path"),
                    "params": dict(item.get("params") or prev.get("params") or {}),
                    "bypass": bool(item.get("bypass", prev.get("bypass", False))),
                })
            # Drop plugin refs for removed paths (allow GC)
            new_paths = {s["path"] for s in new_slots}
            for key, old in old_by_path.items():
                if key not in new_paths:
                    old["plugin"] = None
            self._vst_slots = new_slots
            # Legacy mirrors
            if new_slots:
                self._vst_path = new_slots[0]["path"]
                self._vst_params = dict(new_slots[0].get("params") or {})
                self._vst_plugin = new_slots[0].get("plugin")
                self._vst_loaded_path = new_slots[0].get("loaded_path")
            else:
                self._vst_path = None
                self._vst_params = {}
                self._vst_plugin = None
                self._vst_loaded_path = None
            self._chain = None
            self._struct_sig = None

    def getVstChain(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "path": s.get("path"),
                    "params": dict(s.get("params") or {}),
                    "bypass": bool(s.get("bypass")),
                }
                for s in self._vst_slots
            ]

    def get_vst_plugin(self, path: str | None = None):
        """Return a live VST instance (slot 0 or matching path)."""
        with self._lock:
            if self._chain is None:
                self._build_chain()
            if path:
                key = str(path)
                for s in self._vst_slots:
                    if s.get("path") == key:
                        return s.get("plugin")
                return None
            if self._vst_slots:
                return self._vst_slots[0].get("plugin")
            return self._vst_plugin

    def setVstInstance(self, path: str, plugin) -> None:
        """Adopt a plugin instance loaded by the native editor thread.

        The SAME instance both shows its UI and processes audio — edits in the
        native window are heard live with no second load / no quality drop.
        """
        key = str(path)
        with self._lock:
            # Ensure a slot exists for this path
            found = None
            for s in self._vst_slots:
                if s.get("path") == key:
                    found = s
                    break
            if found is None and plugin is not None:
                self._vst_slots.append({
                    "path": key,
                    "plugin": None,
                    "loaded_path": None,
                    "params": {},
                    "bypass": False,
                })
                found = self._vst_slots[-1]
            if found is None:
                return
            if plugin is None:
                found["plugin"] = None
                found["loaded_path"] = None
            else:
                found["plugin"] = plugin
                found["loaded_path"] = key
            # Legacy mirrors for first slot
            if self._vst_slots and self._vst_slots[0].get("path") == key:
                self._vst_path = key
                self._vst_plugin = plugin
                self._vst_loaded_path = key if plugin is not None else None
            self._chain = None
            self._struct_sig = None

    def setVolume(self, v: float) -> None:
        with self._lock:
            self._volume = _clamp(v, 0.0, 1.0)

    def play(self) -> None:
        if self._state == self._Playing:
            return
        self._eof = False
        self._need_fade_in = True
        self._ramp_target = 1.0
        self._error = ""
        if not HAS_SD:
            self._fail("live audio unavailable (sounddevice missing)")
            return
        if self._source is None or not Path(self._source).is_file():
            self._fail("no source file")
            return
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
            # Dead stream / EOF thread / pause left PortAudio unusable → hard restart
            self._restart_worker()
        self._set_state(self._Playing)

    def _restart_worker(self) -> None:
        """Tear down any half-dead audio thread and start a clean worker."""
        self._stop_evt.set()
        self._gen += 1
        st = self._stream
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
        self._drain_q()
        self._cb_buf = None
        self._eof = False
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="livefx")
        self._thread.start()

    def pause(self) -> None:
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
        self._drain_q()
        self._cb_buf = None
        self._eof = False
        self._ramp_gain = 0.0
        self._set_state(self._Stopped)
        self.positionChanged.emit(0)

    def setPosition(self, ms: int) -> None:
        with self._lock:
            if self._sample_rate > 0:
                self._seek_frames = max(0, int(ms / 1000.0 * self._sample_rate))
                self._played_frames = self._seek_frames
                self._need_fade_in = True  # de-click after seek
        self.positionChanged.emit(int(ms))

    def position(self) -> int:
        with self._lock:
            return int(self._played_frames / max(1, self._sample_rate) * 1000)

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
            t.join(timeout=2.0)
        self._thread = None

    # ------------------------------------------------------------- internals

    def _set_state(self, state) -> None:
        if state != self._state:
            self._state = state
            self.playbackStateChanged.emit(state)

    def _fail(self, msg: str) -> None:
        self._error = msg
        self.errorOccurred.emit(QMediaPlayer.Error.FormatError, msg)
        self._set_state(self._Stopped)

    def _stream_start(self) -> None:
        st = self._stream
        if st is not None:
            try:
                if not getattr(st, "active", False):
                    st.start()
            except Exception:
                pass

    def _stream_stop(self) -> None:
        st = self._stream
        if st is not None:
            try:
                if getattr(st, "active", True):
                    st.stop()
            except Exception:
                pass

    # ---- chain -------------------------------------------------------------

    def _signature(self, s: dict, tone: dict, vst_path: str | None) -> tuple:
        eq = tuple(b.get("on", True) for b in s.get("eq_bands", []))
        json_eff = s.get("json_effect")
        json_id = (json_eff or {}).get("id") or (json_eff or {}).get("name") if isinstance(json_eff, dict) else None
        # Multi-VST structure: paths + bypass flags (params mutate in place)
        vst_sig = tuple(
            (slot.get("path"), bool(slot.get("bypass")))
            for slot in self._vst_slots
        )
        # RX live patch readiness is part of structure so chain picks up patches
        rx_n = len(self._rx_patches)
        return (
            s.get("master_on", True),
            s.get("declick", DEFAULT_DECLICK),
            s.get("dedc", DEFAULT_DEDC),
            s.get("deedge", DEFAULT_DEEDGE),
            bool((s.get("bleed") or {}).get("on", DEFAULT_BLEED_ON)),
            eq,
            # EQ structure: type + dynamic flags force rebuild when filter class changes
            tuple(
                (
                    str(b.get("type") or "peaking"),
                    bool(b.get("on", True)),
                    bool(b.get("dynamic")),
                )
                for b in (s.get("eq_bands") or [])
            ),
            round(float(s.get("eq_output_db") or 0.0), 2),
            json_id,
            vst_sig or vst_path,
            tone.get("power", False),
            str(s.get("algorithm") or "auto"),
            round(float(s.get("freq_skew", 0.5)), 3),
            round(float(s.get("min_confidence", 0.45)), 3),
            rx_n,
            self._rx_patch_status,
        )

    def _rx_bake_key(self, s: dict | None = None) -> tuple | None:
        s = s if s is not None else self._settings
        path = self._source
        if not path or not s.get("declick", DEFAULT_DECLICK):
            return None
        hunt = s.get("artifacts")
        hits = []
        if hunt is not None:
            hits = list(getattr(hunt, "hits", None) or [])
        if not hits:
            hits = list(s.get("artifact_hits") or [])
        if not hits and hunt is not None and getattr(hunt, "clicks", None):
            hits = [
                {"start_s": a, "end_s": b, "confidence": 0.7, "kind": "digital", "method": "cubic"}
                for a, b, _ in hunt.clicks
            ]
        if not hits:
            return None
        # Fingerprint first 40 hits + algo params
        fp = []
        for h in hits[:40]:
            if isinstance(h, dict):
                fp.append(
                    (
                        round(float(h.get("start_s", 0)), 4),
                        round(float(h.get("end_s", 0)), 4),
                        str(h.get("kind") or ""),
                        round(float(h.get("confidence", 0)), 2),
                    )
                )
        return (
            str(Path(path).resolve()) if path else None,
            str(s.get("algorithm") or "auto"),
            round(float(s.get("freq_skew", 0.5)), 3),
            round(float(s.get("min_confidence", 0.45)), 3),
            len(hits),
            tuple(fp),
        )

    def _maybe_schedule_rx_bake(self) -> None:
        """Background-bake RX patches when declick + hits + source are ready."""
        with self._lock:
            s = dict(self._settings)
            path = self._source
        if not s.get("declick", DEFAULT_DECLICK) or not path:
            return
        key = self._rx_bake_key(s)
        if key is None:
            return
        with self._lock:
            if key == self._rx_patch_key and self._rx_patch_status in ("ready", "baking"):
                return
            if self._rx_patch_status == "baking" and key == self._rx_patch_key:
                return
            self._rx_bake_gen += 1
            gen = self._rx_bake_gen
            self._rx_patch_status = "baking"
            self._rx_patch_key = key
        algo = str(s.get("algorithm") or "auto")
        skew = float(s.get("freq_skew", 0.5))
        min_c = float(s.get("min_confidence", 0.45))
        hunt = s.get("artifacts")
        hits = list(getattr(hunt, "hits", None) or s.get("artifact_hits") or [])
        if not hits and hunt is not None and getattr(hunt, "clicks", None):
            hits = [
                {
                    "start_s": a,
                    "end_s": b,
                    "confidence": 0.7,
                    "kind": "digital",
                    "method": "cubic",
                    "peak_db": float(db),
                }
                for a, b, db in hunt.clicks
            ]

        def _job(src=path, hits_local=hits, algorithm=algo, freq_skew=skew, min_conf=min_c, g=gen):
            try:
                data, sr = sf.read(str(src), always_2d=True, dtype="float32")
                audio = np.asarray(data.T, dtype=np.float32)
                from .repair_intel import bake_rx_live_patches

                patches = bake_rx_live_patches(
                    audio,
                    int(sr),
                    hits_local,
                    algorithm=algorithm,
                    freq_skew=freq_skew,
                    min_confidence=min_conf,
                )
            except Exception:
                with self._lock:
                    if g == self._rx_bake_gen:
                        self._rx_patch_status = "error"
                        self._rx_patches = []
                try:
                    self.liveSummary.emit("RX bake failed · bridge repair")
                except Exception:
                    pass
                return
            with self._lock:
                if g != self._rx_bake_gen:
                    return  # superseded
                self._rx_patches = patches
                self._rx_patch_status = "ready" if patches else "bridge"
                # Hot-swap into live click stage (no rebuild glitch) + invalidate sig
                if self._chain:
                    for name, stage, _on in self._chain:
                        if name == "clicks" and hasattr(stage, "set_patches"):
                            try:
                                stage.set_patches(patches)
                            except Exception:
                                pass
                self._struct_sig = None
            try:
                n = len(patches)
                methods = {}
                for p in patches:
                    m = str(p.get("method") or "rx")
                    methods[m] = methods.get(m, 0) + 1
                detail = "+".join(f"{k}×{v}" for k, v in methods.items()) or "none"
                self.liveSummary.emit(f"RX live · {n} patch(es) · {detail}")
            except Exception:
                pass

        t = threading.Thread(target=_job, name="rx-live-bake", daemon=True)
        self._rx_bake_thread = t
        t.start()

    def _is_dry(self, s: dict | None = None, tone: dict | None = None) -> bool:
        """True when no DSP should touch the signal (bit-transparent path)."""
        s = s if s is not None else self._settings
        tone = tone if tone is not None else self._tone
        if not s.get("master_on", True):
            return True
        if s.get("declick", DEFAULT_DECLICK):
            return False
        if s.get("dedc", DEFAULT_DEDC):
            return False
        if s.get("deedge", DEFAULT_DEEDGE):
            return False
        if bool((s.get("bleed") or {}).get("on", DEFAULT_BLEED_ON)):
            return False
        for b in s.get("eq_bands", []) or []:
            if b.get("on", True) and abs(float(b.get("gain_db", 0.0))) > 0.05:
                return False
        if tone.get("power", False) and (
            abs(float(tone.get("lo", 0.0)))
            + abs(float(tone.get("mid", 0.0)))
            + abs(float(tone.get("hi", 0.0)))
            > 0.05
        ):
            return False
        if isinstance(s.get("json_effect"), dict):
            return False
        if any(not slot.get("bypass") and slot.get("path") for slot in self._vst_slots):
            return False
        if self._vst_path and not self._vst_slots:
            return False
        wet = float(s.get("wet_dry", 1.0))
        if wet < 0.995 and wet > 0.005:
            pass
        return True

    def _build_chain(self) -> None:
        """(Re)build chain stage objects. Runs in the worker thread."""
        s = self._settings
        ch = self._channels
        hunt: ArtifactHunt | None = s.get("artifacts") or None
        stages: list[Any] = []
        self._boards = {}

        # master_on=False → empty chain (true dry bypass)
        if not s.get("master_on", True):
            if self._chain is not None:
                self._xfade_chain = self._chain
                self._xfade_left = XFADE_SAMPLES
            self._chain = []
            self._sm["wet"] = self._sm.get("wet", 1.0)
            self._sm["vol"] = self._sm.get("vol", float(self._volume))
            return

        click = _RxLiveRepair(ch)
        click.set_clicks([])
        if hunt and bool(s.get("declick", DEFAULT_DECLICK)):
            # Prefer confidence-filtered hits when present
            min_c = float(s.get("min_confidence", 0.45))
            hits = getattr(hunt, "hits", None) or s.get("artifact_hits") or []
            if hits:
                pairs = []
                for h in hits:
                    if not isinstance(h, dict):
                        continue
                    if float(h.get("confidence", 1.0)) < min_c:
                        continue
                    a, b = float(h.get("start_s", 0)), float(h.get("end_s", 0))
                    pairs.append((int(a * self._sample_rate), int(b * self._sample_rate)))
                click.set_clicks(pairs)
            elif hunt.clicks:
                click.set_clicks(
                    [(int(a * self._sample_rate), int(b * self._sample_rate)) for a, b, _ in hunt.clicks]
                )
            # Install pre-baked RX patches when ready (space-age path)
            if self._rx_patches:
                click.set_patches(self._rx_patches)
            else:
                # Kick bake if not already running
                try:
                    self._maybe_schedule_rx_bake()
                except Exception:
                    pass
        stages.append(("clicks", click, bool(s.get("declick", DEFAULT_DECLICK))))

        drop = _DropoutRepair(ch)
        if hunt and hunt.dropout_edges:
            drop.set_edges([(int(a * self._sample_rate), int(b * self._sample_rate)) for a, b in hunt.dropout_edges])
        stages.append(("dropout", drop, bool(s.get("deedge", DEFAULT_DEEDGE))))

        dc = None
        if self._dc_channels is not None:
            dc = np.asarray(self._dc_channels, dtype=np.float32)
        elif hunt is not None and abs(hunt.dc_offset) > 0.004:
            dc = np.full(ch, float(hunt.dc_offset), dtype=np.float32)
        stages.append(("dc", dc, bool(s.get("dedc", DEFAULT_DEDC)) and dc is not None))

        bleed = (s.get("bleed") or {})
        gate = pb.NoiseGate(
            threshold_db=float(bleed.get("threshold_db", -46.0)),
            ratio=float(bleed.get("ratio", 8.0)),
            attack_ms=float(bleed.get("attack_ms", 10.0)),
            release_ms=float(bleed.get("release_ms", 160.0)),
        )
        # Default OFF — a noise gate on dry preview is the main source of pumping/artifacts
        stages.append(("bleed", gate, bool(bleed.get("on", DEFAULT_BLEED_ON))))

        eqs = []
        self._eq_band_cfg = []  # parallel config for dynamic gain + type
        for b in s.get("eq_bands", []) or []:
            if not b.get("on", True):
                continue
            freq = _clamp(float(b.get("freq", 1000.0)), 20.0, 20000.0)
            gain = float(b.get("gain_db", 0.0))
            q = _clamp(float(b.get("q", 0.9)), 0.15, 24.0)
            typ = str(b.get("type") or "peaking").lower()
            plug = None
            try:
                if typ in ("lowshelf", "ls", "low_shelf"):
                    if abs(gain) >= 0.02:
                        plug = pb.LowShelfFilter(cutoff_frequency_hz=freq, gain_db=gain, q=min(q, 2.0))
                elif typ in ("highshelf", "hs", "high_shelf"):
                    if abs(gain) >= 0.02:
                        plug = pb.HighShelfFilter(cutoff_frequency_hz=freq, gain_db=gain, q=min(q, 2.0))
                elif typ in ("notch", "bandstop", "bs"):
                    depth = -abs(gain) if abs(gain) >= 0.5 else -18.0
                    plug = pb.PeakFilter(cutoff_frequency_hz=freq, gain_db=depth, q=max(q, 1.5))
                elif typ in ("highpass", "hpf", "hp"):
                    plug = pb.HighpassFilter(cutoff_frequency_hz=freq)
                elif typ in ("lowpass", "lpf", "lp"):
                    plug = pb.LowpassFilter(cutoff_frequency_hz=freq)
                else:
                    if abs(gain) >= 0.02:
                        plug = pb.PeakFilter(cutoff_frequency_hz=freq, gain_db=gain, q=q)
            except Exception:
                plug = None
            if plug is not None:
                eqs.append(plug)
                self._eq_band_cfg.append(
                    {
                        "freq": freq,
                        "gain_db": gain,
                        "q": q,
                        "type": typ,
                        "dynamic": bool(b.get("dynamic")),
                        "dyn_threshold_db": float(b.get("dyn_threshold_db", -24.0)),
                        "dyn_ratio": float(b.get("dyn_ratio", 2.0)),
                        "dyn_range_db": float(b.get("dyn_range_db", 10.0)),
                        "plugin": plug,
                    }
                )
        out_db = float(s.get("eq_output_db") or 0.0)
        if abs(out_db) >= 0.05:
            try:
                eqs.append(pb.Gain(gain_db=out_db))
            except Exception:
                pass
        # dynamic envelopes (one per dynamic band)
        n_dyn = sum(1 for c in self._eq_band_cfg if c.get("dynamic"))
        self._eq_dyn_env = np.zeros(max(1, len(self._eq_band_cfg)), dtype=np.float64)
        stages.append(("eq", eqs, bool(eqs)))

        tone = self._tone
        tone_chain = []
        if tone.get("power", False):
            tone_chain = [
                pb.LowShelfFilter(cutoff_frequency_hz=120.0, gain_db=float(tone.get("lo", 0.0)), q=0.7),
                pb.PeakFilter(cutoff_frequency_hz=1000.0, gain_db=float(tone.get("mid", 0.0)), q=1.0),
                pb.HighShelfFilter(cutoff_frequency_hz=8000.0, gain_db=float(tone.get("hi", 0.0)), q=0.7),
            ]
        stages.append(("tone", tone_chain, True))

        json_plugins: list[Any] = []
        json_eff = s.get("json_effect")
        if isinstance(json_eff, dict):
            plugs, note = json_effect_to_chain(json_eff)
            if plugs is not None:
                json_plugins = plugs
        stages.append(("json", json_plugins, True))

        # Multi-VST chain — each slot is its own stage; bypass skips processing
        # without unloading (keeps state / quality when re-enabling).
        slots = list(self._vst_slots)
        if not slots and self._vst_path and Path(self._vst_path).exists():
            slots = [{
                "path": self._vst_path,
                "plugin": self._vst_plugin,
                "loaded_path": self._vst_loaded_path,
                "params": dict(self._vst_params),
                "bypass": False,
            }]
            self._vst_slots = slots
        for i, slot in enumerate(slots):
            vst_path = slot.get("path")
            plugin = slot.get("plugin")
            if vst_path and Path(str(vst_path)).exists() and not slot.get("bypass"):
                if plugin is None or slot.get("loaded_path") != vst_path:
                    try:
                        plugin = pb.load_plugin(str(vst_path))
                        slot["plugin"] = plugin
                        slot["loaded_path"] = vst_path
                    except Exception as exc:
                        self._error = f"VST load failed ({Path(str(vst_path)).stem}): {exc}"
                        plugin = None
                        slot["plugin"] = None
            else:
                plugin = None if slot.get("bypass") else plugin
            stages.append((f"vst:{i}", plugin, plugin is not None and not slot.get("bypass")))

        if self._chain is not None:
            self._xfade_chain = self._chain
            self._xfade_left = XFADE_SAMPLES
        self._chain = stages

        # Parameter smoothing state — carried across rebuilds so fresh filters
        # start where the old ones were (no jumps, no clicks on chip toggles).
        prev_eq = self._sm.get("eq") or []
        sm_eq: list[dict[str, float]] = []
        for i, pl in enumerate(eqs):
            prev = prev_eq[i] if i < len(prev_eq) else None
            g = float(prev["g"]) if prev else float(pl.gain_db)
            f = float(prev["f"]) if prev else float(pl.cutoff_frequency_hz)
            try:
                pl.gain_db = g
                pl.cutoff_frequency_hz = f
            except Exception:
                pass
            sm_eq.append({"g": g, "f": f})
        self._sm["eq"] = sm_eq

        prev_tone = self._sm.get("tone")
        sm_tone: dict[str, float] = {}
        for i, key in enumerate(("lo", "mid", "hi")):
            g = float(prev_tone[key]) if prev_tone else (float(tone_chain[i].gain_db) if tone_chain else 0.0)
            if i < len(tone_chain):
                try:
                    tone_chain[i].gain_db = g
                except Exception:
                    pass
            sm_tone[key] = g
        self._sm["tone"] = sm_tone

        prev_bleed = self._sm.get("bleed")
        if prev_bleed:
            try:
                gate.threshold_db = float(prev_bleed.get("thr", gate.threshold_db))
                gate.ratio = float(prev_bleed.get("ratio", gate.ratio))
            except Exception:
                pass
        self._sm["bleed"] = {"thr": float(gate.threshold_db), "ratio": float(gate.ratio)}

        self._sm["wet"] = self._sm.get("wet", float(_clamp(s.get("wet_dry", 1.0), 0.0, 1.0)))
        self._sm["vol"] = self._sm.get("vol", float(self._volume))

    @staticmethod
    def _slew(cur: float, target: float, max_step: float) -> float:
        d = float(target) - cur
        if abs(d) <= max_step:
            return float(target)
        return cur + (max_step if d > 0 else -max_step)

    @staticmethod
    def _slew_log(cur: float, target: float, max_log_step: float) -> float:
        t = max(float(target), 1.0)
        c = max(float(cur), 1.0)
        d = math.log(t / c)
        if abs(d) <= max_log_step:
            return t
        return c * math.exp(max_log_step if d > 0 else -max_log_step)

    def _apply_params(self) -> None:
        """Mutate in place so DSP state persists between blocks. Targets are
        slewed per block (max step per 23ms block) so knob edits never click."""
        s = self._settings
        stages = self._chain or []
        sm = self._sm
        for name, stage, on in stages:
            if name == "bleed":
                b = s.get("bleed") or {}
                cur = sm.setdefault(
                    "bleed",
                    {"thr": float(stage.threshold_db), "ratio": float(stage.ratio)},
                )
                # Per ~21 ms block — large enough to track knobs in <100 ms total
                cur["thr"] = self._slew(cur["thr"], float(b.get("threshold_db", -46.0)), 3.0)
                cur["ratio"] = self._slew(cur["ratio"], float(b.get("ratio", 8.0)), 4.0)
                stage.threshold_db = cur["thr"]
                stage.ratio = cur["ratio"]
                stage.attack_ms = float(b.get("attack_ms", 10.0))
                stage.release_ms = float(b.get("release_ms", 160.0))
            elif name == "eq":
                # Prefer live band cfg (includes type/dynamic); fall back to settings list
                cfg = list(getattr(self, "_eq_band_cfg", None) or [])
                bands = s.get("eq_bands", []) or []
                cur = sm.setdefault("eq", [])
                # Map plugins that expose gain/freq (peaks/shelves)
                pi = 0
                for b in bands:
                    if not b.get("on", True):
                        continue
                    if pi >= len(stage):
                        break
                    pl = stage[pi]
                    pi += 1
                    if pi - 1 >= len(cur):
                        try:
                            g0 = float(getattr(pl, "gain_db", 0.0))
                        except Exception:
                            g0 = 0.0
                        try:
                            f0 = float(getattr(pl, "cutoff_frequency_hz", 1000.0))
                        except Exception:
                            f0 = 1000.0
                        cur.append({"g": g0, "f": f0})
                    c = cur[pi - 1]
                    target_g = float(b.get("gain_db", 0.0))
                    target_f = _clamp(float(b.get("freq", 1000.0)), 20.0, 20000.0)
                    typ = str(b.get("type") or "peaking").lower()
                    if typ in ("notch", "bandstop", "bs"):
                        target_g = -abs(target_g) if abs(target_g) >= 0.5 else -18.0
                    if typ in ("highpass", "lowpass", "hpf", "lpf", "hp", "lp"):
                        # no gain on HP/LP
                        try:
                            pl.cutoff_frequency_hz = self._slew_log(
                                float(getattr(pl, "cutoff_frequency_hz", target_f)), target_f, 0.25
                            )
                        except Exception:
                            pass
                        continue
                    c["g"] = self._slew(c["g"], target_g, 1.5)
                    c["f"] = self._slew_log(c["f"], target_f, 0.25)
                    try:
                        pl.gain_db = c["g"]
                        pl.cutoff_frequency_hz = c["f"]
                        if hasattr(pl, "q"):
                            pl.q = float(b.get("q", 0.9))
                    except Exception:
                        pass
            elif name == "tone":
                tone = self._tone
                on = tone.get("power", False)
                cur = sm.setdefault("tone", {"lo": 0.0, "mid": 0.0, "hi": 0.0})
                for i, key in enumerate(("lo", "mid", "hi")):
                    target = float(tone.get(key, 0.0)) if on else 0.0
                    cur[key] = self._slew(cur[key], target, 1.5)
                    if i < len(stage):
                        stage[i].gain_db = cur[key]
            elif isinstance(name, str) and name.startswith("vst"):
                if stage is not None:
                    # Resolve params for this slot
                    params = self._vst_params
                    if ":" in name:
                        try:
                            idx = int(name.split(":", 1)[1])
                            if 0 <= idx < len(self._vst_slots):
                                params = self._vst_slots[idx].get("params") or {}
                        except Exception:
                            pass
                    for k, v in (params or {}).items():
                        try:
                            setattr(stage, k, v)
                        except Exception:
                            pass
        sm["wet"] = self._slew(sm.get("wet", 1.0), float(s.get("wet_dry", 1.0)), 0.12)
        sm["vol"] = self._slew(sm.get("vol", 1.0), float(self._volume), 0.12)

    def _board_for(self, name: str, plugins: list) -> Any:
        """Reuse Pedalboard instances so IIR/envelope state persists across blocks."""
        board = self._boards.get(name)
        if board is None or list(board) != list(plugins):
            board = pb.Pedalboard(list(plugins))
            self._boards[name] = board
        return board

    def _ride_dynamic_eq(self, x: np.ndarray) -> None:
        """
        Per-block dynamic EQ: for bands with dynamic=True, reduce boosts (or deepen
        cuts) when energy near band f0 exceeds threshold. Uses rfft bin energy.
        """
        cfg = getattr(self, "_eq_band_cfg", None) or []
        if not any(c.get("dynamic") for c in cfg):
            return
        if x.size == 0:
            return
        mono = np.mean(x.astype(np.float64), axis=0) if x.ndim == 2 else x.astype(np.float64)
        n = mono.shape[0]
        if n < 32:
            return
        win = np.hanning(n)
        spec = np.fft.rfft(mono * win)
        mag2 = (np.abs(spec) ** 2) + 1e-18
        freqs = np.fft.rfftfreq(n, d=1.0 / max(1, self._sample_rate))
        env = getattr(self, "_eq_dyn_env", None)
        if env is None or env.shape[0] < len(cfg):
            env = np.zeros(len(cfg), dtype=np.float64)
            self._eq_dyn_env = env
        for i, c in enumerate(cfg):
            if not c.get("dynamic"):
                continue
            pl = c.get("plugin")
            if pl is None or not hasattr(pl, "gain_db"):
                continue
            f0 = float(c.get("freq", 1000.0))
            q = max(0.3, float(c.get("q", 1.0)))
            # bandwidth in Hz ~ f0/Q
            bw = max(30.0, f0 / q)
            mask = (freqs >= f0 - bw) & (freqs <= f0 + bw)
            if not np.any(mask):
                # nearest bin
                bi = int(np.argmin(np.abs(freqs - f0)))
                e = float(mag2[bi])
            else:
                e = float(np.mean(mag2[mask]))
            # convert to dB-ish level (relative)
            level_db = 10.0 * math.log10(e + 1e-18)  # uncalibrated, relative
            # smooth envelope
            env[i] = 0.82 * env[i] + 0.18 * level_db
            thr = float(c.get("dyn_threshold_db", -24.0))
            # map uncalibrated level: use relative excess over rolling mean of env
            # Prefer calibrated via ratio of band energy to full energy
            full = float(np.mean(mag2)) + 1e-18
            rel_db = 10.0 * math.log10((e / full) + 1e-12)
            thr_rel = thr + 40.0  # shift: thr -24 → ~16 rel for typical music
            base = float(c.get("gain_db", 0.0))
            typ = str(c.get("type") or "peaking")
            if typ in ("notch", "bandstop"):
                base = -abs(base) if abs(base) >= 0.5 else -18.0
            ratio = max(1.0, float(c.get("dyn_ratio", 2.0)))
            rng = max(0.5, float(c.get("dyn_range_db", 10.0)))
            excess = max(0.0, rel_db - (thr + 30.0) * 0.15)  # soft threshold on relative band share
            # Also trigger when absolute block RMS is hot
            rms = float(np.sqrt(np.mean(mono * mono)) + 1e-12)
            rms_db = 20.0 * math.log10(rms)
            if rms_db > thr:
                excess = max(excess, (rms_db - thr) * 0.35)
            gr = min(rng, excess * (ratio * 0.55))
            if base >= 0:
                target = base - gr  # pull boost down when loud
            else:
                target = base - gr * 0.4  # deepen cut slightly
            try:
                # soft slew toward dynamic target
                cur = float(pl.gain_db)
                pl.gain_db = cur + 0.35 * (target - cur)
            except Exception:
                pass

    def _run_chain(
        self,
        x: np.ndarray,
        block_start: int,
        wet: float,
        vol: float,
        chain: list[Any] | None = None,
    ) -> np.ndarray:
        """Run one full stage chain over a block (self._chain unless `chain`)."""
        stages = chain if chain is not None else (self._chain or [])
        if not stages:
            if vol < 1.0:
                x = x * vol
            return np.asarray(x, dtype=np.float32)

        dry = None
        if wet < 0.995:
            dry = x.copy()

        any_on = False
        for name, stage, on in stages:
            if not on:
                continue
            any_on = True
            if name == "clicks":
                x = stage.process(x, block_start)
            elif name == "dropout":
                x = stage.process(x, block_start)
            elif name == "dc":
                x = x - stage[:, None]
            elif name == "bleed":
                x = np.asarray(stage(x, self._sample_rate), dtype=np.float32)
            elif name == "eq":
                if stage:
                    # Dynamic ride on band gains from this block's spectrum
                    try:
                        self._ride_dynamic_eq(x)
                    except Exception:
                        pass
                    board = self._board_for(name, stage)
                    x = np.asarray(board(x, self._sample_rate), dtype=np.float32)
            elif name in ("tone", "json"):
                if stage:
                    board = self._board_for(name, stage)
                    x = np.asarray(board(x, self._sample_rate), dtype=np.float32)
            elif isinstance(name, str) and name.startswith("vst"):
                if stage is not None:
                    x = np.asarray(stage(x, self._sample_rate), dtype=np.float32)

        if dry is not None and any_on:
            x = dry * (1.0 - wet) + x * wet
        if vol < 1.0:
            x = x * vol
        return np.asarray(x, dtype=np.float32)

    def _process_block(self, x: np.ndarray, block_start: int) -> np.ndarray:
        if self._chain is None:
            self._build_chain()
        # True dry path: zero DSP, zero allocations beyond the buffer itself
        if self._is_dry() and self._xfade_chain is None:
            vol = _clamp(float(self._sm.get("vol", self._volume)), 0.0, 1.0)
            if vol < 1.0:
                x = x * vol
            return np.asarray(x, dtype=np.float32)

        self._apply_params()
        sm = self._sm
        wet = _clamp(float(sm.get("wet", 1.0)), 0.0, 1.0)
        vol = _clamp(float(sm.get("vol", 1.0)), 0.0, 1.0)

        # A structural change just rebuilt the chain: blend old -> new output
        # over a short window so filter/gate/delay state resets never click.
        old = self._xfade_chain
        left = self._xfade_left
        if old is not None and left > 0:
            x_new = self._run_chain(x.copy(), block_start, wet, vol)
            x_old = self._run_chain(x.copy(), block_start, wet, vol, chain=old)
            k = min(left, x_new.shape[1])
            if k >= 2:
                t = np.linspace(0.0, 1.0, k, dtype=np.float32)
                w = 0.5 - 0.5 * np.cos(np.pi * t)  # 0 -> 1, weight of the new chain
                out = x_new.copy()
                out[:, :k] = x_old[:, :k] * (1.0 - w) + x_new[:, :k] * w
                x = out
            self._xfade_left = left - k
            if self._xfade_left <= 0:
                self._xfade_chain = None
                self._xfade_left = 0
            return np.asarray(x, dtype=np.float32)

        return self._run_chain(x, block_start, wet, vol)

    def _apply_structure_if_needed(self) -> None:
        with self._lock:
            s = dict(self._settings)
            tone = dict(self._tone)
            vst_path = self._vst_path
            vst_params = dict(self._vst_params)
        sig = self._signature(s, tone, vst_path)
        if sig != self._struct_sig:
            self._struct_sig = sig
            self._build_chain()
            self._apply_params()
            self._emit_summary()

    def _emit_summary(self) -> None:
        s = self._settings
        if self._is_dry(s, self._tone):
            self.liveSummary.emit("dry · bit-transparent")
            return
        parts: list[str] = []
        hunt: ArtifactHunt | None = s.get("artifacts")
        if s.get("declick", DEFAULT_DECLICK) and hunt:
            n_hits = len(getattr(hunt, "hits", None) or hunt.clicks or [])
            algo = str(s.get("algorithm") or "auto")
            st = self._rx_patch_status
            n_pat = len(self._rx_patches)
            if st == "ready" and n_pat:
                parts.append(f"RX live×{n_pat} ({algo})")
            elif st == "baking":
                parts.append(f"RX baking… · bridge×{n_hits}")
            elif n_hits:
                parts.append(f"declick bridge×{n_hits}")
        if s.get("dedc", DEFAULT_DEDC) and hunt and abs(hunt.dc_offset) > 0.004:
            parts.append("DC")
        if s.get("deedge", DEFAULT_DEEDGE) and hunt and hunt.dropout_edges:
            parts.append(f"edges {len(hunt.dropout_edges)}")
        bleed = s.get("bleed") or {}
        if bleed.get("on", DEFAULT_BLEED_ON):
            parts.append(f"gate@{float(bleed.get('threshold_db', -46.0)):.0f}dB")
        eq_on = sum(1 for b in s.get("eq_bands", []) if b.get("on", True) and abs(float(b.get("gain_db", 0.0))) > 0.05)
        if eq_on:
            parts.append(f"EQ {eq_on}band")
        tone = self._tone
        if tone.get("power", False):
            parts.append("tone")
        if isinstance(s.get("json_effect"), dict):
            parts.append("JSON")
        active_vsts = [
            Path(s["path"]).stem
            for s in self._vst_slots
            if s.get("path") and not s.get("bypass")
        ]
        if active_vsts:
            parts.append("VST " + "+".join(active_vsts[:4]) + ("…" if len(active_vsts) > 4 else ""))
        elif self._vst_path:
            parts.append(f"VST {Path(self._vst_path).stem}")
        self.liveSummary.emit(" · ".join(parts) if parts else "dry · bit-transparent")

    # ---- worker thread ------------------------------------------------------

    def _measure_dc(self, path: str, ch: int) -> np.ndarray | None:
        """Per-channel mean DC of a file, cached per path."""
        if path in self._dc_cache:
            return self._dc_cache[path]
        dc = None
        try:
            with sf.SoundFile(path) as f:
                run = np.zeros(min(f.channels, ch), dtype=np.float64)
                n = 0
                while True:
                    blk = f.read(BLOCK * 4, dtype="float32", always_2d=True)
                    if blk.shape[0] == 0:
                        break
                    b = blk[:, : ch]
                    run += b.sum(axis=0)
                    n += b.shape[0]
                if n:
                    run /= n
                    if np.max(np.abs(run)) > 0.004:
                        dc = run.astype(np.float32)
        except Exception:
            dc = None
        if dc is not None:
            self._dc_cache[path] = dc
        return dc

    def _run(self) -> None:
        path = self._source
        try:
            f = sf.SoundFile(path)
        except Exception as exc:
            self._fail(f"cannot open audio: {exc}")
            return
        file_sr = int(f.samplerate)
        file_ch = min(int(f.channels), 2) or 1
        # Always present stereo to the device — mono-only OutputStream fails on many
        # Windows interfaces (M-Audio, WASAPI shared, etc.)
        out_ch = 2
        with self._lock:
            self._sample_rate = file_sr
            self._channels = out_ch
            self._file_channels = file_ch
            self._total_frames = f.frames
            if self._seek_frames is None:
                self._seek_frames = 0
        # Defer full-file DC scan until de-DC is actually enabled (was blocking play start)
        self._dc_channels = None
        self._dc_measured = False
        self.durationChanged.emit(int(f.frames / max(1, file_sr) * 1000))

        stream = None
        out_sr = file_sr
        try:
            if _open_output_stream is not None:
                # Start with "high" for reliable open on pro interfaces, then
                # _open_output_stream falls through latencies if needed.
                # Studio still feels tight because of the shallow PCM ring.
                stream, out_sr = _open_output_stream(
                    file_sr, out_ch, self._sd_cb, STREAM_BLOCK, latency="high"
                )
            else:
                stream = sd.OutputStream(
                    samplerate=file_sr,
                    channels=out_ch,
                    dtype="float32",
                    blocksize=STREAM_BLOCK,
                    callback=self._sd_cb,
                    latency="high",
                )
                out_sr = file_sr
        except Exception as exc:
            self._fail(f"audio device unavailable: {exc}")
            f.close()
            return

        # Honor negotiated channel count if device only opened mono
        try:
            neg = int(getattr(stream, "_nodaw_channels", out_ch) or out_ch)
            if neg in (1, 2):
                out_ch = neg
                with self._lock:
                    self._channels = out_ch
        except Exception:
            pass

        needs_resample = int(out_sr) != int(file_sr)
        with self._lock:
            self._out_sr = int(out_sr)
            self._device_needs_resample = needs_resample
        self._stream = stream

        gen = self._gen
        block_start = 0
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

            # Shallow prefill — enough to avoid underrun, not enough to lag edits
            for _ in range(PREFILL_BLOCKS):
                if self._stop_evt.is_set():
                    break
                self._maybe_measure_dc(path, out_ch)
                self._apply_structure_if_needed()
                ok = self._read_process_enqueue(f, gen, block_start, file_sr, out_sr, needs_resample)
                if ok is None:
                    self._enqueue_eof(gen)
                    break
                block_start = ok

            try:
                stream.start()
            except Exception as exc:
                self._fail(f"audio stream start failed: {exc}")
                f.close()
                return

            while not self._stop_evt.is_set():
                # pending seek?
                with self._lock:
                    seek = self._seek_frames
                    if seek is not None:
                        self._seek_frames = None
                        try:
                            f.seek(int(seek))
                        except Exception:
                            pass
                        block_start = int(seek)
                        self._gen += 1
                        gen = self._gen
                        self._chain = None
                        self._struct_sig = None
                        self._boards = {}
                        self._drain_q()
                        self._cb_buf = None
                        self._eof = False
                        self._need_fade_in = True
                        self._ramp_gain = 0.0
                        for _ in range(PREFILL_BLOCKS):
                            self._maybe_measure_dc(path, out_ch)
                            self._apply_structure_if_needed()
                            ok = self._read_process_enqueue(
                                f, gen, block_start, file_sr, out_sr, needs_resample
                            )
                            if ok is None:
                                break
                            block_start = ok
                        continue

                # Keep the ring shallow — this is what makes LiveFX feel realtime.
                # Deep queues were the main "knobs lag seconds behind" bug.
                qsize = self._q.qsize()
                if qsize >= QUEUE_MAX:
                    sd.sleep(2)
                    continue
                if qsize >= QUEUE_TARGET:
                    sd.sleep(1)
                    continue

                self._maybe_measure_dc(path, out_ch)
                self._apply_structure_if_needed()
                ok = self._read_process_enqueue(f, gen, block_start, file_sr, out_sr, needs_resample)
                if ok is None:
                    self._enqueue_eof(gen)
                    while not self._stop_evt.is_set() and not self._eof:
                        sd.sleep(10)
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

    def _drain_q(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass

    def _maybe_measure_dc(self, path: str, ch: int) -> None:
        """Lazy DC measure — only when de-DC is enabled, never blocks dry play."""
        if self._dc_measured:
            return
        s = self._settings or {}
        if not s.get("dedc", DEFAULT_DEDC):
            return
        self._dc_measured = True
        self._dc_channels = self._measure_dc(path, ch)

    def _read_process_enqueue(
        self,
        f,
        gen: int,
        block_start: int,
        file_sr: int,
        out_sr: int,
        needs_resample: bool,
    ) -> int | None:
        """Read → FX → optional HQ resample → enqueue. Returns new block_start or None on EOF."""
        data = f.read(BLOCK, dtype="float32", always_2d=True)
        n = data.shape[0]
        if n == 0:
            return None
        x = data.T.astype(np.float32)
        if x.shape[0] > 2:
            x = x[:2]
        # Upmix mono → stereo (or match negotiated out channels)
        out_ch = int(getattr(self, "_channels", 2) or 2)
        if x.shape[0] == 1 and out_ch >= 2:
            x = np.repeat(x, 2, axis=0)
        elif x.shape[0] > out_ch:
            x = x[:out_ch]
        elif x.shape[0] < out_ch:
            pad = np.zeros((out_ch - x.shape[0], x.shape[1]), dtype=np.float32)
            x = np.concatenate([x, pad], axis=0)
        try:
            x = self._process_block(x, block_start)
        except Exception:
            pass  # keep raw on DSP error rather than silence
        # Resample after FX so filters run at the file's native rate
        if needs_resample and _hq_resample is not None:
            y = _hq_resample(x.T, file_sr, out_sr).T.astype(np.float32)
            x = y
        np.clip(x, -1.0, 1.0, out=x)
        # Ensure contiguous (ch, n) → enqueue as (frames, ch) for PortAudio
        if x.ndim != 2:
            x = np.asarray(x, dtype=np.float32).reshape(out_ch, -1)
        payload = (gen, np.ascontiguousarray(x.T))
        while not self._stop_evt.is_set():
            try:
                self._q.put(payload, timeout=0.05)
                break
            except queue.Full:
                if self._state != self._Playing:
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
                self._q.put((gen, None), timeout=0.1)
                return
            except queue.Full:
                continue

    def _sd_cb(self, outdata, frames, time_info, status) -> None:
        gen = self._gen
        buf = self._cb_buf
        written = 0
        # PortAudio may pass a non-writable view; force zeros first on underrun path
        try:
            out_ch = outdata.shape[1] if outdata.ndim == 2 else 1
        except Exception:
            out_ch = 2
        while written < frames:
            if buf is None or buf.shape[0] == 0:
                if self._eof:
                    break
                try:
                    item_gen, block = self._q.get_nowait()
                except queue.Empty:
                    self._underflows += 1
                    break
                if item_gen != gen:
                    continue
                if block is None:
                    self._eof = True
                    break
                buf = block
                # Channel-align block to device buffer
                if buf.ndim == 1:
                    buf = buf.reshape(-1, 1)
                if buf.shape[1] < out_ch:
                    buf = np.repeat(buf, out_ch, axis=1)[:, :out_ch]
                elif buf.shape[1] > out_ch:
                    buf = buf[:, :out_ch]
            n = min(frames - written, buf.shape[0])
            try:
                outdata[written : written + n, :out_ch] = buf[:n, :out_ch]
            except Exception:
                # shape mismatch fallback
                chunk = buf[:n]
                if chunk.ndim == 1:
                    outdata[written : written + n, 0] = chunk
                else:
                    c = min(out_ch, chunk.shape[1])
                    outdata[written : written + n, :c] = chunk[:, :c]
            written += n
            buf = buf[n:]
        self._cb_buf = buf
        if written < frames:
            # Hold last sample then fade to zero over a few samples (less clicky than hard zero)
            if written > 0:
                tail = outdata[written - 1 : written].copy()
                fade = min(32, frames - written)
                for i in range(fade):
                    outdata[written + i] = tail * (1.0 - (i + 1) / fade)
                if written + fade < frames:
                    outdata[written + fade :] = 0.0
            else:
                outdata[written:] = 0.0

        # Soft ramp on start / seek / stop
        vol = float(self._volume)
        target = (1.0 if self._state == self._Playing else 0.0) * vol
        if self._need_fade_in and self._state == self._Playing:
            target = 1.0 * vol
            self._ramp_target = 1.0
            self._need_fade_in = False
        g0 = float(self._ramp_gain)
        g1 = float(target)
        out_sr = self._out_sr or self._sample_rate or 44100
        ramp_samples = max(1, int(out_sr * (RAMP_MS / 1000.0)))
        if frames > 0 and abs(g1 - g0) > 1e-6:
            step = (g1 - g0) / max(1, min(frames, ramp_samples))
            gains = g0 + step * np.arange(frames, dtype=np.float32)
            if g1 >= g0:
                gains = np.minimum(gains, g1)
            else:
                gains = np.maximum(gains, g1)
            outdata *= gains[:, None]
            self._ramp_gain = float(gains[-1])
        elif abs(g1 - 1.0) > 1e-6:
            outdata *= g1
            self._ramp_gain = g1
        else:
            self._ramp_gain = g1

        with self._lock:
            if self._device_needs_resample and self._out_sr > 0 and self._sample_rate > 0:
                file_advance = int(round(written * (self._sample_rate / self._out_sr)))
            else:
                file_advance = written
            self._played_frames += file_advance
        if written:
            self.positionChanged.emit(
                int(self._played_frames / max(1, self._sample_rate) * 1000)
            )
        if self._eof and (buf is None or (isinstance(buf, np.ndarray) and buf.shape[0] == 0)):
            self._finish_eof()

    def _finish_eof(self) -> None:
        with self._lock:
            self._played_frames = self._total_frames
        self.positionChanged.emit(int(self._total_frames / max(1, self._sample_rate) * 1000))
        self._set_state(self._Stopped)
