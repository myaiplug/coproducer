"""
Master export dialog — normalize the current file to a streaming preset
(Spotify / Apple / Podcast / club loudness) with true-peak limiting,
then save as WAV / FLAC / MP3.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .convert_dialog import show_convert_results
from .theme import Color, Space, Type

try:
    from ..audio.master_export import EXPORT_FORMATS, STREAM_PRESETS, master_export
except Exception:  # pragma: no cover
    STREAM_PRESETS = {"Spotify  (−14 LUFS)": {"lufs": -14.0, "tpt": -1.0}}
    EXPORT_FORMATS = [("wav", "WAV 24-bit")]
    master_export = None  # type: ignore


class MasterExportDialog(QDialog):
    """One-click streaming-ready master export."""

    def __init__(self, source: str | Path, parent=None):
        super().__init__(parent)
        self._source = Path(source)
        self.setWindowTitle("Master Export — streaming-ready")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(f"background: {Color.BG}; color: {Color.TEXT};")
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        title = QLabel("Streaming-ready master")
        title.setStyleSheet(
            f"font-size: 15px; font-weight: 800; letter-spacing: 1px; color: {Color.ACCENT_SOFT};"
        )
        root.addWidget(title)

        src_lbl = QLabel(f"Source:  {self._source.name}")
        src_lbl.setStyleSheet(f"font-size: 10px; color: {Color.MUTED};")
        src_lbl.setToolTip(str(self._source))
        root.addWidget(src_lbl)

        row1 = QHBoxLayout()
        row1.setSpacing(Space.SM)
        p1 = QLabel("Preset")
        p1.setStyleSheet(f"font-size: 9px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};")
        row1.addWidget(p1)
        self._preset = QComboBox()
        for name, spec in STREAM_PRESETS.items():
            self._preset.addItem(name, spec)
        self._preset.setCurrentIndex(0)
        row1.addWidget(self._preset, 1)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(Space.SM)
        p2 = QLabel("Format")
        p2.setStyleSheet(f"font-size: 9px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};")
        row2.addWidget(p2)
        self._format = QComboBox()
        for key, label in EXPORT_FORMATS:
            self._format.addItem(label, key)
        row2.addWidget(self._format, 1)
        root.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(Space.SM)
        p3 = QLabel("Output")
        p3.setStyleSheet(f"font-size: 9px; font-weight: 700; letter-spacing: 1px; color: {Color.MUTED};")
        row3.addWidget(p3)
        self._dest = QLineEdit(str(self._source.with_name(f"{self._source.stem}_master.wav")))
        self._dest.setStyleSheet(
            f"font-size: 10px; color: {Color.TEXT}; background: {Color.ELEVATED}; border: 1px solid {Color.LINE}; border-radius: 6px; padding: 5px 8px;"
        )
        row3.addWidget(self._dest, 1)
        browse = QPushButton("Browse…")
        browse.setCursor(Qt.PointingHandCursor)
        browse.setFixedHeight(28)
        browse.clicked.connect(self._browse)
        row3.addWidget(browse)
        root.addLayout(row3)

        self._status = QLabel("")
        self._status.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {Color.GOLD if hasattr(Color, 'GOLD') else Color.ACCENT};")
        self._status.setWordWrap(True)
        root.addWidget(self._status)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setFixedHeight(32)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        self._run = QPushButton("Run master export")
        self._run.setCursor(Qt.PointingHandCursor)
        self._run.setFixedHeight(32)
        self._run.setStyleSheet(
            f"background: {Color.ACCENT}; color: #0b0b0e; font-weight: 800; border-radius: 8px; padding: 0 18px;"
        )
        self._run.clicked.connect(self._run_export)
        btns.addWidget(self._run)
        root.addLayout(btns)

    def _browse(self):
        dest, _ = QFileDialog.getSaveFileName(
            self,
            "Save mastered file",
            self._dest.text(),
            "WAV (*.wav);;FLAC (*.flac);;MP3 (*.mp3)",
        )
        if dest:
            self._dest.setText(dest)

    def _status_fn(self, msg: str):
        self._status.setText(msg)
        self._status.repaint()
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()

    def _run_export(self):
        if master_export is None:
            QMessageBox.critical(self, "Master Export", "Engine unavailable (pyloudnorm missing).")
            return
        dest = Path(self._dest.text().strip())
        if not dest.suffix:
            dest = dest.with_suffix(".wav")
        if dest.parent and not dest.parent.exists():
            QMessageBox.warning(self, "Master Export", f"Folder does not exist: {dest.parent}")
            return
        preset = self._preset.currentText()
        fmt = self._format.currentData()
        self._run.setEnabled(False)
        self._status.setText("measuring loudness…")
        from PySide6.QtWidgets import QApplication

        QApplication.processEvents()
        try:
            res = master_export(
                str(self._source),
                str(dest),
                preset=preset,
                format_key=fmt,
                status=self._status_fn,
            )
        finally:
            self._run.setEnabled(True)
        if not res.get("ok"):
            self._status.setText(f"failed: {res.get('error')}")
            return
        self._status.setText(
            f"done · {res['measured_lufs']:.0f} → {res['target_lufs']:.0f} LUFS "
            f"({res['gain_db']:+.1f} dB) · TP {res['true_peak_dbtp']:+.1f} dBTP · {res['format'].upper()}"
        )
        show_convert_results(self, [{"ok": True, "dest": res["dest"], "fmt": res["format"]}])
