"""
Invite-only beta license gate.

Flow (offline-capable for private beta):
1. Owner mints invite codes bound to emails (`tools/mint_beta_invite.py`)
   → written to config/beta_invites.json
2. Tester enters email + 6-digit code on first launch
3. On match, activation is written to config/beta_license.json
4. Optional: SMTP confirmation when COPRODUCER_SMTP_* env vars are set

No network is required for the default invite-code path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import smtplib
import ssl
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def is_valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match(normalize_email(email)))


def _hash_code(email: str, code: str) -> str:
    raw = f"{normalize_email(email)}|{code.strip()}|coproducer-beta-v1"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class BetaStatus:
    activated: bool
    email: str | None = None
    activated_at: str | None = None
    message: str = ""
    invite_id: str | None = None


@dataclass
class InviteRecord:
    email: str
    code_hash: str
    code_hint: str  # last 2 digits only for owner support
    created_at: str
    note: str = ""
    used: bool = False
    used_at: str | None = None
    invite_id: str = field(default_factory=lambda: secrets.token_hex(8))


class BetaGate:
    """File-backed invite + activation store under project config/."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.config = self.root / "config"
        self.config.mkdir(parents=True, exist_ok=True)
        self.invites_path = self.config / "beta_invites.json"
        self.license_path = self.config / "beta_license.json"
        self.pending_path = self.config / "beta_pending.json"

    # ------------------------------------------------------------------ IO

    def _read_json(self, path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _write_json(self, path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)

    def load_invites(self) -> list[dict[str, Any]]:
        data = self._read_json(self.invites_path, {"invites": []})
        return list(data.get("invites") or [])

    def save_invites(self, invites: list[dict[str, Any]]) -> None:
        self._write_json(
            self.invites_path,
            {"version": 1, "updated_at": utc_now(), "invites": invites},
        )

    # ------------------------------------------------------------------ mint

    def mint_invite(self, email: str, *, note: str = "", code: str | None = None) -> dict[str, Any]:
        if not is_valid_email(email):
            raise ValueError(f"Invalid email: {email}")
        email_n = normalize_email(email)
        code = (code or f"{secrets.randbelow(1_000_000):06d}").strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ValueError("Code must be 6 digits")
        rec = InviteRecord(
            email=email_n,
            code_hash=_hash_code(email_n, code),
            code_hint=code[-2:],
            created_at=utc_now(),
            note=note or "",
        )
        invites = self.load_invites()
        # replace prior unused invite for same email
        invites = [
            i
            for i in invites
            if not (
                normalize_email(i.get("email", "")) == email_n and not i.get("used")
            )
        ]
        invites.append(asdict(rec))
        self.save_invites(invites)
        return {
            "email": email_n,
            "code": code,
            "invite_id": rec.invite_id,
            "created_at": rec.created_at,
            "invites_path": str(self.invites_path),
        }

    # ------------------------------------------------------------------ activate

    def status(self) -> BetaStatus:
        # Dev override
        if os.environ.get("COPRODUCER_BETA_BYPASS", "").strip() in {"1", "true", "yes"}:
            return BetaStatus(True, "dev@localhost", utc_now(), "Bypass env enabled", "bypass")
        lic = self._read_json(self.license_path, {})
        if lic.get("activated") and lic.get("email"):
            return BetaStatus(
                True,
                lic.get("email"),
                lic.get("activated_at"),
                "Activated",
                lic.get("invite_id"),
            )
        return BetaStatus(False, None, None, "Activation required")

    def activate(self, email: str, code: str) -> BetaStatus:
        if not is_valid_email(email):
            return BetaStatus(False, None, None, "Enter a valid email address.")
        email_n = normalize_email(email)
        code = (code or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            return BetaStatus(False, email_n, None, "Code must be exactly 6 digits.")

        # Master code from env for owner testing
        master = os.environ.get("COPRODUCER_BETA_MASTER_CODE", "").strip()
        if master and code == master:
            self._write_license(email_n, invite_id="master")
            return self.status()

        h = _hash_code(email_n, code)
        invites = self.load_invites()
        match = None
        for inv in invites:
            if normalize_email(inv.get("email", "")) != email_n:
                continue
            if inv.get("code_hash") == h:
                match = inv
                break
        if not match:
            return BetaStatus(
                False,
                email_n,
                None,
                "Invalid email or code. Ask the owner for an invite.",
            )
        if match.get("used") and match.get("bound_email") not in (None, email_n):
            return BetaStatus(False, email_n, None, "This invite was already used.")

        match["used"] = True
        match["used_at"] = utc_now()
        match["bound_email"] = email_n
        self.save_invites(invites)
        self._write_license(email_n, invite_id=match.get("invite_id"))
        return self.status()

    def _write_license(self, email: str, invite_id: str | None = None) -> None:
        self._write_json(
            self.license_path,
            {
                "activated": True,
                "email": normalize_email(email),
                "activated_at": utc_now(),
                "invite_id": invite_id,
                "product": "CoProducer",
                "channel": "v1-beta",
            },
        )

    # ------------------------------------------------------------------ SMTP optional confirmation

    def request_email_code(self, email: str) -> dict[str, Any]:
        """
        Generate a one-time code for email and either:
        - send via SMTP if configured, or
        - store pending and return code_preview only when COPRODUCER_BETA_SHOW_CODE=1
        """
        if not is_valid_email(email):
            raise ValueError("Invalid email")
        email_n = normalize_email(email)
        code = f"{secrets.randbelow(1_000_000):06d}"
        pending = {
            "email": email_n,
            "code_hash": _hash_code(email_n, code),
            "created_at": utc_now(),
            "expires_minutes": 30,
        }
        self._write_json(self.pending_path, pending)

        sent = False
        err = None
        try:
            sent = self._smtp_send_code(email_n, code)
        except Exception as exc:
            err = str(exc)

        out: dict[str, Any] = {
            "email": email_n,
            "sent": sent,
            "error": err,
            "message": (
                "Confirmation code sent. Check your inbox."
                if sent
                else "Code stored. SMTP not configured — use an owner-minted invite code, "
                "or set COPRODUCER_SMTP_* env vars."
            ),
        }
        if os.environ.get("COPRODUCER_BETA_SHOW_CODE", "").strip() in {"1", "true", "yes"}:
            out["code_preview"] = code  # local testing only
        return out

    def activate_pending_email_code(self, email: str, code: str) -> BetaStatus:
        pending = self._read_json(self.pending_path, {})
        email_n = normalize_email(email)
        if normalize_email(pending.get("email", "")) != email_n:
            return BetaStatus(False, email_n, None, "No pending code for this email.")
        if pending.get("code_hash") != _hash_code(email_n, code):
            return BetaStatus(False, email_n, None, "Incorrect confirmation code.")
        # Also mint invite record for audit trail
        try:
            self.mint_invite(email_n, note="smtp-self-serve", code=code)
        except Exception:
            pass
        return self.activate(email_n, code)

    def smtp_configured(self) -> bool:
        """True when COPRODUCER_SMTP_* env vars are set for outbound mail."""
        host = os.environ.get("COPRODUCER_SMTP_HOST", "").strip()
        user = os.environ.get("COPRODUCER_SMTP_USER", "").strip()
        password = os.environ.get("COPRODUCER_SMTP_PASS", "").strip()
        return bool(host and user and password)

    def send_invite_email(self, email: str, code: str, *, note: str = "") -> dict[str, Any]:
        """
        Email a minted invite code to the tester.
        Returns {ok, sent, error, message}.
        Requires COPRODUCER_SMTP_HOST / USER / PASS (optional PORT, FROM).
        """
        if not is_valid_email(email):
            return {
                "ok": False,
                "sent": False,
                "error": "Invalid email",
                "message": "Enter a valid email address.",
            }
        email_n = normalize_email(email)
        code = (code or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            return {
                "ok": False,
                "sent": False,
                "error": "Bad code",
                "message": "Code must be 6 digits.",
            }
        if not self.smtp_configured():
            return {
                "ok": False,
                "sent": False,
                "error": "SMTP not configured",
                "message": (
                    "Email not sent — SMTP is not set up.\n"
                    "Set COPRODUCER_SMTP_HOST, COPRODUCER_SMTP_USER, "
                    "COPRODUCER_SMTP_PASS (and optional COPRODUCER_SMTP_PORT / FROM).\n"
                    "You can still copy the code and send it yourself."
                ),
            }
        try:
            self._smtp_send_invite(email_n, code, note=note)
            return {
                "ok": True,
                "sent": True,
                "error": None,
                "message": f"Invite email sent to {email_n}.",
            }
        except Exception as exc:
            return {
                "ok": False,
                "sent": False,
                "error": str(exc),
                "message": f"Send failed: {exc}",
            }

    def _smtp_send_invite(self, email: str, code: str, *, note: str = "") -> None:
        host = os.environ.get("COPRODUCER_SMTP_HOST", "").strip()
        user = os.environ.get("COPRODUCER_SMTP_USER", "").strip()
        password = os.environ.get("COPRODUCER_SMTP_PASS", "").strip()
        port = int(os.environ.get("COPRODUCER_SMTP_PORT", "587") or "587")
        from_addr = os.environ.get("COPRODUCER_SMTP_FROM", user).strip() or user
        note_line = f"\nNote: {note}\n" if note else "\n"
        msg = EmailMessage()
        msg["Subject"] = "Your CoProducer Beta invite code"
        msg["From"] = from_addr
        msg["To"] = email
        msg.set_content(
            "You're invited to the CoProducer private beta.\n\n"
            f"Email:  {email}\n"
            f"Code:   {code}\n"
            f"{note_line}"
            "Open CoProducer and enter this email + code on the activation screen.\n"
            "The code is single-use and bound to this email.\n\n"
            "If you did not expect this invite, you can ignore this message.\n"
        )
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(msg)

    def _smtp_send_code(self, email: str, code: str) -> bool:
        host = os.environ.get("COPRODUCER_SMTP_HOST", "").strip()
        user = os.environ.get("COPRODUCER_SMTP_USER", "").strip()
        password = os.environ.get("COPRODUCER_SMTP_PASS", "").strip()
        port = int(os.environ.get("COPRODUCER_SMTP_PORT", "587") or "587")
        from_addr = os.environ.get("COPRODUCER_SMTP_FROM", user).strip()
        if not host or not user or not password or not from_addr:
            return False
        msg = EmailMessage()
        msg["Subject"] = "CoProducer Beta — confirmation code"
        msg["From"] = from_addr
        msg["To"] = email
        msg.set_content(
            f"Your CoProducer v1 Beta confirmation code is:\n\n  {code}\n\n"
            f"Enter this code in the app with your email ({email}).\n"
            f"If you did not request this, ignore this message.\n"
        )
        context = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls(context=context)
            smtp.login(user, password)
            smtp.send_message(msg)
        return True
