"""
VST3 parameter editor for CoProducer Studio Player.

Loads a VST3 plugin via pedalboard, lists every exposed parameter with a
native control (spinbox / combo), and mutates the SAME plugin instance the
live engine is processing — so edits are heard instantly while audio plays.

Also produces a {python_name: value} snapshot used by the offline renderer.
"""

from __future__ import annotations

import ctypes
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .theme import Color, Radius, Space, Type


def plugin_path_ok(path: str | Path | None) -> bool:
    """True if path exists as a loadable VST (file or .vst3 bundle directory)."""
    if not path or path is False:
        return False
    try:
        p = Path(str(path))
        return p.exists()
    except Exception:
        return False


def _param_rows(plugin) -> list[tuple[str, Any, Any]]:
    """Return [(python_name, display_label, parameter)] sorted by label."""
    try:
        params = plugin.parameters
    except Exception:
        return []
    rows = []
    for name, par in (params or {}).items():
        try:
            label = getattr(par, "python_name", None) or name
        except Exception:
            label = name
        rows.append((name, label, par))
    return rows


class _RowControl(QWidget):
    """One parameter row: name label + native editor + reset."""

    def __init__(
        self,
        plugin,
        python_name: str,
        display: str,
        par,
        on_change: Callable[[str, Any], None],
        on_reset: Callable[[str], None],
        initial: dict[str, Any],
        parent=None,
    ):
        super().__init__(parent)
        self.plugin = plugin
        self.python_name = python_name
        self.initial = initial

        lay = QHBoxLayout(self)
        lay.setContentsMargins(Space.SM, 2, Space.SM, 2)
        lay.setSpacing(Space.MD)

        name = QLabel(display)
        name.setStyleSheet(
            f"font-size: {Type.CAPTION}px; font-weight: 600; color: {Color.TEXT};"
        )
        name.setMinimumWidth(170)
        lay.addWidget(name)
        lay.addStretch()

        ptype = ""
        try:
            ptype = str(getattr(par, "type", "") or "")
        except Exception:
            pass
        valid = None
        try:
            valid = list(getattr(par, "valid_values", []) or [])
        except Exception:
            valid = None
        is_str_like = False
        try:
            is_str_like = any(isinstance(v, str) for v in (valid or []))
        except Exception:
            is_str_like = False

        self._control: QWidget | None = None

        if valid and is_str_like:
            combo = QComboBox()
            combo.addItems([str(v) for v in valid])
            try:
                cur = getattr(plugin, python_name)
                idx = [str(v) for v in valid].index(str(cur))
                combo.setCurrentIndex(idx)
            except Exception:
                pass

            def _on_combo(i):
                if 0 <= i < len(valid):
                    try:
                        setattr(plugin, python_name, valid[i])
                    except Exception:
                        pass
                    on_change(python_name, valid[i])

            combo.currentIndexChanged.connect(_on_combo)
            combo.setMinimumWidth(220)
            self._control = combo
        elif ptype.lower().startswith("bool") or ptype.lower() in {"boolean", "switch"}:
            cb = QCheckBox()
            try:
                cb.setChecked(bool(getattr(plugin, python_name)))
            except Exception:
                pass

            def _on_cb(on):
                try:
                    setattr(plugin, python_name, bool(on))
                except Exception:
                    pass
                on_change(python_name, bool(on))

            cb.toggled.connect(_on_cb)
            cb.setStyleSheet("background: transparent;")
            self._control = cb
        else:
            try:
                r = par.range
                lo, hi, step = float(r[0]), float(r[1]), float(r[2] or 0.01)
            except Exception:
                lo, hi, step = 0.0, 1.0, 0.01
            try:
                step = min(step, (hi - lo) / 1000.0)
            except Exception:
                pass
            if step <= 0:
                step = 0.01
            sb = QDoubleSpinBox()
            sb.setRange(lo, hi)
            sb.setSingleStep(step)
            sb.setDecimals(max(1, min(6, int(abs(math.log10(step))) if step < 1 else 1)))
            try:
                units = str(getattr(par, "units", "") or "")
            except Exception:
                units = ""
            if units and units not in {"", "normalized", "dB"}:
                sb.setSuffix(f" {units}")
            try:
                sb.setValue(float(getattr(plugin, python_name)))
            except Exception:
                pass
            sb.setMinimumWidth(160)

            def _on_spin(v):
                try:
                    setattr(plugin, python_name, float(v))
                except Exception:
                    pass
                on_change(python_name, float(v))

            sb.valueChanged.connect(_on_spin)
            self._control = sb
        if self._control is not None:
            lay.addWidget(self._control, 0, Qt.AlignRight)

        reset = QPushButton("↺")
        reset.setFixedSize(30, 28)
        reset.setCursor(Qt.PointingHandCursor)
        reset.setToolTip(f"Reset {display} to its default")
        reset.setStyleSheet(f"""
            QPushButton {{
                background: {Color.ELEVATED}; color: {Color.MUTED};
                border: 1px solid {Color.LINE}; border-radius: 8px; font-weight: 800;
            }}
            QPushButton:hover {{ border-color: {Color.ACCENT}; color: {Color.ACCENT}; }}
        """)
        reset.clicked.connect(lambda: on_reset(python_name))
        lay.addWidget(reset)


class VstEditorDialog(QDialog):
    """
    Live parameter editor for a loaded VST3 plugin.

    The caller passes the plugin instance it owns (the same one the live
    engine processes) plus a change callback so it can persist a param
    snapshot for offline rendering.
    """

    def __init__(
        self,
        plugin,
        vst_path: Path | str,
        *,
        on_change: Callable[[str, Any], None] | None = None,
        initial_params: dict[str, Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.plugin = plugin
        self.vst_path = Path(vst_path)
        self.on_change = on_change
        self._initial: dict[str, Any] = dict(initial_params or {})
        self._current: dict[str, Any] = dict(initial_params or {})
        self.setWindowTitle(f"{self.vst_path.stem} — plugin editor")
        self.setModal(False)
        self.resize(560, 620)
        self.setMinimumSize(460, 400)

        self.setStyleSheet(f"""
            QDialog {{
                background: {Color.BG}; color: {Color.TEXT};
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.25)};
            }}
            QLabel {{ background: transparent; color: {Color.TEXT}; }}
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.SM, Space.LG, Space.SM)
        root.setSpacing(Space.SM)

        hdr = QHBoxLayout()
        title = QLabel(self.vst_path.stem)
        title.setStyleSheet(
            f"font-size: {Type.H2}px; font-weight: 700; font-family: {Type.DISPLAY};"
        )
        hdr.addWidget(title)
        hdr.addStretch()
        meta = []
        for attr in ("manufacturer_name", "category"):
            try:
                v = getattr(plugin, attr)
                if v:
                    meta.append(str(v))
            except Exception:
                pass
        if meta:
            sub = QLabel(" · ".join(meta))
            sub.setStyleSheet(f"font-size: {Type.TINY}px; color: {Color.MUTED};")
            hdr.addWidget(sub)
        root.addLayout(hdr)

        tip = QLabel(
            "Edits apply live to playback. Changes are kept for Render / Save."
        )
        tip.setStyleSheet(f"font-size: {Type.TINY}px; color: {Color.MUTED};")
        root.addWidget(tip)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self._body_lay = QVBoxLayout(body)
        self._body_lay.setContentsMargins(0, 0, 0, 0)
        self._body_lay.setSpacing(2)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        self._status = QLabel("")
        self._status.setStyleSheet(f"font-size: {Type.TINY}px; color: {Color.MUTED};")
        foot.addWidget(self._status)
        foot.addStretch()
        btn_reset_all = QPushButton("Reset all")
        btn_reset_all.setCursor(Qt.PointingHandCursor)
        btn_reset_all.setFixedHeight(30)
        btn_reset_all.clicked.connect(self._reset_all)
        foot.addWidget(btn_reset_all)
        btn_close = QPushButton("Close")
        btn_close.setObjectName("Primary")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setFixedHeight(30)
        btn_close.clicked.connect(self.accept)
        foot.addWidget(btn_close)
        root.addLayout(foot)

        self._rows: list[_RowControl] = []
        self._populate()
        self._load_initial()

    # ------------------------------------------------------------- populate

    def _populate(self):
        for python_name, display, par in _param_rows(self.plugin):
            row = _RowControl(
                self.plugin,
                python_name,
                display,
                par,
                on_change=self._changed,
                on_reset=self._reset_param,
                initial=self._initial,
                parent=self,
            )
            self._rows.append(row)
            self._body_lay.addWidget(row)
        self._body_lay.addStretch()

    def _load_initial(self):
        # capture plugin defaults for reset (params not in the caller snapshot)
        for python_name, _display, _par in _param_rows(self.plugin):
            if python_name in self._initial:
                continue
            try:
                self._initial[python_name] = getattr(self.plugin, python_name)
            except Exception:
                pass

    # ------------------------------------------------------------- callbacks

    def _changed(self, python_name: str, value: Any) -> None:
        self._current[python_name] = value
        self._status.setText(f"{python_name} = {value}")
        if self.on_change:
            try:
                self.on_change(python_name, value)
            except Exception:
                pass

    def _reset_param(self, python_name: str) -> None:
        default = self._initial.get(python_name)
        try:
            if default is not None:
                setattr(self.plugin, python_name, default)
        except Exception:
            return
        self._current[python_name] = default
        self._refresh_row(python_name, default)
        self._status.setText(f"{python_name} → default")
        if self.on_change:
            try:
                self.on_change(python_name, default)
            except Exception:
                pass

    def _reset_all(self) -> None:
        for python_name, _display, _par in _param_rows(self.plugin):
            self._reset_param(python_name)
        self._status.setText("all parameters reset")

    def _refresh_row(self, python_name: str, value: Any) -> None:
        for row in self._rows:
            if row.python_name != python_name:
                continue
            ctrl = row._control
            if isinstance(ctrl, QDoubleSpinBox):
                ctrl.blockSignals(True)
                try:
                    ctrl.setValue(float(value))
                except Exception:
                    pass
                ctrl.blockSignals(False)
            elif isinstance(ctrl, QCheckBox):
                ctrl.blockSignals(True)
                ctrl.setChecked(bool(value))
                ctrl.blockSignals(False)
            elif isinstance(ctrl, QComboBox):
                ctrl.blockSignals(True)
                idx = ctrl.findText(str(value))
                if idx >= 0:
                    ctrl.setCurrentIndex(idx)
                ctrl.blockSignals(False)
            break



class VstNativeEditor:
    """Legacy single-slot wrapper around VstHostManager."""

    def __init__(self, parent=None):
        self._mgr = VstHostManager(parent)
        self._path: str | None = None

    @property
    def is_open(self) -> bool:
        return self._mgr.is_open(self._path) if self._path else False

    @property
    def plugin(self):
        return self._mgr.plugin(self._path) if self._path else None

    def open(self, path: Path | str, *, on_loaded: Callable[[str, Any], None] | None = None) -> None:
        self._path = str(path)
        self._mgr.open(path, on_loaded=on_loaded, replace_others=True)

    def close(self, timeout: float = 1.5) -> None:
        if self._path:
            self._mgr.close(self._path, timeout=timeout)
        else:
            self._mgr.close_all(timeout=timeout)

    def close_all(self, timeout: float = 1.5) -> None:
        self._mgr.close_all(timeout=timeout)

    def params(self) -> dict[str, Any]:
        return {}


class VstChromeBar(QDialog):
    """Always-on-top strip: Close UI · Bypass · Remove."""

    def __init__(
        self,
        path: str,
        *,
        on_close_ui: Callable[[str], None] | None = None,
        on_remove: Callable[[str], None] | None = None,
        on_bypass: Callable[[str, bool], None] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.path = str(path)
        self._on_close_ui = on_close_ui
        self._on_remove = on_remove
        self._on_bypass = on_bypass
        self.setWindowTitle(f"{Path(path).stem} — VST")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setFixedHeight(52)
        self.setMinimumWidth(420)
        self.resize(480, 52)
        self.setStyleSheet(f"""
            QDialog {{
                background: {Color.SURFACE}; color: {Color.TEXT};
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.45)};
                border-radius: 8px;
            }}
            QLabel {{ background: transparent; color: {Color.TEXT}; }}
            QPushButton {{
                background: {Color.ELEVATED}; color: {Color.TEXT};
                border: 1px solid {Color.LINE}; border-radius: 6px;
                padding: 4px 10px; font-weight: 700; font-size: 11px;
            }}
            QPushButton:hover {{ border-color: {Color.ACCENT}; }}
            QPushButton#CloseBtn:hover {{
                background: {Color.ERROR}; color: {Color.WHITE}; border-color: {Color.ERROR};
            }}
            QPushButton#RemoveBtn:hover {{ border-color: {Color.ERROR}; color: {Color.ERROR}; }}
            QCheckBox {{ color: {Color.MUTED}; spacing: 6px; }}
        """)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 8, 6)
        lay.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(Path(path).stem)
        name.setStyleSheet(
            f"font-size: 12px; font-weight: 700; font-family: {Type.DISPLAY}; color: {Color.ACCENT_SOFT};"
        )
        name.setToolTip(str(path))
        col.addWidget(name)
        self._hint = QLabel("native UI · plugin window X / Esc / F4 to close")
        self._hint.setStyleSheet(f"font-size: 9px; color: {Color.MUTED};")
        col.addWidget(self._hint)
        lay.addLayout(col, 1)
        self._bypass = QCheckBox("Bypass")
        self._bypass.toggled.connect(self._emit_bypass)
        lay.addWidget(self._bypass)
        btn_close = QPushButton("Close UI")
        btn_close.setObjectName("CloseBtn")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self._emit_close_ui)
        lay.addWidget(btn_close)
        btn_rm = QPushButton("Remove")
        btn_rm.setObjectName("RemoveBtn")
        btn_rm.setCursor(Qt.PointingHandCursor)
        btn_rm.clicked.connect(self._emit_remove)
        lay.addWidget(btn_rm)
        self._drag_origin = None

    def set_status(self, text: str) -> None:
        self._hint.setText(text)

    def set_bypass(self, on: bool) -> None:
        self._bypass.blockSignals(True)
        self._bypass.setChecked(bool(on))
        self._bypass.blockSignals(False)

    def _emit_close_ui(self) -> None:
        if self._on_close_ui:
            self._on_close_ui(self.path)

    def _emit_remove(self) -> None:
        if self._on_remove:
            self._on_remove(self.path)

    def _emit_bypass(self, on: bool) -> None:
        if self._on_bypass:
            self._on_bypass(self.path, bool(on))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_origin is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_origin)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_origin = None


class _VstSlot:
    def __init__(self, path: str):
        self.path = str(path)
        self.close_evt: threading.Event | None = None
        self.alive = False
        self.error: str | None = None
        self.plugin = None
        self.failed = False


def _is_gui_thread() -> bool:
    app = QApplication.instance()
    if app is None:
        return True
    try:
        return QThread.currentThread() == app.thread()
    except Exception:
        return True


class VstHostManager(QObject):
    """
    Native VST UI host. pedalboard requires show_editor() on the **main thread**.
    Only one native UI can block the main thread at a time; multiple plugins
    remain in the DSP chain.
    """

    pluginLoaded = Signal(str, object)
    pluginFailed = Signal(str, str)
    pluginClosed = Signal(str)
    pluginRemoved = Signal(str)
    bypassChanged = Signal(str, bool)
    statusMessage = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots: dict[str, _VstSlot] = {}
        self._chrome: dict[str, VstChromeBar] = {}
        self._lock = threading.Lock()
        self._active_path: str | None = None
        self._active_close_evt: threading.Event | None = None
        self._hotkey_stop = threading.Event()
        self._hotkey_thread: threading.Thread | None = None

    def open_paths(self) -> list[str]:
        if self._active_path and self.is_open(self._active_path):
            return [self._active_path]
        return []

    def is_open(self, path: str | None) -> bool:
        if not path:
            return False
        key = str(Path(str(path)))
        return self._active_path == key and bool(
            self._slots.get(key) and self._slots[key].alive
        )

    def plugin(self, path: str | None):
        if not path:
            return None
        key = str(Path(str(path)))
        s = self._slots.get(key)
        return s.plugin if s else None

    def last_error(self, path: str | None) -> str | None:
        if not path:
            return None
        s = self._slots.get(str(Path(str(path))))
        return s.error if s else None

    def open(
        self,
        path: Path | str,
        *,
        on_loaded: Callable[[str, Any], None] | None = None,
        replace_others: bool = True,
        show_chrome: bool = True,
    ) -> None:
        if not plugin_path_ok(path):
            msg = f"plugin path missing or invalid: {path!r}"
            self.pluginFailed.emit(str(path or ""), msg)
            self.statusMessage.emit(msg)
            return

        key = str(Path(str(path)))

        if not _is_gui_thread():
            QTimer.singleShot(
                0,
                lambda: self.open(
                    key,
                    on_loaded=on_loaded,
                    replace_others=replace_others,
                    show_chrome=show_chrome,
                ),
            )
            return

        if self._active_path and self._active_path != key:
            if self._active_close_evt is not None:
                self._active_close_evt.set()
            # cannot join — we are about to block again; just clear flag
            self._active_path = None

        if self.is_open(key):
            bar = self._chrome.get(key)
            if bar is not None:
                bar.raise_()
                bar.activateWindow()
            return

        slot = self._slots.get(key) or _VstSlot(key)
        self._slots[key] = slot
        slot.error = None
        slot.failed = False

        if show_chrome:
            self._ensure_chrome(key)
            bar = self._chrome.get(key)
            if bar:
                bar.set_status("opening native UI…")
                bar.raise_()
            QApplication.processEvents()

        self.statusMessage.emit(f"Opening VST UI: {Path(key).stem}…")
        QApplication.processEvents()

        plugin = slot.plugin
        try:
            if plugin is None:
                try:
                    ctypes.windll.ole32.CoInitializeEx(None, 2)
                except Exception:
                    pass
                import pedalboard as pb

                plugin = pb.load_plugin(key)
                if plugin is None:
                    raise RuntimeError("load_plugin returned None")
                slot.plugin = plugin
        except Exception as exc:
            slot.error = repr(exc)
            slot.failed = True
            slot.alive = False
            self.pluginFailed.emit(key, repr(exc))
            self.statusMessage.emit(f"VST load failed: {Path(key).stem}: {exc}")
            self._destroy_chrome(key)
            return

        try:
            self.pluginLoaded.emit(key, plugin)
        except Exception:
            pass
        if on_loaded is not None:
            try:
                on_loaded(key, plugin)
            except Exception:
                pass

        close_evt = threading.Event()
        slot.close_evt = close_evt
        slot.alive = True
        self._active_path = key
        self._active_close_evt = close_evt
        self._start_hotkey_watcher(close_evt)

        if show_chrome:
            bar = self._chrome.get(key)
            if bar:
                bar.set_status("UI open · close plugin window / Esc / F4")
        self.statusMessage.emit(
            f"VST UI open: {Path(key).stem} — close plugin window (or Esc/F4) to return"
        )
        QApplication.processEvents()

        try:
            # REQUIRED: main thread. Blocks until window closed or close_evt set.
            plugin.show_editor(close_evt)
        except Exception as exc:
            slot.error = repr(exc)
            slot.failed = True
            self.pluginFailed.emit(key, repr(exc))
            self.statusMessage.emit(f"VST UI error: {exc}")
        finally:
            self._stop_hotkey_watcher()
            slot.alive = False
            slot.close_evt = None
            if self._active_path == key:
                self._active_path = None
                self._active_close_evt = None
            try:
                self.pluginClosed.emit(key)
            except Exception:
                pass
            bar = self._chrome.get(key)
            if bar:
                bar.set_status("UI closed · Bypass or Remove")
            self.statusMessage.emit(f"VST UI closed: {Path(key).stem}")

    def close(self, path: str | Path, timeout: float = 1.5) -> None:
        key = str(Path(str(path))) if path else ""
        evt = None
        slot = self._slots.get(key)
        if slot is not None:
            evt = slot.close_evt
        if self._active_path == key:
            evt = evt or self._active_close_evt
        if evt is not None:
            evt.set()
        if self._active_path != key:
            self._destroy_chrome(key)

    def close_all(self, timeout: float = 1.5) -> None:
        if self._active_close_evt is not None:
            self._active_close_evt.set()
        for key in list(self._chrome.keys()):
            self._destroy_chrome(key)
        self._active_path = None

    def remove(self, path: str | Path) -> None:
        key = str(Path(str(path)))
        self.close(key)
        self._slots.pop(key, None)
        self._destroy_chrome(key)
        try:
            self.pluginRemoved.emit(key)
        except Exception:
            pass

    def _start_hotkey_watcher(self, close_evt: threading.Event) -> None:
        self._stop_hotkey_watcher()
        self._hotkey_stop = threading.Event()
        stop = self._hotkey_stop

        def _watch():
            try:
                user32 = ctypes.windll.user32
            except Exception:
                return
            while not stop.is_set() and not close_evt.is_set():
                try:
                    if user32.GetAsyncKeyState(0x1B) & 0x8000:  # Esc
                        close_evt.set()
                        return
                    if user32.GetAsyncKeyState(0x73) & 0x8000:  # F4
                        close_evt.set()
                        return
                except Exception:
                    return
                time.sleep(0.05)

        t = threading.Thread(target=_watch, daemon=True, name="vst-hotkey")
        self._hotkey_thread = t
        t.start()

    def _stop_hotkey_watcher(self) -> None:
        try:
            self._hotkey_stop.set()
        except Exception:
            pass
        self._hotkey_thread = None

    def _ensure_chrome(self, key: str) -> None:
        if key in self._chrome:
            bar = self._chrome[key]
            bar.show()
            bar.raise_()
            return
        n = len(self._chrome)
        bar = VstChromeBar(
            key,
            on_close_ui=lambda p: self.close(p),
            on_remove=lambda p: self.remove(p),
            on_bypass=lambda p, on: self.bypassChanged.emit(p, on),
            parent=None,
        )
        bar.move(80 + n * 28, 80 + n * 52)
        bar.show()
        bar.raise_()
        self._chrome[key] = bar

    def _destroy_chrome(self, key: str) -> None:
        bar = self._chrome.pop(key, None)
        if bar is not None:
            try:
                bar.close()
                bar.deleteLater()
            except Exception:
                pass

    def set_chrome_bypass(self, path: str, on: bool) -> None:
        bar = self._chrome.get(str(path))
        if bar is not None:
            bar.set_bypass(on)
