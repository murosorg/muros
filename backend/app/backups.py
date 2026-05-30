"""Snapshots de la configuration MurOS.

Un snapshot est une archive tar.gz contenant :
- muros.db : copie de la base SQLite
- nftables.snapshot : sortie de `nft list ruleset`
- network/interfaces : contenu de /etc/network/interfaces si present
- chrony/conf.d/muros.conf : drop-in NTP (chrony) gere par MurOS
- network/resolved.conf.d/muros.conf : drop-in DNS gere par MurOS
- sysctl.txt : sysctl net.ipv4.ip_forward + ipv6
- manifest.json : meta (timestamp, version, hostname, label)

Les archives sont stockees dans MUROS_BACKUP_DIR (defaut /var/lib/muros/backups).
Retention par defaut : 14 backups, configurable via MUROS_BACKUP_RETENTION.
"""
from __future__ import annotations

import io
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app import __version__
from app.db import DB_PATH


def _sqlite_safe_copy(src: Path, dest: Path) -> None:
    """Consistent copy of a SQLite DB, even while it is being written
    (WAL mode). We use the native backup API that handles the engine-side
    transactional snapshot, rather than a raw cp that may capture a .db and
    a .db-wal out of sync.
    """
    src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()

BACKUP_DIR = Path(os.environ.get("MUROS_BACKUP_DIR", "/var/lib/muros/backups"))
RETENTION = int(os.environ.get("MUROS_BACKUP_RETENTION", "14"))


def _ensure_dir() -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _safe_read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _nft_dump() -> str:
    # Pure kernel-state read, sub-second even with a large ruleset.
    try:
        out = subprocess.check_output(["nft", "list", "ruleset"], text=True, timeout=5)
        return out
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _sysctl_dump() -> str:
    keys = ["net.ipv4.ip_forward", "net.ipv6.conf.all.forwarding"]
    lines: list[str] = []
    for k in keys:
        try:
            v = subprocess.check_output(["sysctl", "-n", k], text=True, timeout=2).strip()
            lines.append(f"{k} = {v}")
        except (subprocess.SubprocessError, FileNotFoundError):
            lines.append(f"{k} = ?")
    return "\n".join(lines) + "\n"


def create_backup(label: str | None = None) -> dict:
    """Cree un snapshot et retourne ses metadonnees."""
    _ensure_dir()
    ts = datetime.now(timezone.utc)
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    safe_label = (label or "").strip().replace("/", "_").replace(" ", "_")[:32]
    name = f"muros-{stamp}" + (f"-{safe_label}" if safe_label else "") + ".tar.gz"
    path = BACKUP_DIR / name

    manifest = {
        "created_at": ts.isoformat(),
        "version": __version__,
        "hostname": platform.node(),
        "kernel": platform.release(),
        "label": label or "",
    }

    with tarfile.open(path, "w:gz") as tar:
        # SQLite DB: consistent copy via the backup API (transactional
        # snapshot) rather than a raw cp, otherwise in WAL mode we risk
        # archiving an inconsistent .db and .db-wal.
        db_path = Path(DB_PATH)
        if db_path.is_file():
            with tempfile.NamedTemporaryFile(
                suffix=".db", delete=False,
            ) as tmp_db_file:
                tmp_db_path = Path(tmp_db_file.name)
            try:
                _sqlite_safe_copy(db_path, tmp_db_path)
                tar.add(tmp_db_path, arcname="muros.db")
            finally:
                tmp_db_path.unlink(missing_ok=True)
        # nftables
        _add_text(tar, "nftables.snapshot", _nft_dump())
        # Network files (informational snapshots; the application manages
        # DNS and NTP via the systemd drop-ins)
        _add_text(tar, "network/interfaces", _safe_read("/etc/network/interfaces"))
        _add_text(tar, "chrony/conf.d/muros.conf",
                  _safe_read("/etc/chrony/conf.d/muros.conf"))
        _add_text(tar, "network/resolved.conf.d/muros.conf",
                  _safe_read("/etc/systemd/resolved.conf.d/muros.conf"))
        _add_text(tar, "sysctl.txt", _sysctl_dump())
        # Manifest
        _add_text(tar, "manifest.json", json.dumps(manifest, indent=2))

    _prune_old()
    return _stat_entry(path)


def _add_text(tar: tarfile.TarFile, name: str, content: str) -> None:
    data = content.encode("utf-8")
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mtime = int(datetime.now(timezone.utc).timestamp())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def list_backups() -> list[dict]:
    _ensure_dir()
    return sorted(
        (_stat_entry(p) for p in BACKUP_DIR.glob("muros-*.tar.gz")),
        key=lambda e: e["name"], reverse=True,
    )


def _stat_entry(path: Path) -> dict:
    st = path.stat()
    label = ""
    manifest: dict = {}
    try:
        with tarfile.open(path, "r:gz") as tar:
            try:
                m = tar.extractfile("manifest.json")
                if m:
                    manifest = json.loads(m.read().decode("utf-8"))
                    label = manifest.get("label", "")
            except KeyError:
                pass
    except tarfile.TarError:
        pass
    return {
        "name": path.name,
        "size_bytes": st.st_size,
        "created_at": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        "label": label,
        "manifest": manifest,
    }


def _resolve(name: str) -> Path:
    if "/" in name or ".." in name or not name.endswith(".tar.gz"):
        raise ValueError("invalid backup name")
    path = BACKUP_DIR / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def delete_backup(name: str) -> None:
    path = _resolve(name)
    path.unlink()


def restore_backup(name: str) -> dict:
    """Restore the DB from a backup. The other files are extracted into a
    restore folder `MUROS_BACKUP_DIR/_restore/`; the admin must re-deploy
    them manually (avoid silently overwriting the system configuration).
    """
    path = _resolve(name)
    with tarfile.open(path, "r:gz") as tar, tempfile.TemporaryDirectory() as tmp:
        tar.extractall(tmp)
        tmp_db = Path(tmp) / "muros.db"
        if tmp_db.is_file():
            # Close the SQLAlchemy pool before overwriting the file to
            # avoid keeping open handles to the old DB and to clean up the
            # WAL/SHM tied to the old instance.
            from app.db import engine
            engine.dispose()
            for suffix in ("-wal", "-shm"):
                side = Path(str(DB_PATH) + suffix)
                if side.exists():
                    side.unlink(missing_ok=True)
            shutil.copy2(tmp_db, DB_PATH)
        restore_dir = BACKUP_DIR / "_restore"
        if restore_dir.exists():
            shutil.rmtree(restore_dir)
        shutil.copytree(tmp, restore_dir)
        manifest_path = Path(tmp) / "manifest.json"
        manifest = {}
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
        return {
            "restored": name,
            "manifest": manifest,
            "extracted_to": str(restore_dir),
            "db_restored": tmp_db.is_file(),
        }


def _prune_old() -> None:
    entries = list_backups()
    for e in entries[RETENTION:]:
        try:
            (BACKUP_DIR / e["name"]).unlink()
        except OSError:
            pass
