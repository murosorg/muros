# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Configuration du serveur SSH via drop-in /etc/ssh/sshd_config.d/muros.conf.

Le drop-in remplace les params du fichier principal (sshd_config). On ne
touche jamais a /etc/ssh/sshd_config directement, on genere uniquement
notre drop-in et on reload sshd. En cas de mauvaise config (sshd -t fail)
on rollback.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.ssh")

DROPIN_PATH = Path("/etc/ssh/sshd_config.d/muros.conf")

SSH_PACKAGES = ["openssh-server"]


def install_packages() -> dict:
    """Installe openssh-server via apt. Idempotent."""
    already = _which("sshd")
    if already:
        return {
            "installed": True,
            "already_present": SSH_PACKAGES,
            "newly_installed": [],
            "output_tail": "",
        }

    if not APPLY_ENABLED:
        return {
            "installed": False,
            "already_present": [],
            "newly_installed": [],
            "output_tail": (
                f"dry-run : aurait execute 'apt-get install -y {' '.join(SSH_PACKAGES)}'."
            ),
        }

    if os.geteuid() != 0:
        raise RuntimeError(
            "Installation impossible : MurOS doit tourner en root. "
            f"Installer manuellement : apt install -y {' '.join(SSH_PACKAGES)}"
        )

    try:
        subprocess.check_call(["which", "apt-get"], stdout=subprocess.DEVNULL, timeout=2)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError("apt-get not found, only supported on Debian/Ubuntu.") from exc

    env = {**os.environ, "DEBIAN_FRONTEND": "noninteractive", "LC_ALL": "C"}
    proc_update = subprocess.run(
        ["apt-get", "update", "-q"],
        env=env, capture_output=True, text=True, timeout=120,
    )
    if proc_update.returncode != 0:
        raise RuntimeError(f"apt-get update a echoue : {(proc_update.stderr or '').strip()[:400]}")

    proc = subprocess.run(
        ["apt-get", "install", "-y", "--no-install-recommends", *SSH_PACKAGES],
        env=env, capture_output=True, text=True, timeout=300,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"apt-get install a echoue (code {proc.returncode}) : "
            f"{(proc.stderr or '').strip()[:400]}"
        )

    if not _which("sshd"):
        raise RuntimeError(f"sshd missing after install. Output: {proc.stdout[-400:]}")

    # Active + demarre le service
    subprocess.run(
        ["systemctl", "enable", "--now", "ssh.service"],
        capture_output=True, text=True, timeout=15,
    )

    return {
        "installed": True,
        "already_present": [],
        "newly_installed": SSH_PACKAGES,
        "output_tail": proc.stdout[-800:],
    }


from app.service_state import is_active as _systemd_active, which as _which  # noqa: E402


def _sshd_version() -> str | None:
    """Version OpenSSH via dpkg."""
    from app.service_state import pkg_version
    return pkg_version("openssh-server", "OpenSSH")


def get_status(admin_disabled: bool = False) -> dict:
    """Etat live de sshd (installe, actif, version, drop-in present).

    `admin_disabled` is the persisted intent (operator clicked the
    'disable SSH' toggle), surfaced to the UI so the Monitoring page
    can render 'disabled by admin' rather than a red alert.
    """
    return {
        "sshd_installed": _which("sshd"),
        "service_active": _systemd_active("ssh") or _systemd_active("sshd"),
        "version": _sshd_version(),
        "dropin_present": DROPIN_PATH.exists(),
        "dropin_path": str(DROPIN_PATH),
        "admin_disabled": admin_disabled,
    }


def set_service_enabled(enabled: bool) -> dict:
    """Enable + start, or disable + stop, the sshd unit.

    Mirrors how operators run `systemctl enable --now ssh` /
    `systemctl disable --now ssh` so the change is persistent across
    reboots. Returns a small result dict {applied, active, message}.
    """
    import subprocess
    from app.apply import APPLY_ENABLED

    if not APPLY_ENABLED:
        return {
            "applied": False,
            "active": _systemd_active("ssh") or _systemd_active("sshd"),
            "message": "dry-run: MUROS_APPLY off, no systemctl call",
        }

    unit = "ssh.service" if _which("sshd") else "sshd.service"
    action = "enable" if enabled else "disable"
    try:
        subprocess.check_output(
            ["systemctl", action, "--now", unit],
            stderr=subprocess.STDOUT, text=True, timeout=15,
        )
    except subprocess.CalledProcessError as exc:
        # Best-effort: surface the stderr tail so the UI can show it.
        raise RuntimeError(
            f"systemctl {action} --now {unit} failed: {exc.output[-500:]}"
        ) from exc
    except FileNotFoundError as exc:
        raise RuntimeError(f"systemctl not available: {exc}") from exc

    return {
        "applied": True,
        "active": _systemd_active("ssh") or _systemd_active("sshd"),
        "message": f"sshd {'started' if enabled else 'stopped'} and {'enabled' if enabled else 'disabled'} at boot",
    }


def render_dropin(cfg) -> str:
    """Render /etc/ssh/sshd_config.d/muros.conf from the DB config.

    The drop-in always exists on a MurOS appliance; the operator only
    chooses how sshd behaves through the UI (port, listen address, root
    login policy, auth methods, keepalive). The drop-in cannot be
    disabled from the UI: falling back to Debian defaults silently
    would re-enable root password login on port 22 which is not a
    state we want to encourage.
    """
    listen = (cfg.listen_address or "0.0.0.0").strip()
    permit_root = (getattr(cfg, "permit_root_login", None) or "prohibit-password").strip()
    pw_auth = "yes" if getattr(cfg, "password_authentication", False) else "no"
    pk_auth = "yes" if getattr(cfg, "pubkey_authentication", True) else "no"
    lines = [
        "# Generated by MurOS - do not edit by hand.",
        "# Change settings from the web UI -> SSH access.",
        "",
        f"Port {cfg.port}",
    ]
    if listen and listen != "0.0.0.0":
        lines.append(f"ListenAddress {listen}")
    lines.extend([
        f"PermitRootLogin {permit_root}",
        f"PasswordAuthentication {pw_auth}",
        f"PubkeyAuthentication {pk_auth}",
        f"MaxAuthTries {cfg.max_auth_tries}",
        f"ClientAliveInterval {cfg.client_alive_interval}",
        f"ClientAliveCountMax {cfg.client_alive_count_max}",
        # Default hardening (always written by MurOS)
        "X11Forwarding no",
        "PermitEmptyPasswords no",
        "UsePAM yes",
        "KbdInteractiveAuthentication no",
        "AllowAgentForwarding no",
        "AllowTcpForwarding no",
    ])
    return "\n".join(lines) + "\n"


# --- authorized_keys management for the administrator account ---
#
# The web UI and SSH share the same Linux account, and the default
# administrator is 'root'. We manage that account's keys in its own home
# directory (/root/.ssh/authorized_keys for root, /home/<user>/.ssh for
# any other login). With root as the administrator, PermitRootLogin must
# allow key-based login; MurOS defaults it to 'prohibit-password' so root
# can connect over SSH with a key but never with a password.

ADMIN_USER = os.environ.get("MUROS_ADMIN_USER", "root")


def _home_dir(user: str) -> str:
    """Return the home directory of a login account.

    root's home is /root, not /home/root. We resolve it from the passwd
    database when available and fall back to the conventional layout on a
    dev box that has no such account.
    """
    try:
        import pwd
        return pwd.getpwnam(user).pw_dir or ("/root" if user == "root" else f"/home/{user}")
    except (KeyError, ImportError):
        return "/root" if user == "root" else f"/home/{user}"


AUTHORIZED_KEYS_PATH = os.environ.get(
    "MUROS_AUTHORIZED_KEYS", f"{_home_dir(ADMIN_USER)}/.ssh/authorized_keys"
)


def _chown_admin(path: str) -> None:
    """Best-effort chown of a path to the administrator login account.

    sshd's StrictModes refuses authorized_keys not owned by the login
    user. The backend writes as root, so we hand ownership back to the
    administrator account. Silently ignored when the account does not
    exist (dev box) or on permission errors.
    """
    try:
        import pwd
        ent = pwd.getpwnam(ADMIN_USER)
        os.chown(path, ent.pw_uid, ent.pw_gid)
    except (KeyError, ImportError, OSError):
        pass

# Supported algorithms (order = preference)
SSH_KEY_TYPES = (
    "ssh-rsa", "ssh-ed25519", "ssh-dss",
    "ecdsa-sha2-nistp256", "ecdsa-sha2-nistp384", "ecdsa-sha2-nistp521",
    "sk-ecdsa-sha2-nistp256@openssh.com", "sk-ssh-ed25519@openssh.com",
)


def _parse_authorized_key_line(line: str) -> dict | None:
    """Parse an authorized_keys line: return {type, key_b64, comment} or None."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # Format: <options>? <type> <base64-key> <comment>
    # For simplicity, options (cmd=, restrict, etc.) are not supported.
    parts = line.split(None, 2)
    if len(parts) < 2:
        return None
    key_type, key_b64 = parts[0], parts[1]
    comment = parts[2] if len(parts) == 3 else ""
    if key_type not in SSH_KEY_TYPES:
        return None
    # Rough base64 validation
    import base64
    try:
        base64.b64decode(key_b64, validate=True)
    except Exception:  # noqa: BLE001
        return None
    return {
        "type": key_type,
        "key_b64": key_b64,
        "comment": comment,
        "fingerprint": _compute_fingerprint(key_type, key_b64),
    }


def _compute_fingerprint(key_type: str, key_b64: str) -> str:
    """Compute the base64 SHA256 fingerprint of the key (ssh-keygen -lf format)."""
    import base64
    import hashlib
    try:
        raw = base64.b64decode(key_b64, validate=True)
    except Exception:  # noqa: BLE001
        return ""
    h = hashlib.sha256(raw).digest()
    b64 = base64.b64encode(h).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


def list_authorized_keys() -> list[dict]:
    """Retourne la liste des cles autorisees pour root."""
    p = AUTHORIZED_KEYS_PATH
    if not os.path.exists(p):
        return []
    keys: list[dict] = []
    try:
        with open(p, encoding="utf-8") as f:
            for i, line in enumerate(f, start=1):
                parsed = _parse_authorized_key_line(line)
                if parsed:
                    parsed["line"] = i
                    keys.append(parsed)
    except OSError as exc:
        log.warning("Lecture %s : %s", p, exc)
    return keys


def add_authorized_key(key_text: str) -> dict:
    """Ajoute une cle a authorized_keys. Refuse les doublons.

    key_text : ligne complete '<type> <base64> [comment]'
    """
    parsed = _parse_authorized_key_line(key_text)
    if parsed is None:
        raise ValueError(
            "Invalid key format. Expected : 'ssh-ed25519 AAAA... comment' "
            "ou 'ssh-rsa AAAA... comment'."
        )

    if not APPLY_ENABLED:
        return {"added": False, "message": "dry-run : MUROS_APPLY off."}
    if os.geteuid() != 0:
        raise RuntimeError("Cannot write SSH keys: MurOS must run as root.")

    existing = list_authorized_keys()
    for k in existing:
        if k["key_b64"] == parsed["key_b64"]:
            raise ValueError(f"This key is already authorized (comment: {k['comment'] or '-'}).")

    ssh_dir = os.path.dirname(AUTHORIZED_KEYS_PATH)
    os.makedirs(ssh_dir, exist_ok=True)
    os.chmod(ssh_dir, 0o700)

    line = f"{parsed['type']} {parsed['key_b64']} {parsed['comment']}\n".strip() + "\n"
    with open(AUTHORIZED_KEYS_PATH, "a", encoding="utf-8") as f:
        f.write(line)
    os.chmod(AUTHORIZED_KEYS_PATH, 0o600)
    # sshd enforces StrictModes: the .ssh dir and authorized_keys must be
    # owned by the login user. The backend runs as root, so chown back to
    # the admin account after writing.
    _chown_admin(ssh_dir)
    _chown_admin(AUTHORIZED_KEYS_PATH)

    return {"added": True, "fingerprint": parsed["fingerprint"]}


def delete_authorized_key(key_b64: str) -> dict:
    """Supprime une cle par sa partie base64 (identifiant unique)."""
    if not APPLY_ENABLED:
        return {"deleted": False, "message": "dry-run : MUROS_APPLY off."}
    if os.geteuid() != 0:
        raise RuntimeError("Cannot write SSH keys: MurOS must run as root.")

    if not os.path.exists(AUTHORIZED_KEYS_PATH):
        raise ValueError("Aucun fichier authorized_keys.")

    new_lines: list[str] = []
    deleted = False
    with open(AUTHORIZED_KEYS_PATH, encoding="utf-8") as f:
        for line in f:
            parsed = _parse_authorized_key_line(line)
            if parsed and parsed["key_b64"] == key_b64:
                deleted = True
                continue
            new_lines.append(line)

    if not deleted:
        raise ValueError("Key not found in authorized_keys.")

    # Ecriture atomique
    tmp = AUTHORIZED_KEYS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    os.chmod(tmp, 0o600)
    os.replace(tmp, AUTHORIZED_KEYS_PATH)
    _chown_admin(AUTHORIZED_KEYS_PATH)
    return {"deleted": True}


def apply_config(cfg) -> dict:
    """Ecrit le drop-in et reload sshd.

    Si cfg.enabled=False : supprime le drop-in (retour defauts Debian).
    Si sshd -t fail : rollback.
    """
    if not APPLY_ENABLED:
        return {
            "applied": False,
            "message": "dry-run : MUROS_APPLY off.",
            "preview": render_dropin(cfg),
        }

    if os.geteuid() != 0:
        raise RuntimeError("Cannot write SSH config: MurOS must run as root.")

    # Backup de l'ancien drop-in pour rollback en cas d'echec sshd -t.
    backup_path: Path | None = None
    if DROPIN_PATH.exists():
        backup_path = DROPIN_PATH.with_suffix(".conf.bak")
        shutil.copy2(DROPIN_PATH, backup_path)

    DROPIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    DROPIN_PATH.write_text(render_dropin(cfg), encoding="utf-8")
    os.chmod(DROPIN_PATH, 0o644)

    # Verif syntaxe via sshd -t.
    check = subprocess.run(
        ["sshd", "-t"], capture_output=True, text=True, timeout=10,
    )
    if check.returncode != 0:
        # Rollback
        if backup_path and backup_path.exists():
            shutil.copy2(backup_path, DROPIN_PATH)
        else:
            try:
                DROPIN_PATH.unlink()
            except OSError:
                pass
        raise RuntimeError(
            f"sshd -t a refuse la config : {check.stderr.strip()[:400]}"
        )

    if backup_path and backup_path.exists():
        backup_path.unlink()

    _reload_sshd()
    listen_msg = f" sur {cfg.listen_address}" if cfg.listen_address and cfg.listen_address != "0.0.0.0" else ""
    return {
        "applied": True,
        "message": f"SSH reconfigure (port {cfg.port}{listen_msg}). "
                   "Verifier la session courante avant de fermer cette fenetre.",
    }


def _reload_sshd() -> None:
    """Reload sshd via systemctl. Sur Debian/Ubuntu le service est 'ssh'."""
    for unit in ("ssh", "sshd"):
        r = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode == 0 and r.stdout.strip() == "active":
            subprocess.run(
                ["systemctl", "reload", unit],
                capture_output=True, text=True, timeout=10,
            )
            return
