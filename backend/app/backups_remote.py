"""Backup distant : pousse les snapshots tar.gz vers un serveur via rsync/SSH.

Configuration persistee dans `MUROS_BACKUP_DIR/remote.json`. C'est volontaire-
ment hors DB pour eviter une migration et garder le secret (la cle SSH) hors
sauvegardes SQLite.

Modele :
- enabled       : bool, active ou non l'envoi
- host          : nom DNS ou IP du serveur de destination
- user          : utilisateur SSH (souvent 'muros-backup' ou 'backup')
- port          : port SSH (defaut 22)
- path          : chemin distant absolu, ex `/srv/backups/firewall-01`
- ssh_key_path  : chemin local vers la cle privee (defaut /var/lib/muros/ssh/id_ed25519)
- last_push_at  : ISO timestamp du dernier push reussi
- last_error    : message d'erreur du dernier echec

L'admin doit deposer la cle publique correspondante sur le serveur distant
manuellement. MurOS ne genere pas la cle (point sensible : on prefere que
l'admin verifie l'empreinte).
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

# Hote : lettres, chiffres, point, tiret. Pas d'option SSH dissimulee.
_VALID_HOST = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\.\-]{0,253}$")
# User : lettres, chiffres, underscore, tiret, point. Pas de @ ni espace.
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
            f"impossible d'ecrire {CONFIG_PATH} : {exc}. Verifier les droits "
            f"sur {BACKUP_DIR} (en dev, exporter MUROS_BACKUP_DIR=/tmp/muros)."
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
        "--mkpath",  # cree le dossier distant s'il manque (rsync >= 3.2)
        "--timeout=30",
        "-e", rsync_ssh,
        str(src),
        remote,
    ]


def push_backup(name: str) -> dict:
    """Envoie un snapshot vers la cible distante via rsync."""
    cfg = get_config()
    if not cfg["enabled"]:
        raise RuntimeError("backup distant desactive")
    if not all(cfg[k] for k in ("host", "user", "path")):
        raise RuntimeError("incomplete config (host, user and path required)")

    src = _resolve_backup(name)

    if not APPLY_ENABLED:
        cmd = _build_rsync_cmd(cfg, src)
        return {
            "pushed": False,
            "dry_run": True,
            "message": "MUROS_APPLY n'est pas active",
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
    """Genere une paire SSH ed25519 au chemin configure (defaut
    /var/lib/muros/ssh/id_ed25519).

    Retourne la cle publique pour que l'admin la copie sur le serveur distant.
    Refuse de regenerer si une cle existe deja, sauf si force=True.
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

    # Note : on ne gate PAS la generation par MUROS_APPLY.
    # ssh-keygen does not touch the kernel, it is just a file in a dedicated
    # directory. The admin needs to be able to generate the key even
    # en mode dry-run pour la preparer avant le passage en prod.

    # Create the parent directory with 0700 (the private key must be protected)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
    except PermissionError as exc:
        raise RuntimeError(
            f"impossible de creer {key_path.parent} : {exc}. "
            f"Changer le chemin de la cle dans la config (champ 'ssh_key_path') "
            f"ou lancer le backend avec les droits suffisants."
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

    # ed25519 = court, rapide, sur. Comment = hostname pour reperage.
    import socket
    comment = f"muros@{socket.gethostname()}"
    try:
        res = subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", comment],
            capture_output=True, text=True, timeout=20,
        )
    except (subprocess.SubprocessError, FileNotFoundError) as exc:
        raise RuntimeError(f"ssh-keygen a echoue : {exc}") from exc
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"ssh-keygen exit {res.returncode}")

    # Read the public key to return it to the UI
    try:
        pub = pub_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(f"unable to read {pub_path} : {exc}") from exc

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
    """Verifie qu'on peut joindre la cible. Lance `ssh user@host true`.

    Si `override` est fourni, on teste avec ces valeurs au lieu de la conf
    persistee. Permet a l'UI de tester avant Enregistrer.
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
            "message": "Renseignez l'hote et l'utilisateur avant de tester.",
        }
    if not APPLY_ENABLED:
        return {
            "ok": False,
            "dry_run": True,
            "message": (
                f"Mode dry-run (MUROS_APPLY desactive). En production, on tenterait "
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
