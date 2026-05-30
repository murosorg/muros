"""Helpers unifies pour les interactions systemd / PATH.

Tous les modules (HA, VPN, SNMP, SSH, etc.) renvoient maintenant un
`service_state` parmi : "active", "inactive", "failed", "unknown".
L'UI tri-state s'appuie dessus pour colorer (vert / gris / rouge).

Expose aussi `which()` et `is_active()` pour eviter la duplication
qu'on avait dans 9 modules (chacun redefinissait son `_which` et son
`_systemd_active`).
"""
from __future__ import annotations

import shutil
import subprocess
from typing import Literal

ServiceState = Literal["active", "inactive", "failed", "unknown"]


def which(cmd: str) -> bool:
    """True si `cmd` est trouve dans le PATH. Wrapper trivial de shutil.which
    qui renvoie un bool plutot qu'un chemin pour utilisation en `if which(...)`."""
    return shutil.which(cmd) is not None


def is_active(unit: str) -> bool:
    """True if `systemctl is-active <unit>` returns 'active'.

    False in all other cases (inactive, failed, unknown, systemctl
    missing). For the detailed state, use `service_state()`.
    """
    return service_state(unit) == "active"


def pkg_version(package: str, label: str | None = None) -> str | None:
    """Return the installed version of a Debian package, or None.

    Single source of truth for every version displayed in the MurOS UI.
    We prefer dpkg-query over `<binary> --version` because:
      - it is instant (no binary fork),
      - it works even if the binary prints plugin warnings on stderr or
        exits with a non-zero code (real case with swanctl on Debian),
      - it is the REAL installed version, identical to what apt sees.

    `label` is the human prefix (e.g. "strongSwan"). If not provided, the
    package name is used.
    """
    if not which("dpkg-query"):
        return None
    try:
        r = subprocess.run(
            ["dpkg-query", "-W", "-f=${Status}\\t${Version}", package],
            capture_output=True, text=True, timeout=2,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    if r.returncode != 0:
        return None
    line = (r.stdout or "").strip()
    if "\t" not in line:
        return None
    status, version = line.split("\t", 1)
    # We require the package to be really installed (not just known to
    # dpkg, e.g. "config-files" after remove without purge).
    if not status.startswith("install ok installed"):
        return None
    version = version.strip()
    if not version:
        return None
    return f"{label or package} {version}"


def service_state(unit: str) -> ServiceState:
    """Retourne l'etat actuel d'un unit systemd.

    On normalise les sorties exotiques (`activating`, `deactivating`,
    `reloading`) vers `active` parce que pour l'admin c'est en cours
    d'etre OK. `not-found` et erreurs runtime -> `unknown`.
    """
    if not shutil.which("systemctl"):
        return "unknown"
    try:
        out = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=3,
        )
    except Exception:  # noqa: BLE001
        return "unknown"

    raw = (out.stdout.strip() or out.stderr.strip()).lower()
    if raw in ("active", "activating", "reloading"):
        return "active"
    if raw in ("inactive", "deactivating"):
        return "inactive"
    if raw == "failed":
        return "failed"
    return "unknown"
