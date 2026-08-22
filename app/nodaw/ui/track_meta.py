# -*- coding: utf-8 -*-
"""Cover art + editable track metadata for Home / Studio."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QFont, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QGridLayout,
    QMessageBox,
)

from .theme import Color, Type, Space, Radius, Layout


class CoverArtButton(QFrame):
    """
    Square cover art tile.
    Click to add / replace embedded image.
    """

    coverChanged = Signal()
    addRequested = Signal()

    def __init__(self, size: int = 140, parent=None):
        super().__init__(parent)
        self._size = size
        self._has_cover = False
        self._path: Optional[Path] = None
        self.setFixedSize(size, size)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to add or replace cover art")
        self._pix: Optional[QPixmap] = None
        self._restyle()

    def _restyle(self):
        shape = getattr(Layout, "CARD_SHAPE", "glass")
        r = 4 if shape in ("hud", "hard", "cyber") else max(6, Radius.LG)
        border = Color.ACCENT if self._has_cover else Color.LINE
        self.setStyleSheet(f"""
            QFrame {{
                background: {Color.BG};
                border: 1px solid {border};
                border-radius: {r}px;
            }}
        """)

    def set_audio_path(self, path: Path | str | None):
        self._path = Path(path) if path else None
        self.refresh()

    def set_preview_bytes(self, data: bytes | None):
        self._pix = None
        self._has_cover = False
        if data:
            img = QImage.fromData(data)
            if not img.isNull():
                side = self._size - 4
                pm = QPixmap.fromImage(img).scaled(
                    side,
                    side,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                if pm.width() > side or pm.height() > side:
                    x = max(0, (pm.width() - side) // 2)
                    y = max(0, (pm.height() - side) // 2)
                    pm = pm.copy(x, y, side, side)
                self._pix = pm
                self._has_cover = True
        self._restyle()
        self.setToolTip(
            "Click to replace cover art" if self._has_cover else "No cover — click to add"
        )
        self.update()

    def refresh(self):
        self._pix = None
        self._has_cover = False
        if self._path and self._path.is_file():
            try:
                from nodaw.audio.tags_media import extract_cover_bytes

                data = extract_cover_bytes(self._path)
                if data:
                    self.set_preview_bytes(data)
                    return
            except Exception:
                pass
        self._restyle()
        self.setToolTip(
            "Click to replace cover art" if self._has_cover else "No cover — click to add"
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        r = self.rect().adjusted(2, 2, -2, -2)
        if self._has_cover and self._pix and not self._pix.isNull():
            p.drawPixmap(r, self._pix)
        else:
            p.fillRect(r, QColor(Color.with_alpha(Color.MUTED, 0.06)))
            p.setPen(QPen(QColor(Color.with_alpha(Color.MUTED, 0.4)), 1.2, Qt.PenStyle.DashLine))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QColor(Color.with_alpha(Color.MUTED, 0.5)))
            font = QFont()
            font.setPointSize(26)
            font.setWeight(QFont.Weight.Light)
            p.setFont(font)
            p.drawText(r.adjusted(0, -10, 0, 0), Qt.AlignCenter, "+")
            font2 = QFont()
            font2.setPointSize(8)
            font2.setWeight(QFont.Weight.DemiBold)
            font2.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
            p.setFont(font2)
            p.setPen(QColor(Color.with_alpha(Color.MUTED, 0.65)))
            p.drawText(r.adjusted(0, 36, 0, 0), Qt.AlignHCenter | Qt.AlignTop, "COVER")
        p.end()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.addRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class TrackMetadataPanel(QFrame):
    """
    Release metadata strip: cover left, form right, actions bottom.

    Designed as its own band under the score row — not squeezed beside the ring.
    """

    saved = Signal(str)

    COVER_SIZE = 140

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path: Optional[Path] = None
        self._pending_cover: bytes | None = None
        self._pending_cover_mime: str = "image/jpeg"

        self.setObjectName("TrackMetaPanel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._apply_shell_style()

        root = QVBoxLayout(self)
        root.setContentsMargins(Space.MD, Space.MD, Space.MD, Space.MD)
        root.setSpacing(Space.SM)

        # Eyebrow
        head = QHBoxLayout()
        head.setSpacing(Space.SM)
        eyebrow = QLabel("RELEASE · TAGS & COVER")
        eyebrow.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 1.4px; "
            f"color: {Color.MUTED}; background: transparent; border: none;"
        )
        head.addWidget(eyebrow)
        head.addStretch(1)
        self.file_lbl = QLabel("")
        self.file_lbl.setStyleSheet(
            f"font-size: 10px; color: {Color.MUTED}; background: transparent; border: none;"
        )
        self.file_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.file_lbl)
        root.addLayout(head)

        # Body: cover | fields
        body = QHBoxLayout()
        body.setSpacing(Space.LG)
        body.setContentsMargins(0, 0, 0, 0)

        left = QVBoxLayout()
        left.setSpacing(6)
        left.setAlignment(Qt.AlignTop)
        self.cover = CoverArtButton(self.COVER_SIZE)
        self.cover.addRequested.connect(self._pick_cover)
        left.addWidget(self.cover, 0, Qt.AlignLeft)
        self.cover_hint = QLabel("Click image to change")
        self.cover_hint.setStyleSheet(
            f"font-size: 9px; color: {Color.MUTED}; background: transparent; border: none;"
        )
        left.addWidget(self.cover_hint)
        body.addLayout(left, 0)

        # Form — clean 2-col primary + full-width comment
        form_wrap = QVBoxLayout()
        form_wrap.setSpacing(Space.SM)
        form_wrap.setContentsMargins(0, 0, 0, 0)

        grid = QGridLayout()
        grid.setHorizontalSpacing(Space.MD)
        grid.setVerticalSpacing(Space.SM)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._fields: dict[str, QLineEdit] = {}

        # Row of primary pairs
        pairs = [
            ("title", "Title"),
            ("artist", "Artist"),
            ("album", "Album"),
            ("genre", "Genre"),
            ("date", "Year"),
            ("bpm", "BPM"),
        ]
        for i, (key, label) in enumerate(pairs):
            cell = self._field_cell(key, label)
            grid.addLayout(cell, i // 2, i % 2)

        form_wrap.addLayout(grid)

        # Comment full width
        comment_cell = self._field_cell("comment", "Comment")
        form_wrap.addLayout(comment_cell)

        # Actions
        btn_row = QHBoxLayout()
        btn_row.setSpacing(Space.SM)
        btn_row.setContentsMargins(0, 4, 0, 0)

        self.save_btn = QPushButton("Save to file")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setToolTip("Write tags + cover into this audio file (overwrite).")
        self.save_btn.setStyleSheet(self._btn_style(primary=True))
        self.save_btn.clicked.connect(self._save)
        self.save_btn.setEnabled(False)
        btn_row.addWidget(self.save_btn)

        self.save_as_btn = QPushButton("Save as…")
        self.save_as_btn.setCursor(Qt.PointingHandCursor)
        self.save_as_btn.setToolTip("Export a copy with tags + cover applied.")
        self.save_as_btn.setStyleSheet(self._btn_style(primary=False))
        self.save_as_btn.clicked.connect(self._save_as)
        self.save_as_btn.setEnabled(False)
        btn_row.addWidget(self.save_as_btn)

        self.status = QLabel("")
        self.status.setStyleSheet(
            f"font-size: 11px; color: {Color.MUTED}; background: transparent; border: none;"
        )
        self.status.setWordWrap(False)
        btn_row.addWidget(self.status, 1)
        form_wrap.addLayout(btn_row)

        body.addLayout(form_wrap, 1)
        root.addLayout(body)

    def _apply_shell_style(self):
        r = max(4, int(Radius.CARD))
        self.setStyleSheet(f"""
            QFrame#TrackMetaPanel {{
                background: {Color.with_alpha(Color.BG, 0.55)};
                border: 1px solid {Color.LINE};
                border-radius: {r}px;
            }}
        """)

    def _btn_style(self, *, primary: bool) -> str:
        if primary:
            return f"""
                QPushButton {{
                    background: {Color.ACCENT};
                    color: {Color.BG};
                    border: none;
                    border-radius: {max(2, Radius.BUTTON)}px;
                    padding: 8px 16px;
                    font-size: 11px;
                    font-weight: 600;
                }}
                QPushButton:hover {{ background: {Color.ACCENT_SOFT}; }}
                QPushButton:disabled {{
                    background: {Color.LINE};
                    color: {Color.MUTED};
                }}
            """
        return f"""
            QPushButton {{
                background: transparent;
                color: {Color.TEXT};
                border: 1px solid {Color.LINE_HOVER};
                border-radius: {max(2, Radius.BUTTON)}px;
                padding: 8px 14px;
                font-size: 11px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {Color.ACCENT};
                color: {Color.ACCENT};
            }}
            QPushButton:disabled {{
                color: {Color.MUTED};
                border-color: {Color.LINE};
            }}
        """

    def _field_cell(self, key: str, label: str) -> QVBoxLayout:
        cell = QVBoxLayout()
        cell.setSpacing(3)
        cell.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(label.upper())
        lab.setStyleSheet(
            f"font-size: 9px; font-weight: 600; letter-spacing: 0.8px; "
            f"color: {Color.MUTED}; background: transparent; border: none;"
        )
        edit = QLineEdit()
        edit.setPlaceholderText(label)
        edit.setMinimumHeight(32)
        edit.setStyleSheet(f"""
            QLineEdit {{
                background: {Color.ELEVATED};
                color: {Color.TEXT};
                border: 1px solid {Color.LINE};
                border-radius: {max(2, Radius.SM + 2)}px;
                padding: 6px 10px;
                font-size: 12px;
                font-family: {Type.FAMILY};
                selection-background-color: {Color.ACCENT};
                selection-color: {Color.BG};
            }}
            QLineEdit:focus {{
                border-color: {Color.ACCENT};
            }}
            QLineEdit:disabled {{
                color: {Color.MUTED};
            }}
        """)
        self._fields[key] = edit
        cell.addWidget(lab)
        cell.addWidget(edit)
        return cell

    def clear(self):
        self._path = None
        self._pending_cover = None
        self._pending_cover_mime = "image/jpeg"
        self.cover.set_audio_path(None)
        for edit in self._fields.values():
            edit.clear()
        self.save_btn.setEnabled(False)
        self.save_as_btn.setEnabled(False)
        self.status.setText("")
        self.file_lbl.setText("")

    def load_path(self, path: Path | str | None, tags: dict | None = None):
        if not path or not Path(path).is_file():
            self.clear()
            return
        self._path = Path(path)
        self._pending_cover = None
        self._pending_cover_mime = "image/jpeg"
        self.cover.set_audio_path(self._path)
        data = dict(tags or {})
        if not data:
            try:
                from nodaw.audio.tags_media import read_tags

                data = read_tags(self._path)
            except Exception:
                data = {}
        for key, edit in self._fields.items():
            edit.setText(str(data.get(key) or ""))
        self.save_btn.setEnabled(True)
        self.save_as_btn.setEnabled(True)
        self.file_lbl.setText(self._path.name)
        self.status.setText("Edit tags, then Save to file")

    def current_tags(self) -> dict[str, str]:
        return {k: e.text().strip() for k, e in self._fields.items()}

    def _pick_cover(self):
        if not self._path or not self._path.is_file():
            QMessageBox.information(
                self,
                "Cover art",
                "Analyze or open a mix first so cover art can be written to the file.",
            )
            return
        picked, _ = QFileDialog.getOpenFileName(
            self,
            "Choose cover image",
            "",
            "Images (*.jpg *.jpeg *.png *.webp *.gif);;All files (*.*)",
        )
        if not picked:
            return
        try:
            from nodaw.audio.tags_media import mime_from_path

            img = Path(picked)
            data = img.read_bytes()
            if len(data) < 32:
                QMessageBox.warning(self, "Cover art", "Image file is empty.")
                return
            self._pending_cover = data
            self._pending_cover_mime = mime_from_path(img)
            self.cover.set_preview_bytes(data)
            self.status.setText(f"Cover ready · {img.name} — Save to file")
            self.coverChanged_emit()
        except Exception as exc:
            QMessageBox.critical(self, "Cover art", str(exc))

    def coverChanged_emit(self):
        self.cover.coverChanged.emit()

    def _apply_to_path(self, dest: Path | None = None) -> tuple[bool, str, Path | None]:
        if not self._path or not self._path.is_file():
            return False, "No audio file loaded", None
        tags = self.current_tags()
        try:
            from nodaw.audio.tags_media import save_audio_with_metadata

            ok, msg = save_audio_with_metadata(
                self._path,
                tags,
                cover_bytes=self._pending_cover,
                cover_mime=self._pending_cover_mime if self._pending_cover else None,
                dest=dest,
                overwrite=True,
            )
            out = Path(dest) if dest else self._path
            return ok, msg, out if ok else None
        except Exception as exc:
            return False, str(exc), None

    def _save(self):
        ok, msg, out = self._apply_to_path(None)
        if ok and out is not None:
            self._pending_cover = None
            self._path = out
            self.cover.set_audio_path(self._path)
            title = self.current_tags().get("title") or out.name
            self.status.setText(f"Saved · {title}")
            self.file_lbl.setText(out.name)
            self.saved.emit(str(out))
        else:
            QMessageBox.warning(self, "Save metadata", msg)

    def _save_as(self):
        if not self._path or not self._path.is_file():
            return
        ext = self._path.suffix.lower() or ".wav"
        filters = {
            ".mp3": "MP3 (*.mp3)",
            ".wav": "WAV (*.wav)",
            ".flac": "FLAC (*.flac)",
            ".m4a": "M4A (*.m4a)",
            ".aiff": "AIFF (*.aiff)",
            ".aif": "AIFF (*.aif)",
            ".ogg": "OGG (*.ogg)",
        }
        filt = filters.get(ext, f"Audio (*{ext})")
        picked, _ = QFileDialog.getSaveFileName(
            self,
            "Save audio with metadata",
            str(self._path),
            f"{filt};;All files (*.*)",
        )
        if not picked:
            return
        dest = Path(picked)
        if dest.suffix.lower() != ext:
            dest = dest.with_suffix(ext)

        if dest.exists() and dest.resolve() != self._path.resolve():
            ans = QMessageBox.question(
                self,
                "Overwrite file?",
                f"Replace existing file?\n\n{dest}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if ans != QMessageBox.StandardButton.Yes:
                return

        ok, msg, out = self._apply_to_path(dest)
        if ok and out is not None:
            self._pending_cover = None
            self._path = out
            self.cover.set_audio_path(self._path)
            self.status.setText(f"Saved · {out.name}")
            self.file_lbl.setText(out.name)
            self.saved.emit(str(out))
            QMessageBox.information(
                self,
                "Saved",
                f"Audio written with metadata and cover:\n\n{out}",
            )
        else:
            QMessageBox.warning(self, "Save as", msg)
