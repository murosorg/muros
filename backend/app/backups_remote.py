"""Remote backup: push tar.gz snapshots to a server via rsync/SSH.

Configuration persisted in `MUROS_BACKUP_DIR/remote.json`. This is
deliberately out of the DB to avoid a migration and keep the secret (the
SSH key) out of the SQLite backups.

Model:
- enabled       : bool, enables the push or not
- host          : DNS name or IP of the destination server
- user          : SSH user (often 'muros-backup' or 'backup')
- port          : SSH port (default 22)
- path          : absolute remote path, e.g. `/srv/backups/firewall-01`
- ssh_key_path  : local path to the private key (default /var/lib/muros/ssh/id_ed25519)
- last_push_at  : ISO timestamp of the last successful push
- last_error    : error message of the last failure

The admin must place the matching public key on the remote server
manually. MurOS does not generate the key (sensitive point: we prefer the
admin to verify the fingerprint).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.apply import APPLY_ENABLED
from app.backups import BACKUP_DIR

CONFIG_PATH = BACKUP_DIR / "remote.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "host": "",
    "user": "",
    "port": 22,
    "path": "",
    "ssh_key_path": "/var/lib/muros/ssh/id_ed25519",
    "last_push_at": None,
    "last_error": None,
}

# Host: letters, digits, dot, dash. No hidden SSH option.
_VALID_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\.\-]{0,253}$")
# User: letters, digits, underscore, dash, dot. No @ or space.
_VALID_USER = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-\.]{0,31}$")


def _ensure_dir() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def get_config() -> dict[str, Any]:
    """Load the config. Return the defaults if there is no file."""
    _ensure_dir()
    if not CONFIG_PATH.is_file():
        return dict(DEFAULT_CONFIG)
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG)
    # Merge avec defauts pour gerer les ajouts de champs futurs
    cfg = dict(DEFAULT_CONFIG)
    cfg.update({k: v for k, v in data.items() if k in DEFAULT_CONFIG})
    return cfg


def _save(cfg: dict[str, Any]) -> None:
    try:
        _ensure_dir()
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except PermissionError as exc:
        raise RuntimeError(
            f"unable to write {CONFIG_PATH}: {exc}. Check the permissions "
            f"on {BACKUP_DIR} (in dev, export MUROS_BACKUP_DIR=/tmp/muros)."
        ) from exc
    # 0600: the config references an SSH key, we protect it.
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass


def set_config(data: dict[str, Any]) -> dict[str, Any]:
    """Valide et persiste la config. N'efface jamais last_push_at / last_error
    (gestion interne)."""
    cfg = get_config()
    if "enabled" in data:
        cfg["enabled"] = bool(data["enabled"])
    if "host" in data:
        host = (data["host"] or "").strip()
        if host and not _VALID_HOST.match(host):
            raise ValueError(f"invalid host : {host!r}")
        cfg["host"] = host
    if "user" in data:
        user = (data["user"] or "").strip()
        if user and not _VALID_USER.match(user):
            raise ValueError(f"invalid user : {user!r}")
        cfg["user"] = user
    if "port" in data:
        try:
            port = int(data["port"])
        except (TypeError, ValueError) as exc:
            raise ValueError("port must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("port hors plage 1-65535")
        cfg["port"] = port
    if "path" in data:
        path = (data["path"] or "").strip()
        if path and (not path.startswith("/") or ".." in path):
            raise ValueError("remote path must be absolute and without '..'")
        cfg["path"] = path
    if "ssh_key_path" in data:
        keypath = (data["ssh_key_path"] or "").strip()
        cfg["ssh_key_path"] = keypath or DEFAULT_CONFIG["ssh_key_path"]
    if cfg["enabled"]:
        # Si on active, les champs critiques doivent etre renseignes
        for required in ("host", "user", "path"):
            if not cfg[required]:
                raise ValueError(f"missing required field : {required}")
    _save(cfg)
    return cfg


def _resolve_backup(name: str) -> Path:
    """Securise le nom de backup (pas de slash, pas de ..)."""
    if "/" in name or ".." in name or not name.endswith(".tar.gz"):
        raise ValueError(f"invalid backup name : {name!r}")
    path = BACKUP_DIR / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def _build_rsync_cmd(cfg: dict[str, Any], src: Path) -> list[str]:
    """Build the rsync command with a custom SSH (port + key)."""
    ssh_parts = [
        "ssh",
        "-p", str(cfg["port"]),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    if cfg.get("ssh_key_path"):
        ssh_parts.extend(["-i", cfg["ssh_key_path"]])
    rsync_ssh = " ".join(shlex.quote(p) for p in ssh_parts)
    remote = f"{cfg['user']}@{cfg['host']}:{cfg['path']}/"
    return [
        "rsync",
        "-av",
        "--mkpath",  # creates the remote directory if missing (rsync >= 3.2)
        "--timeout=30",
        "-e", rsync_ssh,
        str(src),
        remote,
    ]


def push_backup(name: str) -> dict:
    """Send a snapshot to the remote target via rsync."""
    cfg = get_config()
    if not cfg["enabled"]:
        raise RuntimeError("remote backup disabled")
    if not all(cfg[k] for k in ("host", "user", "path")):
        raise RuntimeError("incomplete config (host, user and path required)")

    src = _resolve_backup(name)

    if not APPLY_ENABLED:
        cmd = _build_rsync_cmd(cfg, src)
        return {
            "pushed": False,
            "dry_run": True,
            "message": "MUROS_APPLY is not enabled",
            "command": " ".join(shlex.quote(p) for p in cmd),
        }

    cmd = _build_rsync_cmd(cfg, src)
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired as exc:
        cfg["last_error"] = "timeout (600s) depasse"
        _save(cfg)
        raise RuntimeError("rsync : timeout") from exc
    except FileNotFoundError as exc:
        cfg["last_error"] = "rsync non installe"
        _save(cfg)
        raise RuntimeError("rsync non installe sur le firewall") from exc

    if res.returncode == 0:
        cfg["last_push_at"] = datetime.now(timezone.utc).isoformat()
        cfg["last_error"] = None
        _save(cfg)
        return {
            "pushed": True,
            "dry_run": False,
            "message": "ok",
            "output_tail": (res.stdout or "").splitlines()[-5:],
        }
    err = (res.stderr or res.stdout or "").strip().splitlines()[-3:]
    cfg["last_error"] = " | ".join(err) or f"rsync exit {res.returncode}"
    _save(cfg)
    raise RuntimeError(cfg["last_error"])


def generate_ssh_key(force: bool = False) -> dict:
    """Generate an ed25519 SSH key pair at the configured path (default
    /var/lib/muros/ssh/id_ed25519).

    Returns the public key so the admin can copy it onto the remote server.
    Refuses to regenerate if a key already exists, unless force=True.
    """
    cfg = get_config()
    key_path = Path(cfg.get("ssh_key_path") or DEFAULT_CONFIG["ssh_key_path"])
    pub_path = key_path.with_suffix(key_path.suffix + ".pub") if key_path.suffix else Path(str(key_path) + ".pub")

    if key_path.exists() and not force:
        # Return the existing public key, the admin most likely wants it
        pub = ""
        try:
            pub = pub_path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        return {
            "generated": False,
            "dry_run": False,
            "message": "A key already exists. Use force=true to regenerate.",
            "key_path": str(key_path),
            "public_key": pub,
        }

    # Note: we do NOT gate the generation on MUROS_APPLY.
    # ssh-keygen does not touch the kernel, it is just a file in a dedicated
    # directory. The admin needs to be able to generate the key even
    # in dry-run mode to prepare it before going to prod.

    # Create the parent directory with 0700 (the private key must be protected)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"unable to create {key_path.parent}: {exc}. "
            f"Change the key path in the config ('ssh_key_path' field) "
            f"or run the backend with sufficient privileges."
        ) from exc
    try:
        os.chmod(key_path.parent, 0o700)
    except OSError:
        pass

    if force and key_path.exists():
        try:
            key_path.unlink()
            if pub_path.exists():
                pub_path.unlink()
        except OSError:
            pass

    # ed25519 = short, fast, secure. Comment = hostname for identification.
    import socket
    comment = f"muros@{socket.gethostname()}"
    try:
        res = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", comment],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError(f"ssh-keygen failed: {exc}") from exc
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"ssh-keygen exit {res.returncode}")

    # Read the public key to return it to the UI
    try:
        pub = pub_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"unable to read {pub_path}: {exc}") from exc

    return {
        "generated": True,
        "dry_run": False,
        "message": "ed25519 key generated. Add the public key on the remote server.",
        "key_path": str(key_path),
        "public_key": pub,
    }


def get_public_key() -> dict:
    """Return the public key if it already exists, without regenerating it."""
    cfg = get_config()
    key_path = Path(cfg.get("ssh_key_path") or DEFAULT_CONFIG["ssh_key_path"])
    pub_path = Path(str(key_path) + ".pub")
    if not pub_path.is_file():
        return {"exists": False, "key_path": str(key_path), "public_key": ""}
    try:
        pub = pub_path.read_text(encoding="utf-8").strip()
    except OSError:
        return {"exists": False, "key_path": str(key_path), "public_key": ""}
    return {"exists": True, "key_path": str(key_path), "public_key": pub}


def test_connection(override: dict | None = None) -> dict:
    """Check that the target is reachable. Runs `ssh user@host true`.

    If `override` is provided, we test with those values instead of the
    persisted config. Lets the UI test before Save.
    """
    cfg = get_config()
    if override:
        # Surface merge: we do not write the config, we just test the new values
        for k in ("host", "user", "port", "path", "ssh_key_path"):
            if k in override and override[k] is not None and override[k] != "":
                cfg[k] = override[k]
    if not all(cfg.get(k) for k in ("host", "user")):
        return {
            "ok": False,
            "dry_run": False,
            "message": "Fill in the host and the user before testing.",
        }
    if not APPLY_ENABLED:
        return {
            "ok": False,
            "dry_run": True,
            "message": (
                f"Dry-run mode (MUROS_APPLY disabled). In production, we would try "
                f"ssh -p {cfg['port']} {cfg['user']}@{cfg['host']} true"
            ),
        }
    cmd = [
        "ssh",
        "-p", str(cfg["port"]),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    if cfg.get("ssh_key_path"):
        cmd.extend(["-i", cfg["ssh_key_path"]])
    cmd.extend([f"{cfg['user']}@{cfg['host']}", "true"])
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        return {"ok": False, "dry_run": False, "message": str(exc)}
    if res.returncode == 0:
        return {"ok": True, "dry_run": False, "message": "Connexion SSH OK"}
    return {
        "ok": False,
        "dry_run": False,
        "message": (res.stderr or res.stdout or f"exit {res.returncode}").strip(),
    }
