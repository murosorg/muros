# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""System authentication through PAM.

The MurOS web UI and SSH share the same Linux accounts: an identity is
authenticated against the system PAM stack (service ``muros``).
Credentials live in ``/etc/shadow`` like any other Linux account, so the
password used to log into the web UI is the very same one used to open an
SSH session. The default administrator is ``root``; every other Linux
account is refused entry to the web UI until root grants it access from
the Access > Users page (see the ``ui_access`` gate in routes/auth.py).

The backend runs as root, so it can both validate credentials through
PAM and change passwords through ``chpasswd``. On a developer machine
(``MUROS_APPLY`` unset / false) there is no real PAM account, so a
dev-only fallback accepts the documented default credentials and turns
password writes into no-ops. This keeps ``make backend`` usable without
provisioning Linux accounts.
"""
from __future__ import annotations

import logging
import os
import subprocess

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.pam")

# PAM service file shipped by the package at /etc/pam.d/muros. It simply
# includes the system common-auth / common-account stacks so the web UI
# authenticates exactly like a local login would.
PAM_SERVICE = os.environ.get("MUROS_PAM_SERVICE", "muros")

# Default administrator account shared by the web UI and SSH. MurOS uses
# the system 'root' account directly: the package postinst sets its
# default password on a fresh install and flags must_change_password in
# the DB so the operator rotates it at first login.
ADMIN_USER = os.environ.get("MUROS_ADMIN_USER", "root")

# Dev-only credentials used when MUROS_APPLY is off (no real PAM account
# on a developer laptop). Never used on a deployed appliance.
_DEV_USER = "root"
_DEV_PASSWORD = "muros"


def _load_pam():
    """Import python-pam lazily so the dev box and the ast-based syntax
    checks keep working even without libpam bindings."""
    try:
        import pam  # type: ignore
    except Exception as exc:  # noqa: BLE001
        # Surface the real underlying error (missing libpam, missing
        # transitive dependency such as 'six', API mismatch, ...) instead
        # of a generic message. Hiding it once cost an hour of debugging a
        # broken login on a deployed box (python-pam 2.0.2 imports 'six'
        # without declaring it, so the import failed with a ModuleNotFound
        # that the generic message masked).
        log.error("python-pam import failed: %r", exc)
        raise RuntimeError(
            f"python-pam is not usable ({exc}); cannot authenticate against PAM."
        ) from exc
    return pam.pam()


def authenticate(username: str, password: str) -> bool:
    """Return True when (username, password) is valid for the system.

    On a deployed appliance this delegates to the PAM ``muros`` service
    (pam_unix against /etc/shadow). On a dev box it accepts the default
    admin credentials only.
    """
    if not username or not password:
        return False

    if not APPLY_ENABLED:
        return username == _DEV_USER and password == _DEV_PASSWORD

    pam = _load_pam()
    try:
        ok = bool(pam.authenticate(username, password, service=PAM_SERVICE))
    except Exception as exc:  # noqa: BLE001
        log.warning("PAM authenticate raised for %s: %s", username, exc)
        return False
    if not ok:
        log.info(
            "PAM auth refused for %s (service=%s, reason=%s)",
            username, PAM_SERVICE, getattr(pam, "reason", "?"),
        )
    return ok


def account_exists(username: str) -> bool:
    """True when a local Linux account with this name exists."""
    try:
        import pwd
        pwd.getpwnam(username)
        return True
    except (KeyError, ImportError):
        return False


# Login shells that mean "this account can actually open a session".
# Daemon / service accounts use nologin or false and are filtered out so
# the Access > Users picker only offers real human accounts to grant.
_REAL_SHELLS_DENY = ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false")


def list_login_accounts() -> list[str]:
    """List local Linux accounts a human could log in with.

    Returns root plus every account with UID >= 1000 that has a real
    login shell. Service / daemon accounts (nologin, false) are skipped.
    Used by the Access > Users page so root can grant web UI access to
    an existing system account. On a dev box without a real passwd
    database this still returns at least the dev account.
    """
    names: list[str] = []
    try:
        import pwd
        for entry in pwd.getpwall():
            shell = (entry.pw_shell or "").strip()
            if shell in _REAL_SHELLS_DENY:
                continue
            if entry.pw_uid == 0 or entry.pw_uid >= 1000:
                names.append(entry.pw_name)
    except ImportError:
        pass
    if _DEV_USER not in names:
        names.append(_DEV_USER)
    # Stable, de-duplicated ordering for a predictable UI list.
    return sorted(set(names))


def set_password(username: str, new_password: str) -> dict:
    """Set the Linux password of ``username`` via chpasswd (root only).

    This is what the web UI calls when the operator changes their
    password: because the web account and the SSH account are the same
    Linux user, the new password applies to both at once.
    """
    if not APPLY_ENABLED:
        return {"applied": False, "message": "dry-run: MUROS_APPLY off, password unchanged."}

    if os.geteuid() != 0:
        raise RuntimeError("Cannot change password: MurOS must run as root.")

    proc = subprocess.run(
        ["chpasswd"],
        input=f"{username}:{new_password}\n",
        capture_output=True, text=True, timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"chpasswd failed (code {proc.returncode}): "
            f"{(proc.stderr or '').strip()[:200]}"
        )
    return {"applied": True, "message": f"Password updated for account '{username}'."}
