"""
CoProducer licensing - uniform with Liminal (StemSplit) desktop licensing.

Sources (same constants/behavior as StemSplit's `src-tauri/src/main.rs`):
  - dev_bypass            env-key override (dev only)
  - managed_pro           local owner-minted accounts (%LOCALAPPDATA%\\CoProducer\\managed_pro_users.json)
  - remote_license_server POST {billing}/api/licenses/validate (Liminal billing-service, optional)
  - gumroad               POST https://api.gumroad.com/v2/licenses/verify (per-product product_id)

Per-program isolation: everything is stored under %LOCALAPPDATA%\\CoProducer\\ so
CoProducer and Liminal/StemSplit users never share or confuse license state.

Storage (%LOCALAPPDATA%\\CoProducer\\license.json, same schema as StemSplit StoredLicense):
  { license_key, email, activated_at, last_verified (unix), is_valid, source }

Managed-pro credentials are verified as sha256(password) === stored password_sha256,
matching StemSplit's `verify_managed_pro_credentials`. Managed-pro activations store
the sentinel "MANAGED-PRO-AUTH" (never the raw password), same as StemSplit.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sys
import urllib.parse
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:  # urllib is stdlib; keep import optional-safe for headless tools
    import urllib.request
    from urllib.error import HTTPError
except Exception:  # pragma: no cover
    urllib = None
    HTTPError = None

from nodaw.beta.license import is_valid_email, normalize_email

PRODUCT_FOLDER = "CoProducer"
LICENSE_FILE = "license.json"
MANAGED_PRO_USERS_FILE = "managed_pro_users.json"

# Mirrors StemSplit LICENSE_RECHECK_INTERVAL (7 days)
LICENSE_RECHECK_INTERVAL = 7 * 24 * 60 * 60
# Mirrors StemSplit offline grace period (30 days)
LICENSE_GRACE_PERIOD = 30 * 24 * 60 * 60

# Mirrors StemSplit LICENSE_SOURCE_* values
LICENSE_SOURCE_DEV_BYPASS = "dev_bypass"
LICENSE_SOURCE_MANAGED_PRO = "managed_pro"
LICENSE_SOURCE_GUMROAD = "gumroad"
LICENSE_SOURCE_REMOTE = "remote_license_server"

# Mirrors StemSplit sentinels written to license.json / shown in the UI
MANAGED_PRO_AUTH_KEY = "MANAGED-PRO-AUTH"
MANAGED_PRO_DISPLAY_KEY = "MANAGED-****-PRO"
REMOTE_DISPLAY_KEY = "REMOTE-****-ACCESS"
DEV_BYPASS_DISPLAY_KEY = "DEV-****-ACCESS"

# Mirrors StemSplit GUMROAD_PRODUCT_ID - CoProducer's own Gumroad product permalink ID.
# Set once the CoProducer Gumroad product is live; overridden by config/settings.json
# licensing.gumroad_product_id and the COPRODUCER_GUMROAD_PRODUCT_ID env var.
GUMROAD_PRODUCT_ID = os.environ.get("COPRODUCER_GUMROAD_PRODUCT_ID", "").strip()

DEFAULT_BILLING_URL = "https://nodaw-entitlements-staging.onrender.com"


def _app_root() -> Path:
    """Writable install or project root (mirrors CoProducerDesktop._resolve_roots)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def resolve_billing_url() -> str:
    """Remote billing URL for license validation.

    Priority: COPRODUCER_BILLING_URL env var, then config/settings.json
    licensing.billing_url, then DEFAULT_BILLING_URL. Never raises.
    """
    env = os.environ.get("COPRODUCER_BILLING_URL", "").strip()
    if env:
        return env
    try:
        from nodaw.config import ProjectPaths, load_settings

        settings = load_settings(ProjectPaths(_app_root()))
        url = str(settings.get("licensing", {}).get("billing_url", "") or "").strip()
        if url:
            return url
    except Exception:
        pass
    return DEFAULT_BILLING_URL


def resolve_gumroad_product_id() -> str:
    """CoProducer Gumroad product permalink ID.

    Priority: COPRODUCER_GUMROAD_PRODUCT_ID env var, then config/settings.json
    licensing.gumroad_product_id, then the GUMROAD_PRODUCT_ID constant.
    """
    env = os.environ.get("COPRODUCER_GUMROAD_PRODUCT_ID", "").strip()
    if env:
        return env
    try:
        from nodaw.config import ProjectPaths, load_settings

        settings = load_settings(ProjectPaths(_app_root()))
        pid = str(settings.get("licensing", {}).get("gumroad_product_id", "") or "").strip()
        if pid:
            return pid
    except Exception:
        pass
    return GUMROAD_PRODUCT_ID


BYpassTrue = {"1", "true", "yes", "on"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_now_unix() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def mask_license_key(key: str) -> str:
    """Mask license key for display - mirrors StemSplit `mask_license_key`."""
    key = key or ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


# --------------------------------------------------------------------------- dirs

def data_dir() -> Path:
    override = os.environ.get("COPRODUCER_LICENSE_DIR", "").strip()
    if override:
        return Path(override).resolve()
    local = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(local) / PRODUCT_FOLDER


def license_path() -> Path:
    return data_dir() / LICENSE_FILE


def managed_pro_users_path() -> Path:
    return data_dir() / MANAGED_PRO_USERS_FILE


# --------------------------------------------------------------------------- storage

def _read_json(path: Path, default: Any) -> Any:
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


@dataclass
class StoredLicense:
    """Same JSON shape as StemSplit's StoredLicense."""

    license_key: str
    email: str
    activated_at: str
    last_verified: int  # unix seconds
    is_valid: bool
    source: str


def load_stored_license() -> StoredLicense | None:
    data = _read_json(license_path(), {})
    if not data or not isinstance(data, dict):
        return None
    try:
        return StoredLicense(
            license_key=str(data.get("license_key", "")),
            email=str(data.get("email", "")),
            activated_at=str(data.get("activated_at", "")),
            last_verified=int(data.get("last_verified", 0)),
            is_valid=bool(data.get("is_valid", False)),
            source=str(data.get("source", "")),
        )
    except Exception:
        return None


def save_stored_license(lic: StoredLicense) -> None:
    _write_json(license_path(), asdict(lic))


@dataclass
class ManagedProUser:
    """Same JSON shape as StemSplit's ManagedProUser."""

    email: str
    password_sha256: str
    enabled: bool = True


@dataclass
class ManagedProUsersDb:
    """Same JSON shape as StemSplit's ManagedProUsersDb."""

    users: list[ManagedProUser] = field(default_factory=list)


def load_managed_pro_db() -> ManagedProUsersDb:
    data = _read_json(managed_pro_users_path(), {})
    users = []
    for item in data.get("users") or []:
        if not isinstance(item, dict):
            continue
        users.append(
            ManagedProUser(
                email=str(item.get("email", "")).lower(),
                password_sha256=str(item.get("password_sha256", "")),
                enabled=bool(item.get("enabled", False)),
            )
        )
    return ManagedProUsersDb(users=users)


def save_managed_pro_db(db: ManagedProUsersDb) -> None:
    _write_json(
        managed_pro_users_path(),
        {"version": 1, "users": [asdict(u) for u in db.users]},
    )


def verify_managed_pro_credentials(email: str, password: str) -> bool:
    """sha256(password) === stored password_sha256 - mirrors StemSplit."""
    email_n = normalize_email(email)
    pw = (password or "").strip()
    if not email_n or not pw:
        return False
    digest = sha256_hex(pw)
    for user in load_managed_pro_db().users:
        if user.email == email_n and user.enabled and user.password_sha256 == digest:
            return True
    return False


def has_managed_pro_email(email: str) -> bool:
    """Mirrors StemSplit `has_managed_pro_email`."""
    email_n = normalize_email(email)
    return any(u.email == email_n for u in load_managed_pro_db().users)


def is_managed_pro_email_enabled(email: str) -> bool:
    """Mirrors StemSplit `is_managed_pro_email_enabled`."""
    email_n = normalize_email(email)
    return any(u.email == email_n and u.enabled for u in load_managed_pro_db().users)


def issue_managed_pro(email: str, *, password: str | None = None, note: str = "") -> dict[str, Any]:
    """Owner tool: mint a managed-pro access password for an email."""
    if not is_valid_email(email):
        raise ValueError(f"Invalid email: {email}")
    email_n = normalize_email(email)
    pw = (password or secrets.token_urlsafe(12)).strip()
    if len(pw) < 8:
        raise ValueError("Password must be at least 8 characters.")
    db = load_managed_pro_db()
    kept = [u for u in db.users if u.email != email_n]
    kept.append(ManagedProUser(email=email_n, password_sha256=sha256_hex(pw)))
    save_managed_pro_db(ManagedProUsersDb(users=kept))
    return {
        "email": email_n,
        "password": pw,
        "note": note,
        "source": LICENSE_SOURCE_MANAGED_PRO,
        "users_path": str(managed_pro_users_path()),
        "created_at": utc_now_iso(),
    }


# --------------------------------------------------------------------------- status

@dataclass
class LicenseStatus:
    """Mirrors StemSplit's LicenseInfo shape."""

    is_valid: bool = False
    is_trial: bool = False
    email: str | None = None
    purchase_date: str | None = None
    license_key: str | None = None
    features: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    error: str | None = None
    source: str | None = None

    @property
    def activated(self) -> bool:
        """True only when a real license is active (never for trial / locked-out).

        CoProducer's startup gate uses this flag — trial must not open the app.
        Soft-grace offline licenses set is_valid=True and therefore pass.
        """
        return bool(self.is_valid)

    @property
    def message(self) -> str:
        if self.is_valid:
            return f"Activated for {self.email or ''}"
        if self.is_trial:
            return self.error or "Activation required"
        return self.error or "Activation required"


def _full_features() -> list[str]:
    return ["all"]


def _no_limitations() -> list[str]:
    return []


def _trial_status(email: str | None = None, error: str | None = None) -> LicenseStatus:
    return LicenseStatus(
        is_trial=True,
        email=email,
        error=error,
        limitations=["License required"],
    )


def _dev_bypass_status() -> LicenseStatus | None:
    key = os.environ.get("COPRODUCER_DEV_BYPASS_KEY", "").strip()
    legacy = os.environ.get("COPRODUCER_BETA_BYPASS", "").strip().lower()
    if key or legacy in BYpassTrue:
        return LicenseStatus(
            is_valid=True,
            email="dev@localhost",
            license_key=DEV_BYPASS_DISPLAY_KEY,
            features=_full_features(),
            source=LICENSE_SOURCE_DEV_BYPASS,
        )
    return None


def _migrate_legacy_beta_license() -> StoredLicense | None:
    """One-time migration: config/beta_license.json (v1 beta gate) → local license.json.

    After a successful migration the legacy file is marked `migrated_to_v2` so a
    later deactivate() cannot resurrect access by re-importing the same file.
    """
    legacy = os.environ.get("COPRODUCER_BETA_LICENSE_FILE", "").strip()
    if legacy:
        path = Path(legacy)
    else:
        root_env = os.environ.get("COPRODUCER_PROJECT_ROOT", "").strip()
        if root_env:
            root = Path(root_env)
        else:
            # Prefer app install/project root over cwd (cwd is unreliable at launch)
            root = _app_root()
        path = root / "config" / "beta_license.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    # Already migrated once — never re-activate after user deactivates
    if data.get("migrated_to_v2"):
        return None
    if not data.get("activated") or not data.get("email"):
        return None
    email_n = normalize_email(data.get("email", ""))
    if not email_n:
        return None
    lic = StoredLicense(
        license_key="migrated-v1-beta",
        email=email_n,
        activated_at=str(data.get("activated_at") or utc_now_iso()),
        last_verified=utc_now_unix(),
        is_valid=True,
        source=LICENSE_SOURCE_MANAGED_PRO,
    )
    save_stored_license(lic)
    # Stamp legacy file so deactivate stays sticky
    try:
        data["migrated_to_v2"] = True
        data["migrated_at"] = utc_now_iso()
        data["migrated_email"] = email_n
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        pass
    return lic


def get_license_status() -> LicenseStatus:
    """
    Same per-source semantics as StemSplit `get_license_status`:
      - remote:       stale → re-verify; rejected → trial; unreachable → 30-day grace
      - managed_pro:  valid while the email stays enabled in the local DB
      - gumroad:      stale → re-verify; error/offline → 30-day grace, then trial
    """
    dev = _dev_bypass_status()
    if dev:
        return dev

    lic = load_stored_license()
    if lic is None:
        lic = _migrate_legacy_beta_license()
    if lic is None or not lic.is_valid:
        return _trial_status()

    now = utc_now_unix()
    needs_reverify = (now - lic.last_verified) > LICENSE_RECHECK_INTERVAL

    if lic.source == LICENSE_SOURCE_REMOTE:
        if needs_reverify:
            try:
                remote = _remote_validate(lic.email, lic.license_key)
            except RuntimeError:
                remote = None
            if isinstance(remote, dict) and remote:
                if remote.get("valid"):
                    lic.email = str(remote.get("email") or lic.email)
                    lic.activated_at = str(remote.get("purchase_date") or lic.activated_at)
                    lic.last_verified = now
                    lic.is_valid = True
                    save_stored_license(lic)
                    return LicenseStatus(
                        is_valid=True,
                        email=lic.email,
                        purchase_date=lic.activated_at,
                        license_key=REMOTE_DISPLAY_KEY,
                        features=list(remote.get("features") or _full_features()),
                        limitations=_no_limitations(),
                        source=LICENSE_SOURCE_REMOTE,
                    )
                return _trial_status(
                    lic.email,
                    remote.get("error") or "Remote access credential rejected",
                )
            if (now - lic.last_verified) < LICENSE_GRACE_PERIOD:
                return LicenseStatus(
                    is_valid=True,
                    email=lic.email,
                    purchase_date=lic.activated_at,
                    license_key=REMOTE_DISPLAY_KEY,
                    features=_full_features(),
                    limitations=_no_limitations(),
                    error="Remote license server unavailable, using cached access",
                    source=LICENSE_SOURCE_REMOTE,
                )
            return _trial_status(lic.email, "License re-verification overdue - activate again")
        return LicenseStatus(
            is_valid=True,
            email=lic.email,
            purchase_date=lic.activated_at,
            license_key=REMOTE_DISPLAY_KEY,
            features=_full_features(),
            limitations=_no_limitations(),
            source=LICENSE_SOURCE_REMOTE,
        )

    if lic.source == LICENSE_SOURCE_MANAGED_PRO:
        # Emails issued by the v1 beta migration may not exist in the managed DB;
        # only revoke when the email is present and explicitly disabled.
        if lic.is_valid and (
            not has_managed_pro_email(lic.email) or is_managed_pro_email_enabled(lic.email)
        ):
            return LicenseStatus(
                is_valid=True,
                email=lic.email,
                purchase_date=lic.activated_at,
                license_key=MANAGED_PRO_DISPLAY_KEY,
                features=_full_features(),
                limitations=_no_limitations(),
                source=LICENSE_SOURCE_MANAGED_PRO,
            )
        return _trial_status(lic.email, "Managed Pro access has been disabled for this account")

    if lic.source == LICENSE_SOURCE_DEV_BYPASS:
        return LicenseStatus(
            is_valid=True,
            email=lic.email,
            purchase_date=lic.activated_at,
            license_key=DEV_BYPASS_DISPLAY_KEY,
            features=_full_features(),
            limitations=_no_limitations(),
            source=LICENSE_SOURCE_DEV_BYPASS,
        )

    # gumroad (also the default source for licenses without a known source)
    if needs_reverify:
        try:
            gumroad_email, _created = _verify_with_gumroad(lic.license_key)
            lic.email = gumroad_email or lic.email
            lic.last_verified = now
            lic.is_valid = True
            save_stored_license(lic)
            return LicenseStatus(
                is_valid=True,
                email=lic.email,
                purchase_date=lic.activated_at,
                license_key=mask_license_key(lic.license_key),
                features=_full_features(),
                limitations=_no_limitations(),
                source=LICENSE_SOURCE_GUMROAD,
            )
        except RuntimeError:
            if (now - lic.last_verified) < LICENSE_GRACE_PERIOD:
                days = (now - lic.last_verified) // 86400
                return LicenseStatus(
                    is_valid=True,
                    email=lic.email,
                    purchase_date=lic.activated_at,
                    license_key=mask_license_key(lic.license_key),
                    features=_full_features(),
                    limitations=_no_limitations(),
                    error=f"Offline mode - last verified {days} days ago",
                    source=LICENSE_SOURCE_GUMROAD,
                )
            return _trial_status(lic.email, "License re-verification overdue - activate again")
    return LicenseStatus(
        is_valid=True,
        email=lic.email,
        purchase_date=lic.activated_at,
        license_key=mask_license_key(lic.license_key),
        features=_full_features(),
        limitations=_no_limitations(),
        source=LICENSE_SOURCE_GUMROAD,
    )


# --------------------------------------------------------------------------- activate / deactivate

def _remote_validate(email: str, license_key: str) -> dict[str, Any]:
    """POST {billing}/api/licenses/validate - same contract as Liminal billing-service."""
    if urllib is None:
        raise RuntimeError("urllib unavailable")
    base = resolve_billing_url()
    url = base.rstrip("/") + "/api/licenses/validate"
    body = json.dumps({"email": normalize_email(email), "licenseKey": license_key}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raw = exc.read()  # server answered (e.g. 404) - parse its JSON body
    except Exception as exc:
        raise RuntimeError(f"License server unreachable: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"License server returned invalid JSON: {exc}") from exc
    return data if isinstance(data, dict) else {}


def _verify_with_gumroad(license_key: str) -> tuple[str | None, str | None]:
    """
    POST https://api.gumroad.com/v2/licenses/verify - mirrors StemSplit
    `verify_with_gumroad`. Returns (purchase_email, created_at) on success.
    """
    if urllib is None:
        raise RuntimeError("urllib unavailable")
    product_id = resolve_gumroad_product_id()
    if not product_id:
        raise RuntimeError(
            "Gumroad licensing is not configured for this build "
            "(set COPRODUCER_GUMROAD_PRODUCT_ID)."
        )
    body = urllib.parse.urlencode(
        {
            "product_id": product_id,
            "license_key": (license_key or "").strip(),
            "increment_uses_count": "false",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://api.gumroad.com/v2/licenses/verify",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
    except HTTPError as exc:
        raw = exc.read()
    except Exception as exc:
        raise RuntimeError(f"Gumroad unreachable: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Gumroad returned invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or not data.get("success"):
        raise RuntimeError(str(data.get("message") or "License verification failed"))
    purchase = data.get("purchase") or {}
    if purchase.get("refunded"):
        raise RuntimeError("This license has been refunded")
    if purchase.get("chargebacked"):
        raise RuntimeError("This license has been chargebacked")
    return purchase.get("email"), purchase.get("created_at")


def activate(email: str, license_key: str) -> LicenseStatus:
    """
    Activation order matches StemSplit:
      dev bypass → managed pro → remote license server → gumroad.
    On success, writes license.json (StoredLicense schema, source recorded).
    """
    email_n = normalize_email(email)
    key = (license_key or "").strip()

    dev = _dev_bypass_status()
    if dev:
        save_stored_license(
            StoredLicense(
                license_key="dev-bypass",
                email="dev@localhost",
                activated_at=utc_now_iso(),
                last_verified=utc_now_unix(),
                is_valid=True,
                source=LICENSE_SOURCE_DEV_BYPASS,
            )
        )
        return dev

    if not is_valid_email(email_n):
        return LicenseStatus(error="Enter a valid email address.")
    if not key:
        return LicenseStatus(error="Enter your license key.", email=email_n)

    # 1) local managed-pro (offline-capable, sha256) - raw password never stored
    if verify_managed_pro_credentials(email_n, key):
        save_stored_license(
            StoredLicense(
                license_key=MANAGED_PRO_AUTH_KEY,
                email=email_n,
                activated_at=utc_now_iso(),
                last_verified=utc_now_unix(),
                is_valid=True,
                source=LICENSE_SOURCE_MANAGED_PRO,
            )
        )
        return LicenseStatus(
            is_valid=True,
            email=email_n,
            purchase_date=utc_now_iso(),
            license_key=MANAGED_PRO_DISPLAY_KEY,
            features=_full_features(),
            limitations=_no_limitations(),
            source=LICENSE_SOURCE_MANAGED_PRO,
        )

    # 2) remote license server (Liminal billing-service contract)
    remote_error: str | None = None
    try:
        remote = _remote_validate(email_n, key)
    except RuntimeError as exc:
        remote = None
        remote_error = str(exc)
    if isinstance(remote, dict) and remote:
        if remote.get("recognized") and remote.get("valid"):
            activated_at = str(remote.get("purchase_date") or utc_now_iso())
            save_stored_license(
                StoredLicense(
                    license_key=key,
                    email=str(remote.get("email") or email_n),
                    activated_at=activated_at,
                    last_verified=utc_now_unix(),
                    is_valid=True,
                    source=LICENSE_SOURCE_REMOTE,
                )
            )
            return LicenseStatus(
                is_valid=True,
                email=email_n,
                purchase_date=activated_at,
                license_key=REMOTE_DISPLAY_KEY,
                features=list(remote.get("features") or _full_features()),
                limitations=_no_limitations(),
                source=LICENSE_SOURCE_REMOTE,
            )
        if remote.get("recognized"):
            return LicenseStatus(
                error=remote.get("error") or "Remote access credential rejected",
                email=email_n,
            )

    # 3) email belongs to a managed-pro account but the password was wrong
    if has_managed_pro_email(email_n):
        return LicenseStatus(
            error="Managed Pro credentials are invalid for this account",
            email=email_n,
        )

    # 4) Gumroad (final fallback - purchase email must match)
    try:
        gumroad_email, created_at = _verify_with_gumroad(key)
    except RuntimeError as exc:
        detail = str(exc)
        if remote_error:
            detail = f"{detail} (also: {remote_error})"
        return LicenseStatus(
            error=detail,
            email=email_n,
            limitations=["Managed-pro password or online license key required."],
        )
    gumroad_email_n = normalize_email(gumroad_email or "")
    if not gumroad_email_n:
        return LicenseStatus(
            error="Could not verify purchase email - please contact support",
            email=email_n,
        )
    if gumroad_email_n != email_n:
        return LicenseStatus(
            error="Email does not match purchase email. "
            "Please use the email you used to purchase on Gumroad.",
            email=email_n,
        )
    activated_at = str(created_at or utc_now_iso())
    save_stored_license(
        StoredLicense(
            license_key=key,
            email=email_n,
            activated_at=activated_at,
            last_verified=utc_now_unix(),
            is_valid=True,
            source=LICENSE_SOURCE_GUMROAD,
        )
    )
    return LicenseStatus(
        is_valid=True,
        email=email_n,
        purchase_date=activated_at,
        license_key=mask_license_key(key),
        features=_full_features(),
        limitations=_no_limitations(),
        source=LICENSE_SOURCE_GUMROAD,
    )


def deactivate() -> LicenseStatus:
    """Removes local license.json (mirrors StemSplit deactivate)."""
    try:
        p = license_path()
        if p.is_file():
            p.unlink()
    except Exception:
        pass
    return _trial_status()
