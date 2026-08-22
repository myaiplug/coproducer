"""
Batch analyze dialog — drop / pick a folder, run full analysis on every
audio file in a worker thread, show a results table, and write the standard
CSV + HTML/JSON/TXT batch report through the existing WorkflowRunner.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .theme import Color, Space

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class _BatchWorker(QObject):
    progress = Signal(str)       # "file name → score"
    done = Signal(object, object)  # (report, error)
    running = Signal(bool)

    def __init__(self, folder: Path, recursive: bool):
        super().__init__()
        self.folder = Path(folder)
        self.recursive = recursive

    def run(self):
        error = None
        report = None
        try:
            from ..core.engine import WorkflowRunner
            from ..utils.files import audio_files
            from ..features.collections import track_row

            logger = logging.getLogger("coproducer.batch")
            runner = WorkflowRunner(PROJECT_ROOT, logger, generate_previews=False)
            files = audio_files(
                self.folder, runner.extensions, recursive=self.recursive
            )
            if not files:
                error = "No supported audio files found in that folder."
                self.done.emit(None, error)
                return
            for i, path in enumerate(files, 1):
                try:
                    ta = runner.analyze(path)
                    row = track_row(path, ta, runner.settings)
                    self.progress.emit(
                        f"[{i}/{len(files)}] {path.name} → {row.get('score', '?')}/100"
                    )
                except Exception as exc:
                    self.progress.emit(f"[{i}/{len(files)}] {path.name} → ERROR {exc}")
            report = runner.batch(self.folder)
        except Exception as exc:
            error = str(exc)
        self.done.emit(report, error)


class BatchAnalyzeDialog(QDialog):
    """Analyze every audio file in a folder and show a scored table."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Batch Analyze Folder")
        self.setModal(True)
        self.setMinimumSize(760, 520)
        self.setStyleSheet(f"background: {Color.BG}; color: {Color.TEXT};")
        self._threads: list[QThread] = []
        self._build()

    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(Space.XL, Space.LG, Space.XL, Space.LG)
        root.setSpacing(Space.MD)

        title = QLabel("Batch analyze folder")
        title.setStyleSheet(
            f"font-size: 15px; font-weight: 800; letter-spacing: 1px; color: {Color.ACCENT_SOFT};"
        )
        root.addWidget(title)
        hint = QLabel("Runs the full analysis (loudness, spectral, faults, stereo…) on every file and writes a batch report + CSV.")
        hint.setStyleSheet(f"font-size: 10px; color: {Color.MUTED};")
        hint.setWordWrap(True)
        root.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(Space.SM)
        self._folder = QLineEdit()
        self._folder.setPlaceholderText("Choose a folder of mixes…")
        self._folder.setStyleSheet(
            f"font-size: 11px; color: {Color.TEXT}; background: {Color.ELEVATED}; border: 1px solid {Color.LINE}; border-radius: 6px; padding: 6px 8px;"
        )
        row.addWidget(self._folder, 1)
        browse = QPushButton("Browse…")
        browse.setCursor(Qt.PointingHandCursor)
        browse.setFixedHeight(30)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        self._recursive = QCheckBox("Include subfolders")
        self._recursive.setChecked(True)
        self._recursive.setCursor(Qt.PointingHandCursor)
        self._recursive.setStyleSheet(f"font-size: 10px; color: {Color.TEXT}; background: transparent;")
        row.addWidget(self._recursive)
        self._start = QPushButton("Start analysis")
        self._start.setCursor(Qt.PointingHandCursor)
        self._start.setFixedHeight(30)
        self._start.setStyleSheet(
            f"background: {Color.ACCENT}; color: #0b0b0e; font-weight: 800; border-radius: 8px; padding: 0 16px;"
        )
        self._start.clicked.connect(self._start_batch)
        row.addWidget(self._start)
        root.addLayout(row)

        self._status = QLabel("Ready.")
        self._status.setStyleSheet(f"font-size: 11px; font-weight: 600; color: {Color.GOLD if hasattr(Color, 'GOLD') else Color.ACCENT};")
        root.addWidget(self._status)

        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(["File", "Score", "Rating", "LUFS", "TP dBTP", "Path"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setStyleSheet(
            f"background: {Color.ELEVATED}; alternate-background-color: {Color.SURFACE};"
            f"border: 1px solid {Color.LINE}; border-radius: 8px; font-size: 10px;"
        )
        root.addWidget(self._table, 1)

        btns = QHBoxLayout()
        btns.addStretch()
        self._btn_reports = QPushButton("Open reports folder")
        self._btn_reports.setCursor(Qt.PointingHandCursor)
        self._btn_reports.setFixedHeight(30)
        self._btn_reports.clicked.connect(self._open_reports)
        self._btn_reports.setEnabled(False)
        btns.addWidget(self._btn_reports)
        close = QPushButton("Close")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedHeight(30)
        close.clicked.connect(self.accept)
        btns.addWidget(close)
        root.addLayout(btns)

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose folder of audio files")
        if folder:
            self._folder.setText(folder)

    def _start_batch(self):
        folder = Path(self._folder.text().strip())
        if not folder.is_dir():
            QMessageBox.warning(self, "Batch Analyze", "Choose an existing folder first.")
            return
        self._table.setRowCount(0)
        self._start.setEnabled(False)
        self._btn_reports.setEnabled(False)
        self._status.setText("Analyzing…")
        thread = QThread(self)
        worker = _BatchWorker(folder, self._recursive.isChecked())
        worker.moveToThread(thread)
        worker.progress.connect(self._status.setText)
        worker.done.connect(self._on_done)
        thread.started.connect(worker.run)
        self._threads.append(thread)
        thread.start()

    def _on_done(self, report, error):
        self._start.setEnabled(True)
        if error:
            self._status.setText(f"Failed: {error}")
            return
        rows = (report or {}).get("tracks") or []
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            self._table.setItem(i, 0, QTableWidgetItem(str(row.get("file") or "?")))
            self._table.setItem(i, 1, QTableWidgetItem(str(row.get("score", "?"))))
            self._table.setItem(i, 2, QTableWidgetItem(str(row.get("rating", ""))))
            lufs = row.get("integrated_lufs")
            self._table.setItem(i, 3, QTableWidgetItem(f"{lufs:.1f}" if isinstance(lufs, (int, float)) else ""))
            tp = row.get("true_peak_dbtp")
            self._table.setItem(i, 4, QTableWidgetItem(f"{tp:+.1f}" if isinstance(tp, (int, float)) else ""))
            self._table.setItem(i, 5, QTableWidgetItem(str(row.get("path", ""))))
        summary = (report or {}).get("summary") or "Batch complete."
        self._status.setText(f"{summary}")
        self._btn_reports.setEnabled(True)

    def _open_reports(self):
        reports = PROJECT_ROOT / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        try:
            from .convert_dialog import _open_folder

            _open_folder(str(reports))
        except Exception:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(reports)))

    def close_threads(self):
        for t in self._threads:
            try:
                t.quit()
                t.wait(1500)
            except Exception:
                pass
        self._threads = []
