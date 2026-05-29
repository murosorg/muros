# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Configuration de la base de donnees (SQLite + SQLAlchemy).

PRAGMAs poses a chaque connexion :
- journal_mode=WAL : lectures concurrentes pendant l'ecriture (l'API et
  les jobs internes lisent en parallele de l'UI qui ecrit).
- synchronous=NORMAL : bon compromis perf/durabilite en mode WAL
  (recommandation officielle SQLite, FULL est overkill ici).
- foreign_keys=ON : SQLite ne respecte pas les FK par defaut, ce qui est
  un piege classique.
- busy_timeout=5000 : evite les "database is locked" sur ecriture
  concurrente furtive.
"""
import os
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

DB_PATH = os.environ.get("MUROS_DB", "muros.db")
DB_URL = f"sqlite:///{DB_PATH}"

# SQLite engine notes:
# - check_same_thread=False because FastAPI dispatches requests on a
#   thread pool and a session may be released on a different thread.
# - NullPool: SQLite open()/close() on a local file with WAL is cheap,
#   and the default QueuePool (size=5, overflow=10) caps total concurrent
#   sessions to 15. Long-running endpoints (monitoring sparklines, logs
#   tail, apply) plus a background watcher are enough to exhaust it and
#   surface as `QueuePool limit of size 5 overflow 10 reached`. NullPool
#   sidesteps the issue entirely: every request opens its own connection
#   and closes it on session.close(), no cumulative leak possible.
# - busy_timeout (PRAGMA below) absorbs short write contention.
engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False, "timeout": 30},
    poolclass=NullPool,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _connection_record):
    """Pose les PRAGMA WAL/synchronous/foreign_keys a chaque connexion."""
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
    finally:
        cur.close()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    # L'import enregistre les modeles dans Base.metadata avant create_all.
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _migrate_schema()


def _migrate_schema() -> None:
    """Schema migration hook.

    Run lightweight ALTER TABLE for additive column changes between
    releases. SQLAlchemy create_all() only creates missing TABLES, not
    missing columns, so we top it up here.
    """
    from sqlalchemy import text, inspect
    with engine.connect() as conn:
        insp = inspect(conn)
        try:
            cols = {c["name"] for c in insp.get_columns("interfaces")}
        except Exception:
            cols = set()
        if cols and "pending_delete" not in cols:
            conn.execute(text(
                "ALTER TABLE interfaces ADD COLUMN pending_delete "
                "BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()

        # rc13 : strict pending model on firewall/NAT/zones. Every
        # create/update/delete/reorder sets dirty=True. Apply clears it.
        # The UI surfaces a count per page so the admin always knows
        # how many DB changes are not yet on the kernel.
        for table_name in ("firewall_rules", "nat_rules", "zones"):
            try:
                tcols = {c["name"] for c in insp.get_columns(table_name)}
            except Exception:
                tcols = set()
            if tcols and "dirty" not in tcols:
                # Existing rows are flagged dirty=True on first migration:
                # we have no way to know if they were applied to the kernel
                # before this column existed, so we play it safe and ask
                # the admin to click Apply once after upgrade.
                conn.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN dirty "
                    "BOOLEAN NOT NULL DEFAULT 1"
                ))
                conn.commit()

        # rc87 : public_endpoint on wireguard_config to pre-fill the
        # Endpoint line in exported client configs.
        try:
            wgcols = {c["name"] for c in insp.get_columns("wireguard_config")}
        except Exception:
            wgcols = set()
        if wgcols and "public_endpoint" not in wgcols:
            conn.execute(text(
                "ALTER TABLE wireguard_config ADD COLUMN public_endpoint "
                "VARCHAR(255) NOT NULL DEFAULT ''"
            ))
            conn.commit()

        # rc119 : client_allowed_ips on wireguard_peers. Lets the admin
        # control what the *client* routes through the tunnel (the
        # [Peer] AllowedIPs in the exported client config). Empty value
        # falls back to a full-tunnel default at export time.
        try:
            wgpcols = {c["name"] for c in insp.get_columns("wireguard_peers")}
        except Exception:
            wgpcols = set()
        if wgpcols and "client_allowed_ips" not in wgpcols:
            conn.execute(text(
                "ALTER TABLE wireguard_peers ADD COLUMN client_allowed_ips "
                "VARCHAR(255) NOT NULL DEFAULT ''"
            ))
            conn.commit()

        # ssh-admin-disable : track operator-driven sshd disable.
        # See routes/tls_ssh.py POST /api/ssh/service/toggle.
        try:
            sshcols = {c["name"] for c in insp.get_columns("ssh_config")}
        except Exception:
            sshcols = set()
        if sshcols and "admin_disabled" not in sshcols:
            conn.execute(text(
                "ALTER TABLE ssh_config ADD COLUMN admin_disabled "
                "BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()

        # rc128 : drop the legacy "Deny all (catch-all)" rule on the
        # forward chain. The chain already has policy drop in the
        # compiler output, so the explicit catch-all rule is redundant
        # and only adds noise to the ruleset. Aligns forward with the
        # input/output convention (no catch-all rule, just the policy).
        try:
            res = conn.execute(text(
                "DELETE FROM firewall_rules "
                "WHERE chain = 'forward' AND action = 'drop' "
                "AND position >= 900 "
                "AND comment = 'Deny all (catch-all)'"
            ))
            if res.rowcount:
                conn.commit()
        except Exception:
            pass
