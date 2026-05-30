"""Synchronisation de la configuration entre les 2 noeuds HA.

Pattern inspire d'OPNsense : le MASTER pousse sa DB sqlite vers le BACKUP
apres chaque apply (mode auto) ou sur action manuelle (mode manual).

Le BACKUP recoit la DB via POST /api/ha/sync/receive, valide le token,
fait une copie de sa DB locale dans /var/lib/muros/backups/pre-sync-*.db,
remplace la DB et applique la conf (muros_boot.py rejoue tout).

Le role VRRP est determine via parse de `ip addr show` (presence d'une
VIP MurOS) ou via le fichier d'etat ecrit par le hook keepalived
(packaging/usr/lib/muros/ha-notify.sh).
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets as py_secrets
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app import models
from app.apply import APPLY_ENABLED

log = logging.getLogger("muros.ha_sync")

# Resolution du chemin de la DB sqlite. On suit la convention du module db.
DEFAULT_DB_PATH = "/var/lib/muros/muros.db"
BACKUP_DIR = Path("/var/lib/muros/backups")
VRRP_STATE_FILE = Path("/run/muros/vrrp-state")


def _get_db_path() -> Path:
    """Retourne le chemin absolu de la DB sqlite."""
    p = os.environ.get("MUROS_DB", DEFAULT_DB_PATH)
    return Path(p)


def generate_token() -> str:
    """Genere un token de sync long (64 chars hex = 32 octets)."""
    return py_secrets.token_hex(32)


def get_vrrp_role() -> str:
    """Retourne le role VRRP actuel : MASTER, BACKUP, FAULT, STANDALONE.

    STANDALONE = keepalived n'est pas configure (pas de HA).
    """
    # Methode 1 : fichier ecrit par le hook keepalived.
    if VRRP_STATE_FILE.exists():
        try:
            content = VRRP_STATE_FILE.read_text(encoding="utf-8").strip()
            # Format attendu : "<instance> <state>" ou juste "<state>"
            parts = content.split()
            if parts:
                state = parts[-1].upper()
                if state in ("MASTER", "BACKUP", "FAULT"):
                    return state
        except OSError:
            pass

    # Methode 2 : parse de la sortie keepalived via journalctl (heuristique).
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "keepalived"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0 or r.stdout.strip() != "active":
            return "STANDALONE"
    except (subprocess.SubprocessError, FileNotFoundError):
        return "STANDALONE"

    # keepalived tourne mais pas de fichier d'etat : suppose MASTER par defaut
    # (le hook n'a pas encore declenche).
    return "MASTER"


def is_writable_role() -> bool:
    """True si le noeud peut accepter des ecritures (MASTER ou STANDALONE)."""
    role = get_vrrp_role()
    return role in ("MASTER", "STANDALONE")


# --- Lecture de la conf de sync ---

def get_config(db: Session) -> models.HaSyncConfig:
    cfg = db.get(models.HaSyncConfig, 1)
    if cfg is None:
        cfg = models.HaSyncConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


# --- Push : envoi de la DB vers le peer ---

def _read_db_bytes() -> bytes:
    """Lit le contenu de la DB sqlite en bytes.

    On utilise PRAGMA wal_checkpoint avant pour eviter de pousser une DB
    avec des donnees dans le WAL qui ne seraient pas dans le fichier .db.
    """
    db_path = _get_db_path()
    if not db_path.exists():
        raise RuntimeError(f"DB not found : {db_path}")

    # Checkpoint WAL pour avoir une DB complete dans le fichier principal.
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("WAL checkpoint failed : %s", exc)

    return db_path.read_bytes()


def _http_post(url: str, headers: dict, body: bytes, verify_tls: bool, timeout: int = 30) -> tuple[int, bytes]:
    """POST HTTP minimaliste via urllib (pas de dep externe)."""
    import ssl
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except urllib.error.URLError as e:
        raise RuntimeError(f"Peer connection failed : {e.reason}") from e


def _http_get(url: str, headers: dict, verify_tls: bool, timeout: int = 10) -> tuple[int, bytes]:
    import ssl
    ctx = ssl.create_default_context()
    if not verify_tls:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except urllib.error.URLError as e:
        raise RuntimeError(f"Peer connection failed : {e.reason}") from e


def test_connection(cfg: models.HaSyncConfig) -> dict:
    """Ping le peer via GET /api/ha/sync/ping. Renvoie le role et la version peer."""
    if not cfg.peer_url or not cfg.peer_token:
        raise RuntimeError("Peer URL or token missing.")
    url = cfg.peer_url.rstrip("/") + "/api/ha/sync/ping"
    headers = {"X-Muros-Sync-Token": cfg.peer_token}
    status, body = _http_get(url, headers, cfg.verify_tls, timeout=5)
    if status != 200:
        raise RuntimeError(f"Peer a repondu {status} : {body.decode('utf-8', errors='replace')[:200]}")
    import json
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise RuntimeError("Invalid peer response (JSON expected).")
    return data


def push_to_peer(db: Session, cfg: models.HaSyncConfig, triggered_by: str = "manual") -> dict:
    """Pousse la DB sqlite courante vers le peer.

    Cree un HaSyncLog avec succes/echec.
    """
    if not cfg.enabled:
        raise RuntimeError("Synchronisation HA desactivee.")
    if not cfg.peer_url or not cfg.peer_token:
        raise RuntimeError("Peer URL or token missing.")
    if not is_writable_role():
        raise RuntimeError("Ce noeud n'est pas MASTER, push refuse.")

    started = time.time()
    db_bytes = b""
    error: str | None = None

    try:
        db_bytes = _read_db_bytes()
        # Calcul HMAC-SHA256 du contenu avec le token comme cle.
        sig = hmac.new(
            cfg.peer_token.encode("utf-8"), db_bytes, hashlib.sha256,
        ).hexdigest()

        url = cfg.peer_url.rstrip("/") + "/api/ha/sync/receive"
        headers = {
            "X-Muros-Sync-Token": cfg.peer_token,
            "X-Muros-Sync-Signature": sig,
            "Content-Type": "application/octet-stream",
        }
        status, body = _http_post(
            url, headers, db_bytes, cfg.verify_tls, timeout=60,
        )
        if status != 200:
            raise RuntimeError(
                f"Peer a repondu {status} : {body.decode('utf-8', errors='replace')[:300]}"
            )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:500]
        log.warning("HA sync push echec : %s", exc)

    duration_ms = int((time.time() - started) * 1000)
    entry = models.HaSyncLog(
        direction="push",
        success=(error is None),
        error=error,
        duration_ms=duration_ms,
        db_size_bytes=len(db_bytes),
        triggered_by=triggered_by,
    )
    db.add(entry)
    db.commit()
    _rotate_log(db, keep=50)

    if error:
        raise RuntimeError(error)

    return {
        "success": True,
        "duration_ms": duration_ms,
        "db_size_bytes": len(db_bytes),
    }


# --- Receive : reception de la DB depuis le peer ---

def receive_from_peer(cfg: models.HaSyncConfig, signature: str, body: bytes) -> dict:
    """Recoit une DB sqlite du peer.

    Verifie la signature HMAC, fait un backup local, ecrit la nouvelle DB.
    Le service backend doit etre redemarre apres (caller responsability).
    """
    if not cfg.enabled:
        raise RuntimeError("Synchronisation HA desactivee sur ce noeud.")
    if not cfg.peer_token:
        raise RuntimeError("Token de sync non configure sur ce noeud.")

    expected_sig = hmac.new(
        cfg.peer_token.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, signature):
        raise RuntimeError("Invalid HMAC signature.")

    if not body or len(body) < 100:
        raise RuntimeError("Received DB is invalid (too short).")

    # Verif sqlite header : doit commencer par 'SQLite format 3\x00'
    if not body.startswith(b"SQLite format 3\x00"):
        raise RuntimeError("Received DB is not a valid SQLite file.")

    if not APPLY_ENABLED:
        return {
            "received": True,
            "applied": False,
            "reason": "dry-run (MUROS_APPLY off), DB recue non ecrite.",
            "size_bytes": len(body),
        }

    db_path = _get_db_path()

    # Backup pre-sync.
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"pre-sync-{ts}.db"
    if db_path.exists():
        try:
            shutil.copy2(db_path, backup_path)
            os.chmod(backup_path, 0o600)
        except OSError as exc:
            log.warning("Backup pre-sync impossible : %s", exc)

    # Ecriture atomique : on ecrit dans un .tmp puis on remplace.
    tmp_path = db_path.with_suffix(".db.tmp")
    tmp_path.write_bytes(body)
    os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, db_path)

    return {
        "received": True,
        "applied": True,
        "backup_path": str(backup_path),
        "size_bytes": len(body),
    }


# --- Rotation log ---

def _rotate_log(db: Session, keep: int = 50) -> None:
    """Supprime les vieux logs au-dela des N derniers."""
    ids = (
        db.query(models.HaSyncLog.id)
        .order_by(models.HaSyncLog.id.desc())
        .offset(keep)
        .all()
    )
    if ids:
        old_ids = [i[0] for i in ids]
        db.query(models.HaSyncLog).filter(
            models.HaSyncLog.id.in_(old_ids)
        ).delete(synchronize_session=False)
        db.commit()


# --- Auto-push apres apply ---

_AUTO_PUSH_RUNNING = False


def maybe_auto_push(db: Session, triggered_by: str = "apply") -> None:
    """A appeler apres chaque apply de conf.

    Push silencieux vers le peer si sync_mode=auto et qu'on est MASTER.
    Erreurs loguees mais pas remontees a l'appelant (best-effort).
    """
    global _AUTO_PUSH_RUNNING
    if _AUTO_PUSH_RUNNING:
        # Evite la recursion si le receive declenche un apply qui re-push.
        return
    cfg = db.get(models.HaSyncConfig, 1)
    if cfg is None or not cfg.enabled or cfg.sync_mode != "auto":
        return
    if not is_writable_role():
        return
    _AUTO_PUSH_RUNNING = True
    try:
        push_to_peer(db, cfg, triggered_by=triggered_by)
    except Exception as exc:  # noqa: BLE001
        log.warning("Auto-push HA echec : %s", exc)
    finally:
        _AUTO_PUSH_RUNNING = False
