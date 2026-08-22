"""Convert multi-select + result dialogs."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .theme import Color, Radius, Space, Type


class ConvertResultDialog(QDialog):
    """Shows full paths (wrapped), Open folder / Reveal file actions."""

    def __init__(
        self,
        parent: QWidget | None,
        results: list[dict[str, Any]],
        *,
        title: str = "Convert complete",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(520)
        self.setMinimumHeight(280)
        self._results = results or []

        lay = QVBoxLayout(self)
        lay.setSpacing(Space.MD)

        ok_n = sum(1 for r in self._results if r.get("ok"))
        fail_n = len(self._results) - ok_n
        summary = QLabel(
            f"{ok_n} succeeded" + (f", {fail_n} failed" if fail_n else "") + "."
        )
        summary.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {Color.TEXT};")
        lay.addWidget(summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(Space.SM)

        for r in self._results:
            card = QWidget()
            cl = QVBoxLayout(card)
            cl.setContentsMargins(8, 8, 8, 8)
            status = "OK" if r.get("ok") else "FAILED"
            color = Color.SUCCESS if r.get("ok") and hasattr(Color, "SUCCESS") else (
                "#22c55e" if r.get("ok") else "#ef4444"
            )
            head = QLabel(f"[{status}]  .{r.get('fmt', '?')}")
            head.setStyleSheet(f"font-weight: 700; color: {color};")
            cl.addWidget(head)

            path = str(r.get("dest") or "")
            path_lbl = QLabel(path)
            path_lbl.setWordWrap(True)
            path_lbl.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
                | Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            path_lbl.setStyleSheet(
                f"font-family: {Type.MONO}; font-size: 11px; color: {Color.TEXT};"
            )
            cl.addWidget(path_lbl)

            if r.get("error") and not r.get("ok"):
                err = QLabel((r.get("error") or "")[:600])
                err.setWordWrap(True)
                err.setStyleSheet("color: #fca5a5; font-size: 11px;")
                cl.addWidget(err)

            row = QHBoxLayout()
            if r.get("ok") and path and Path(path).is_file():
                b1 = QPushButton("Open folder")
                b1.clicked.connect(lambda _=False, p=path: self._open_folder(p))
                b2 = QPushButton("Reveal file")
                b2.clicked.connect(lambda _=False, p=path: self._reveal(p))
                row.addWidget(b1)
                row.addWidget(b2)
            row.addStretch()
            cl.addLayout(row)
            bl.addWidget(card)

        bl.addStretch()
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        # Global open folder (first success)
        actions = QHBoxLayout()
        first_ok = next((r for r in self._results if r.get("ok")), None)
        if first_ok:
            open_all = QPushButton("Open export folder")
            open_all.setDefault(True)
            open_all.clicked.connect(
                lambda: self._open_folder(str(first_ok.get("dest")))
            )
            actions.addWidget(open_all)
        actions.addStretch()
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        actions.addWidget(close_btn)
        lay.addLayout(actions)

    @staticmethod
    def _open_folder(path: str) -> None:
        p = Path(path)
        folder = p if p.is_dir() else p.parent
        if folder.is_dir():
            os.startfile(str(folder))

    @staticmethod
    def _reveal(path: str) -> None:
        p = Path(path)
        if p.is_file():
            import subprocess

            subprocess.Popen(["explorer", "/select,", str(p)])
        elif p.parent.is_dir():
            os.startfile(str(p.parent))


def show_convert_results(parent: QWidget | None, results: list[dict[str, Any]]) -> None:
    if not results:
        return
    ok = all(r.get("ok") for r in results)
    title = "Convert complete" if ok else (
        "Convert finished with errors" if any(r.get("ok") for r in results) else "Convert failed"
    )
    dlg = ConvertResultDialog(parent, results, title=title)
    dlg.exec()
