"""Configuration synchronization between the 2 HA nodes.

Pattern inspired by OPNsense: the MASTER pushes its sqlite DB to the
BACKUP after each apply (auto mode) or on a manual action (manual mode).

The BACKUP receives the DB via POST /api/ha/sync/receive, validates the
token, makes a copy of its local DB into
/var/lib/muros/backups/pre-sync-*.db, replaces the DB and applies the
config (muros_boot.py replays everything).

The VRRP role is determined by parsing `ip addr show` (presence of a
MurOS VIP) or via the state file written by the keepalived hook
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

# Resolve the sqlite DB path. We follow the db module convention.
DEFAULT_DB_PATH = "/var/lib/muros/muros.db"
BACKUP_DIR = Path("/var/lib/muros/backups")
VRRP_STATE_FILE = Path("/run/muros/vrrp-state")


def _get_db_path() -> Path:
    """Return the absolute path of the sqlite DB."""
    p = os.environ.get("MUROS_DB", DEFAULT_DB_PATH)
    return Path(p)


def generate_token() -> str:
    """Generate a long sync token (64 hex chars = 32 bytes)."""
    return py_secrets.token_hex(32)


def get_vrrp_role() -> str:
    """Return the current VRRP role: MASTER, BACKUP, FAULT, STANDALONE.

    STANDALONE = keepalived is not configured (no HA).
    """
    # Method 1: file written by the keepalived hook.
    if VRRP_STATE_FILE.exists():
        try:
            content = VRRP_STATE_FILE.read_text(encoding="utf-8").strip()
            # Expected format: "<instance> <state>" or just "<state>"
            parts = content.split()
            if parts:
                state = parts[-1].upper()
                if state in ("MASTER", "BACKUP", "FAULT"):
                    return state
        except OSError:
            pass

    # Method 2: parse the keepalived output via journalctl (heuristic).
    try:
        r = subprocess.run(
            ["systemctl", "is-active", "keepalived"],
            capture_output=True, text=True, timeout=2,
        )
        if r.returncode != 0 or r.stdout.strip() != "active":
            return "STANDALONE"
    except (subprocess.SubprocessError, FileNotFoundError):
        return "STANDALONE"

    # keepalived is running but no state file: assume MASTER by default
    # (the hook has not fired yet).
    return "MASTER"


def is_writable_role() -> bool:
    """True if the node can accept writes (MASTER or STANDALONE)."""
    role = get_vrrp_role()
    return role in ("MASTER", "STANDALONE")


# --- Read the sync config ---

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
    """Read the sqlite DB content as bytes.

    We run PRAGMA wal_checkpoint first to avoid pushing a DB with data
    still in the WAL that would not be in the .db file.
    """
    db_path = _get_db_path()
    if not db_path.exists():
        raise RuntimeError(f"DB not found: {db_path}")

    # Checkpoint WAL to get a complete DB in the main file.
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("WAL checkpoint failed : %s", exc)

    return db_path.read_bytes()


def _http_post(url: str, headers: dict, body: bytes, verify_tls: bool, timeout: int = 30) -> tuple[int, bytes]:
    """Minimal HTTP POST via urllib (no external dep)."""
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
    """Ping the peer via GET /api/ha/sync/ping. Returns the peer role and version."""
    if not cfg.peer_url or not cfg.peer_token:
        raise RuntimeError("Peer URL or token missing.")
    url = cfg.peer_url.rstrip("/") + "/api/ha/sync/ping"
    headers = {"X-Muros-Sync-Token": cfg.peer_token}
    status, body = _http_get(url, headers, cfg.verify_tls, timeout=5)
    if status != 200:
        raise RuntimeError(f"Peer responded {status}: {body.decode('utf-8', errors='replace')[:200]}")
    import json
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        raise RuntimeError("Invalid peer response (JSON expected).")
    return data


def push_to_peer(db: Session, cfg: models.HaSyncConfig, triggered_by: str = "manual") -> dict:
    """Push the current sqlite DB to the peer.

    Creates a HaSyncLog with success/failure.
    """
    if not cfg.enabled:
        raise RuntimeError("HA synchronization disabled.")
    if not cfg.peer_url or not cfg.peer_token:
        raise RuntimeError("Peer URL or token missing.")
    if not is_writable_role():
        raise RuntimeError("This node is not MASTER, push refused.")

    started = time.time()
    db_bytes = b""
    error: str | None = None

    try:
        db_bytes = _read_db_bytes()
        # Compute HMAC-SHA256 of the content with the token as the key.
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
                f"Peer responded {status}: {body.decode('utf-8', errors='replace')[:300]}"
            )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)[:500]
        log.warning("HA sync push failed: %s", exc)

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


# --- Receive: receiving the DB from the peer ---

def receive_from_peer(cfg: models.HaSyncConfig, signature: str, body: bytes) -> dict:
    """Receive a sqlite DB from the peer.

    Verifies the HMAC signature, makes a local backup, writes the new DB.
    The backend service must be restarted afterwards (caller responsibility).
    """
    if not cfg.enabled:
        raise RuntimeError("HA synchronization disabled on this node.")
    if not cfg.peer_token:
        raise RuntimeError("Sync token not configured on this node.")

    expected_sig = hmac.new(
        cfg.peer_token.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, signature):
        raise RuntimeError("Invalid HMAC signature.")

    if not body or len(body) < 100:
        raise RuntimeError("Received DB is invalid (too short).")

    # Check sqlite header: must start with 'SQLite format 3\x00'
    if not body.startswith(b"SQLite format 3\x00"):
        raise RuntimeError("Received DB is not a valid SQLite file.")

    if not APPLY_ENABLED:
        return {
            "received": True,
            "applied": False,
            "reason": "dry-run (MUROS_APPLY off), received DB not written.",
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
            log.warning("Pre-sync backup failed: %s", exc)

    # Atomic write: write to a .tmp then replace.
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


# --- Log rotation ---

def _rotate_log(db: Session, keep: int = 50) -> None:
    """Delete old logs beyond the last N."""
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


# --- Auto-push after apply ---

_AUTO_PUSH_RUNNING = False


def maybe_auto_push(db: Session, triggered_by: str = "apply") -> None:
    """To call after each config apply.

    Silent push to the peer if sync_mode=auto and we are MASTER.
    Errors are logged but not propagated to the caller (best-effort).
    """
    global _AUTO_PUSH_RUNNING
    if _AUTO_PUSH_RUNNING:
        # Avoid recursion if the receive triggers an apply that re-pushes.
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
        log.warning("HA auto-push failed: %s", exc)
    finally:
        _AUTO_PUSH_RUNNING = False

