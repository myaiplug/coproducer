"""
Standalone Artifact Hunter + Bleedfix tools for CoProducer.

Two independent, file-driven widgets:

  ArtifactHunterTool  -> hunt_artifacts() scan (clicks / DC / dropout edges)
                         then remove_artifacts_audio() repair + export
  BleedfixTool        -> apply_bleedfix() expander-gate + export

They share nothing but theme components, so they can be embedded side-by-side
(desktop sidebar pages) or popped as modals from the Studio Player without
being coupled to each other.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import soundfile as sf

from ..audio import studio_fx
from .eq_knobs import PowerLightButton
from .studio_fx_panel import CHIP_ACCENT, CHIP_AMBER, CHIP_CYAN, CHIP_ORANGE, FxKnob, _chip
from .theme import Color, Radius, Space, Type


class _ToolCard(QFrame):
    """Shared chrome: title bar + status line."""

    def __init__(self, title: str, subtitle: str, parent=None):
        super().__init__(parent)
        self.setObjectName("FxToolCard")
        self.setStyleSheet(f"""
            QFrame#FxToolCard {{
                background: {Color.with_alpha(Color.ELEVATED, 1.0)};
                border: 1px solid {Color.with_alpha(Color.ACCENT, 0.3)};
                border-radius: {Radius.XL}px;
            }}
        """)
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.LG, Space.MD, Space.LG, Space.LG)
        root.setSpacing(Space.SM)

        hdr = QHBoxLayout()
        t = QLabel(title)
        t.setStyleSheet(
            f"font-size: 12px; font-weight: 800; letter-spacing: 1.6px; "
            f"font-family: {Type.DISPLAY}; color: {Color.TEXT};"
        )
        hdr.addWidget(t)
        sub = QLabel(subtitle)
        sub.setStyleSheet(f"font-size: {Type.TINY}px; color: {Color.MUTED}; background: transparent;")
        hdr.addWidget(sub)
        hdr.addStretch()
        root.addLayout(hdr)
        self._root = root

    def _status_line(self) -> QLabel:
        self._status = QLabel("")
        self._status.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent;"
        )
        self._root.addWidget(self._status)
        return self._status

    def set_status(self, text: str) -> None:
        self._status.setText(text)


class _FileRow(QWidget):
    """Path label + Browse. Emits nothing — host reads path()."""

    def __init__(self, placeholder: str = "no file loaded", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(Space.SM)
        self._path: Path | None = None
        self._label = QLabel(placeholder)
        self._label.setStyleSheet(
            f"font-size: {Type.CAPTION}px; color: {Color.MUTED}; background: transparent;"
            "padding: 6px 10px; border: 1px dashed rgba(255,255,255,40); border-radius: 8px;"
        )
        self._label.setMinimumWidth(220)
        lay.addWidget(self._label, 1)
        self._browse = QPushButton("Browse…")
        self._browse.setCursor(Qt.PointingHandCursor)
        self._browse.setFixedHeight(28)
        self._browse.clicked.connect(self._pick)
        lay.addWidget(self._browse)

    def _pick(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose audio file",
            "",
            "Audio files (*.wav *.flac *.mp3 *.ogg *.m4a *.aiff *.aif);;All files (*.*)",
        )
        if path:
            self.set_path(path)

    def path(self) -> Path | None:
        return self._path

    def set_path(self, path: str | Path) -> None:
        p = Path(path)
        self._path = p
        self._label.setText(f"{p.name}  ·  {p.parent}")
        self._label.setToolTip(str(p))
        self._label.setStyleSheet(self._label.styleSheet())


class ArtifactHunterTool(_ToolCard):
    """Scan a file for clicks / DC / dropout edges, repair, and export."""

    def __init__(self, parent=None):
        super().__init__("ARTIFACT HUNTER", "click · DC · dropout detection", parent)
        self._hunt: studio_fx.ArtifactHunt | None = None
        self._src_audio: Any = None
        self._sr = 44100

        self._file_row = _FileRow()
        self._root.addWidget(self._file_row)

        row = QHBoxLayout()
        self._btn_scan = QPushButton("Scan")
        self._btn_scan.setCursor(Qt.PointingHandCursor)
        self._btn_scan.setFixedHeight(30)
        self._btn_scan.setStyleSheet(
            f"QPushButton {{ background: {Color.with_alpha(Color.ACCENT, 0.25)}; color: {Color.ACCENT};"
            f" border: 1px solid {Color.ACCENT}; border-radius: 8px; font-weight: 700; }}"
        )
        self._btn_scan.clicked.connect(self.scan_now)
        row.addWidget(self._btn_scan)
        row.addStretch()
        self._root.addLayout(row)

        self._status_line()
        self._status.setText("pick a file, then Scan")

        self._list = QListWidget()
        self._list.setMaximumHeight(180)
        self._list.setStyleSheet(
            f"QListWidget {{ background: {Color.BG}; border: 1px solid {Color.LINE};"
            f" border-radius: 8px; color: {Color.TEXT}; font-size: {Type.CAPTION}px; }}"
        )
        self._root.addWidget(self._list)

        toggles = QHBoxLayout()
        toggles.setSpacing(6)
        self.cb_declick = _chip("DE-CLICK", checkable=True, checked=True, color=CHIP_AMBER)
        self.cb_dedc = _chip("DE-DC", checkable=True, checked=True, color=CHIP_CYAN)
        self.cb_deedge = _chip("DROPOUT EDGES", checkable=True, checked=True, color=CHIP_ORANGE)
        for cb in (self.cb_declick, self.cb_dedc, self.cb_deedge):
            toggles.addWidget(cb)
        toggles.addStretch()
        self._root.addLayout(toggles)

        act = QHBoxLayout()
        act.addStretch()
        self._btn_repair = QPushButton("Repair & Save…")
        self._btn_repair.setCursor(Qt.PointingHandCursor)
        self._btn_repair.setFixedHeight(32)
        self._btn_repair.setObjectName("Primary")
        self._btn_repair.setEnabled(False)
        self._btn_repair.clicked.connect(self._repair_save)
        act.addWidget(self._btn_repair)
        self._root.addLayout(act)

    # ------------------------------------------------------------- public

    def set_source(self, path: str | Path) -> None:
        self._file_row.set_path(path)
        self.scan_now()

    def path(self) -> Path | None:
        return self._file_row.path()

    def scan_now(self):
        path = self._file_row.path()
        if not path or not path.is_file():
            QMessageBox.information(self, "Artifact Hunter", "Pick an audio file first.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            data, sr = sf.read(str(path), always_2d=True, dtype="float32")
            self._src_audio = data.T
            self._sr = int(sr)
            hunt = studio_fx.hunt_artifacts_audio(self._src_audio, self._sr)
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            self._status.setText(f"scan failed: {exc}")
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._hunt = hunt
        self._list.clear()
        if hunt.error:
            self._status.setText(f"scan error: {hunt.error}")
            self._btn_repair.setEnabled(False)
            return
        # Prefer v2 intelligence hits when present
        if hunt.hits:
            for h in hunt.hits[:80]:
                if not isinstance(h, dict):
                    continue
                band = h.get("band")
                band_s = f" B{band}" if band is not None else ""
                self._list.addItem(
                    QListWidgetItem(
                        f"{h.get('kind','click'):10s}  {float(h['start_s']):.3f}s → {float(h['end_s']):.3f}s"
                        f"  conf {float(h.get('confidence',0)):.2f}  {h.get('method','?')}{band_s}"
                    )
                )
        else:
            for s, e, db in hunt.clicks:
                self._list.addItem(QListWidgetItem(f"click   {s:.3f}s → {e:.3f}s   peak {db:.1f} dB"))
        for s, e in hunt.dropout_edges:
            self._list.addItem(QListWidgetItem(f"dropout edge   {s:.3f}s → {e:.3f}s"))
        if hunt.dc_offset:
            self._list.addItem(QListWidgetItem(f"DC offset   {hunt.dc_offset:+.4f}"))
        if hunt.clipped_estimate:
            self._list.addItem(QListWidgetItem(f"clipped samples   ~{hunt.clipped_estimate}"))
        if not hunt.clicks and not hunt.hits and not hunt.dropout_edges:
            self._list.addItem(QListWidgetItem("clean — no clicks or dropout edges found"))
        pref = (hunt.metrics or {}).get("preferred_algorithm", "auto")
        n_hits = len(hunt.hits) if hunt.hits else len(hunt.clicks)
        self._status.setText(
            f"{hunt.duration_s:.1f}s · {n_hits} hits · "
            f"{len(hunt.dropout_edges)} edges · DC {hunt.dc_offset:+.4f} · algo={pref}"
        )
        self._btn_repair.setEnabled(True)

    def _repair_save(self):
        if self._hunt is None or self._src_audio is None:
            return
        src = self._file_row.path()
        default = src.with_name(f"{src.stem}_repaired.wav")
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save repaired audio", str(default), "WAV (*.wav);;FLAC (*.flac)"
        )
        if not dest:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            m = (self._hunt.metrics or {}) if self._hunt else {}
            repaired, applied = studio_fx.remove_artifacts_audio(
                self._src_audio,
                self._sr,
                self._hunt,
                declick=self.cb_declick.isChecked(),
                dedc=self.cb_dedc.isChecked(),
                deedge=self.cb_deedge.isChecked(),
                algorithm=str(m.get("preferred_algorithm") or "auto"),
                freq_skew=float(m.get("freq_skew", 0.5)),
                min_confidence=float(m.get("min_confidence", 0.45)),
            )
            # 24-bit when possible for repair quality
            subtype = "PCM_24" if str(dest).lower().endswith(".wav") else "PCM_16"
            try:
                sf.write(dest, repaired.T, self._sr, subtype=subtype)
            except Exception:
                sf.write(dest, repaired.T, self._sr, subtype="PCM_16")
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Repair failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._status.setText("saved · " + ", ".join(applied))


class BleedfixTool(_ToolCard):
    """Expander-gate (mic bleed / room wash ducking) with export."""

    def __init__(self, parent=None):
        super().__init__("BLEEDFIX", "expander gate for mic bleed / room wash", parent)
        self._src_audio: Any = None
        self._sr = 44100

        self._file_row = _FileRow()
        self._root.addWidget(self._file_row)

        knobs = QHBoxLayout()
        knobs.setSpacing(4)
        knobs.addStretch()
        self.k_threshold = FxKnob("THRESH", minimum=-70.0, maximum=-10.0, default=-46.0,
                                  log=True, fmt="{:.0f}", unit="dB", color_role="high")
        self.k_ratio = FxKnob("RATIO", minimum=1.0, maximum=20.0, default=8.0,
                              fmt="{:.0f}", unit=":1", color_role="mid")
        self.k_attack = FxKnob("ATTACK", minimum=0.5, maximum=20.0, default=3.0,
                               fmt="{:.1f}", unit="ms", color_role="low")
        self.k_release = FxKnob("RELEASE", minimum=20.0, maximum=500.0, default=160.0,
                                fmt="{:.0f}", unit="ms", color_role="low")
        self.k_wet = FxKnob("WET", minimum=0.0, maximum=100.0, default=100.0,
                            fmt="{:.0f}", unit="%", color_role="high")
        for k in (self.k_threshold, self.k_ratio, self.k_attack, self.k_release, self.k_wet):
            knobs.addWidget(k)
        knobs.addStretch()
        self._root.addLayout(knobs)

        self._status_line()
        self._status.setText("pick a file, then Process & Save")

        act = QHBoxLayout()
        act.addStretch()
        self._btn_process = QPushButton("Process & Save…")
        self._btn_process.setCursor(Qt.PointingHandCursor)
        self._btn_process.setFixedHeight(32)
        self._btn_process.setObjectName("Primary")
        self._btn_process.setEnabled(False)
        self._btn_process.clicked.connect(self._process_save)
        act.addWidget(self._btn_process)
        self._root.addLayout(act)

    # ------------------------------------------------------------- public

    def set_source(self, path: str | Path) -> None:
        self._file_row.set_path(path)
        self._btn_process.setEnabled(True)

    def path(self) -> Path | None:
        return self._file_row.path()

    def _process_save(self):
        path = self._file_row.path()
        if not path or not path.is_file():
            QMessageBox.information(self, "Bleedfix", "Pick an audio file first.")
            return
        default = path.with_name(f"{path.stem}_bleedfix.wav")
        dest, _ = QFileDialog.getSaveFileName(
            self, "Save bleed-fixed audio", str(default), "WAV (*.wav);;FLAC (*.flac)"
        )
        if not dest:
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            data, sr = sf.read(str(path), always_2d=True, dtype="float32")
            fixed, note = studio_fx.apply_bleedfix(
                data.T,
                int(sr),
                threshold_db=self.k_threshold.value(),
                ratio=self.k_ratio.value(),
                attack_ms=self.k_attack.value(),
                release_ms=self.k_release.value(),
                wet=self.k_wet.value() / 100.0,
            )
            sf.write(dest, fixed.T, int(sr), subtype="PCM_16")
        except Exception as exc:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "Bleedfix failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        self._status.setText(f"saved · {note}")


def artifact_hunter_modal(source: str | Path | None = None, parent=None):
    """Open the Artifact Hunter as a modal dialog."""
    from PySide6.QtWidgets import QDialog

    dlg = QDialog(parent)
    dlg.setWindowTitle("Artifact Hunter")
    dlg.setModal(True)
    dlg.resize(520, 560)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(0, 0, 0, 0)
    tool = ArtifactHunterTool(dlg)
    lay.addWidget(tool)
    if source:
        tool.set_source(source)
    dlg.exec()
    return dlg


def bleedfix_modal(source: str | Path | None = None, parent=None):
    """Open the Bleedfix tool as a modal dialog."""
    from PySide6.QtWidgets import QDialog

    dlg = QDialog(parent)
    dlg.setWindowTitle("Bleedfix")
    dlg.setModal(True)
    dlg.resize(520, 460)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(0, 0, 0, 0)
    tool = BleedfixTool(dlg)
    lay.addWidget(tool)
    if source:
        tool.set_source(source)
    dlg.exec()
    return dlg
