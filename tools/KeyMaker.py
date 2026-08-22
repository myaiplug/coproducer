# -*- coding: utf-8 -*-
#!/usr/bin/env python3
"""
CoProducer Activation Key Maker — owner tool.

Mints managed-pro access passwords (uniform with Liminal/StemSplit licensing):
  - Enter tester email (+ optional note)
  - Generate an access password (stored in %LOCALAPPDATA%\\CoProducer\\managed_pro_users.json)
  - Copy password / full invite text
  - Send invite email (if COPRODUCER_SMTP_* is configured)

Launch:  KEY_MAKER.bat   or   py -3.11 tools/KeyMaker.py
"""

from __future__ import annotations

import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from pathlib import Path

# Project root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from nodaw.beta.license import is_valid_email
from nodaw.licensing import issue_managed_pro, managed_pro_users_path


# Dark, compact owner chrome
BG = "#0f1218"
SURFACE = "#171c26"
ELEVATED = "#1e2533"
TEXT = "#e8ecf4"
MUTED = "#8b95a8"
ACCENT = "#5b8cff"
ACCENT_SOFT = "#8eb0ff"
OK = "#3ecf8e"
WARN = "#f0b429"
ERR = "#f07178"
LINE = "#2a3344"


class KeyMakerWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CoProducer — Activation Key Maker")
        self.setMinimumSize(620, 640)
        self.resize(680, 700)

        self._last: dict | None = None

        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(14)

        title = QLabel("Activation Key Maker")
        title.setStyleSheet(
            f"font-size: 24px; font-weight: 700; color: {TEXT}; background: transparent;"
        )
        lay.addWidget(title)

        sub = QLabel(
            "Mint a managed-pro access password bound to an email. "
            "Users activate in-app with their email + this password — same flow as "
            "StemSplit / Liminal. Accounts are stored in the local licensing database."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet(f"font-size: 14px; color: {MUTED}; background: transparent;")
        lay.addWidget(sub)

        card = QFrame()
        card.setStyleSheet(
            f"""
            QFrame {{
                background: {SURFACE};
                border: 1px solid {LINE};
                border-radius: 14px;
            }}
            """
        )
        form_wrap = QVBoxLayout(card)
        form_wrap.setContentsMargins(20, 18, 20, 18)
        form_wrap.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(14)
        form.setHorizontalSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.email_edit = QLineEdit()
        self.email_edit.setPlaceholderText("tester@example.com")
        self.email_edit.setClearButtonEnabled(True)
        self.email_edit.returnPressed.connect(self._generate)

        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("Optional note (e.g. beta wave 1)")
        self.note_edit.setClearButtonEnabled(True)

        self.pw_edit = QLineEdit()
        self.pw_edit.setPlaceholderText("Leave blank for random access password")
        self.pw_edit.setClearButtonEnabled(True)

        # Tall fields so text is fully visible
        for w in (self.email_edit, self.note_edit, self.pw_edit):
            w.setMinimumHeight(48)
            w.setFont(QFont("Segoe UI", 14))

        lbl_style = (
            f"color: {MUTED}; font-size: 14px; font-weight: 600; background: transparent;"
        )
        for lbl_text, widget in (
            ("Email", self.email_edit),
            ("Note", self.note_edit),
            ("Access password", self.pw_edit),
        ):
            lab = QLabel(lbl_text)
            lab.setStyleSheet(lbl_style)
            lab.setMinimumWidth(120)
            form.addRow(lab, widget)

        form_wrap.addLayout(form)
        lay.addWidget(card)

        # Actions
        row = QHBoxLayout()
        row.setSpacing(10)
        self.gen_btn = QPushButton("Generate password")
        self.gen_btn.setObjectName("Primary")
        self.gen_btn.setCursor(Qt.PointingHandCursor)
        self.gen_btn.setMinimumHeight(44)
        self.gen_btn.clicked.connect(self._generate)
        row.addWidget(self.gen_btn)

        self.copy_code_btn = QPushButton("Copy password")
        self.copy_code_btn.setCursor(Qt.PointingHandCursor)
        self.copy_code_btn.setMinimumHeight(44)
        self.copy_code_btn.setEnabled(False)
        self.copy_code_btn.clicked.connect(self._copy_password)
        row.addWidget(self.copy_code_btn)

        self.copy_all_btn = QPushButton("Copy invite text")
        self.copy_all_btn.setCursor(Qt.PointingHandCursor)
        self.copy_all_btn.setMinimumHeight(44)
        self.copy_all_btn.setEnabled(False)
        self.copy_all_btn.clicked.connect(self._copy_invite_text)
        row.addWidget(self.copy_all_btn)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.send_btn = QPushButton("Send to email")
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setMinimumHeight(44)
        self.send_btn.setEnabled(False)
        self.send_btn.setToolTip(
            "Emails the invite using COPRODUCER_SMTP_HOST / USER / PASS"
        )
        self.send_btn.clicked.connect(self._send_email)
        row2.addWidget(self.send_btn)

        self.gen_send_btn = QPushButton("Generate + send")
        self.gen_send_btn.setCursor(Qt.PointingHandCursor)
        self.gen_send_btn.setMinimumHeight(44)
        self.gen_send_btn.setToolTip("Mint a new access password and email it in one step")
        self.gen_send_btn.clicked.connect(self._generate_and_send)
        row2.addWidget(self.gen_send_btn)
        lay.addLayout(row2)

        # Result display
        out_lbl = QLabel("Last invite")
        out_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {MUTED}; "
            f"letter-spacing: 0.6px; background: transparent;"
        )
        lay.addWidget(out_lbl)

        self.result = QTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("Generated invite appears here…")
        self.result.setMinimumHeight(180)
        self.result.setFont(QFont("Consolas", 14))
        lay.addWidget(self.result, 1)

        self.status = QLabel(self._smtp_status_text())
        self.status.setWordWrap(True)
        self.status.setStyleSheet(
            f"font-size: 13px; color: {MUTED}; background: transparent;"
        )
        lay.addWidget(self.status)

        path_lbl = QLabel(f"Licensing database: {managed_pro_users_path()}")
        path_lbl.setWordWrap(True)
        path_lbl.setStyleSheet(
            f"font-size: 12px; color: {MUTED}; background: transparent;"
        )
        lay.addWidget(path_lbl)

        self._apply_style()
        self.email_edit.setFocus()

    def _smtp_status_text(self) -> str:
        host = os.environ.get("COPRODUCER_SMTP_HOST", "").strip()
        user = os.environ.get("COPRODUCER_SMTP_USER", "").strip()
        pw = os.environ.get("COPRODUCER_SMTP_PASS", "").strip()
        if host and user and pw:
            return f"SMTP ready ({host}) — Send to email will work."
        return (
            "SMTP not configured — use Copy to share passwords manually.\n"
            "To enable send: set COPRODUCER_SMTP_HOST, COPRODUCER_SMTP_USER, "
            "COPRODUCER_SMTP_PASS (optional PORT / FROM)."
        )

    def _apply_style(self) -> None:
        self.setStyleSheet(
            f"""
            QMainWindow, QWidget {{
                background: {BG};
                color: {TEXT};
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: 15px;
            }}
            QLineEdit {{
                background: {ELEVATED};
                border: 1px solid {LINE};
                border-radius: 10px;
                padding: 12px 14px;
                min-height: 28px;
                color: {TEXT};
                font-size: 15px;
                selection-background-color: {ACCENT};
            }}
            QLineEdit:focus {{
                border-color: {ACCENT};
            }}
            QTextEdit {{
                background: {ELEVATED};
                border: 1px solid {LINE};
                border-radius: 12px;
                padding: 14px;
                color: {TEXT};
                font-size: 15px;
            }}
            QPushButton {{
                background: {ELEVATED};
                border: 1px solid {LINE};
                border-radius: 10px;
                padding: 12px 18px;
                color: {TEXT};
                font-size: 14px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                border-color: {ACCENT};
                background: #243049;
            }}
            QPushButton:disabled {{
                color: {MUTED};
                border-color: {LINE};
            }}
            QPushButton#Primary {{
                background: {ACCENT};
                border: none;
                color: #0a0e16;
                font-size: 15px;
            }}
            QPushButton#Primary:hover {{
                background: {ACCENT_SOFT};
            }}
            """
        )

    def _set_status(self, text: str, *, kind: str = "info") -> None:
        color = {"ok": OK, "warn": WARN, "err": ERR}.get(kind, MUTED)
        self.status.setText(text)
        self.status.setStyleSheet(
            f"font-size: 11px; color: {color}; background: transparent;"
        )

    def _invite_text(self, inv: dict) -> str:
        note = (self.note_edit.text() or "").strip()
        lines = [
            "CoProducer access invite",
            f"Email: {inv['email']}",
            f"Access password: {inv['password']}",
        ]
        if note:
            lines.append(f"Note:  {note}")
        lines.append("")
        lines.append("Open CoProducer, enter this email + access password to activate.")
        return "\n".join(lines)

    def _show_result(self, inv: dict) -> None:
        self._last = inv
        self.result.setPlainText(self._invite_text(inv))
        self.copy_code_btn.setEnabled(True)
        self.copy_all_btn.setEnabled(True)
        self.send_btn.setEnabled(True)
        # Fill password field so user sees it
        self.pw_edit.setText(inv["password"])

    def _generate(self) -> bool:
        email = self.email_edit.text().strip()
        if not is_valid_email(email):
            self._set_status("Enter a valid email address.", kind="err")
            QMessageBox.warning(self, "Email", "Enter a valid email address.")
            return False
        note = self.note_edit.text().strip()
        fixed = self.pw_edit.text().strip() or None
        try:
            inv = issue_managed_pro(email, password=fixed, note=note)
        except Exception as exc:
            self._set_status(str(exc), kind="err")
            QMessageBox.critical(self, "Mint failed", str(exc))
            return False
        self._show_result(inv)
        self._set_status(
            f"Access password created for {inv['email']}.",
            kind="ok",
        )
        return True

    def _copy_password(self) -> None:
        if not self._last:
            return
        QGuiApplication.clipboard().setText(str(self._last["password"]))
        self._set_status("Access password copied to clipboard.", kind="ok")

    def _copy_invite_text(self) -> None:
        if not self._last:
            return
        text = self._invite_text(self._last)
        QGuiApplication.clipboard().setText(text)
        self._set_status("Full invite text copied to clipboard.", kind="ok")

    def _send_email(self) -> None:
        if not self._last:
            if not self._generate():
                return
        inv = self._last
        assert inv is not None
        res = self._smtp_send_invite(inv["email"], inv["password"])
        if res.get("sent"):
            self._set_status(res["message"], kind="ok")
            QMessageBox.information(self, "Email sent", res["message"])
        else:
            self._set_status(res.get("message") or "Send failed", kind="warn")
            QMessageBox.warning(
                self,
                "Email not sent",
                res.get("message") or "Could not send email.",
            )

    def _generate_and_send(self) -> None:
        if not self._generate():
            return
        self._send_email()

    def _smtp_send_invite(self, email: str, password: str) -> dict:
        host = os.environ.get("COPRODUCER_SMTP_HOST", "").strip()
        user = os.environ.get("COPRODUCER_SMTP_USER", "").strip()
        secret = os.environ.get("COPRODUCER_SMTP_PASS", "").strip()
        if not host or not user or not secret:
            return {
                "ok": False,
                "sent": False,
                "message": (
                    "Email not sent — SMTP is not set up.\n"
                    "Set COPRODUCER_SMTP_HOST, COPRODUCER_SMTP_USER, "
                    "COPRODUCER_SMTP_PASS (optional PORT / FROM).\n"
                    "You can still copy the password and send it yourself."
                ),
            }
        port = int(os.environ.get("COPRODUCER_SMTP_PORT", "587") or "587")
        from_addr = os.environ.get("COPRODUCER_SMTP_FROM", user).strip() or user
        note = self.note_edit.text().strip()
        note_line = f"\nNote: {note}\n" if note else "\n"
        msg = EmailMessage()
        msg["Subject"] = "Your CoProducer access invite"
        msg["From"] = from_addr
        msg["To"] = email
        msg.set_content(
            "You're invited to CoProducer.\n\n"
            f"Email:            {email}\n"
            f"Access password:  {password}\n"
            f"{note_line}"
            "Open CoProducer and enter this email + access password on the activation screen.\n"
            "Keep it private — it is bound to your email.\n\n"
            "If you did not expect this invite, you can ignore this message.\n"
        )
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=30) as smtp:
                smtp.starttls(context=context)
                smtp.login(user, secret)
                smtp.send_message(msg)
            return {"ok": True, "sent": True, "message": f"Invite email sent to {email}."}
        except Exception as exc:
            return {"ok": False, "sent": False, "message": f"Send failed: {exc}"}


def main() -> int:
    # Hide console flash on Windows if launched via python.exe
    if sys.platform == "win32":
        try:
            import ctypes

            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = KeyMakerWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
