#!/usr/bin/env python3
"""MurOS watcher : boucle de surveillance des evenements critiques.

Tourne en service systemd separe (muros-watcher.service). Boucle toutes les
30 secondes, verifie les conditions d'alerte, declenche send_mail via
app.notifications.notify (qui gere le throttle par event_type).
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.db import SessionLocal  # noqa: E402
from app import notifications  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s muros-watcher: %(message)s",
)
log = logging.getLogger("muros.watcher")

CHECK_INTERVAL_SEC = int(os.environ.get("MUROS_WATCHER_INTERVAL", "30"))

# Services surveilles. Un service absent de l'OS est ignore silencieusement
# (paquet non installe). Tous les services emettent le meme event_type
# service_down, charge a l'admin d'activer/desactiver la regle dans l'UI.
SERVICES: list[str] = [
    "muros-backend",
    "nginx",
    "fail2ban",
    "keepalived",
    "conntrackd",
    "strongswan",
    "strongswan-starter",
    "wg-quick@wg0",
    "snmpd",
    "postfix",
    "ssh",
    "sshd",
    "chrony",
    "kea-dhcp4-server",
    "unbound",
    "muros-watcher",
]


def _is_installed(unit: str) -> bool:
    r = subprocess.run(
        ["systemctl", "list-unit-files", unit, "--no-legend"],
        capture_output=True, text=True, timeout=5,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def _is_active(unit: str) -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", unit],
        capture_output=True, text=True, timeout=3,
    )
    return r.returncode == 0 and r.stdout.strip() == "active"


def check_services(db) -> None:
    """Alerte si un service installe n'est plus actif."""
    for unit in SERVICES:
        if not _is_installed(unit):
            continue
        if _is_active(unit):
            continue
        notifications.notify(
            db, "service_down",
            subject=f"Service {unit} arrete",
            body=(
                f"Le service systemd {unit} n'est plus actif.\n"
                f"Verifier : systemctl status {unit}"
            ),
        )


def check_fail2ban_bans(db, since_sec: int = 60) -> None:
    """Detecte les nouveaux bans fail2ban dans les X dernieres secondes via journalctl."""
    if not _is_installed("fail2ban"):
        return
    r = subprocess.run(
        ["journalctl", "-u", "fail2ban", f"--since={since_sec} seconds ago",
         "--no-pager", "-q"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return
    bans = re.findall(r"NOTICE\s+\[(\S+)\]\s+Ban\s+([\d.:a-fA-F]+)", r.stdout)
    for jail, ip in bans:
        notifications.notify(
            db, "fail2ban_ban",
            subject=f"IP banned: {ip} (jail {jail})",
            body=f"Fail2ban has banned the IP address {ip} in jail {jail}.",
        )


def check_ipsec_sas(db) -> None:
    """Si on a des connexions IPsec activees mais 0 SA etablie : alerte."""
    try:
        from app import models, ipsec
    except ImportError:
        return
    conns = db.query(models.IpsecConnection).filter_by(enabled=True).all()
    if not conns:
        return
    if not _is_active("strongswan") and not _is_active("strongswan-starter"):
        return  # le check_services s'en occupe
    sas = ipsec.get_status().get("active_sas", [])
    established = {s["name"] for s in sas if s.get("state") == "ESTABLISHED"}
    for c in conns:
        if c.name not in established:
            notifications.notify(
                db, "ipsec_sa_down",
                subject=f"Tunnel IPsec {c.name} down",
                body=f"La connexion IPsec '{c.name}' (remote {c.remote_addrs}) "
                     "n'est pas etablie.",
            )


def check_wireguard_peers(db, max_silent_sec: int = 300) -> None:
    """Alerte si un peer WG n'a pas eu de handshake depuis > max_silent_sec."""
    if not _is_active("wg-quick@wg0"):
        return
    try:
        from app import models
    except ImportError:
        return
    cfg = db.get(models.WireGuardConfig, 1)
    if cfg is None or not cfg.enabled:
        return
    peers = {p.public_key: p for p in db.query(models.WireGuardPeer)
             .filter_by(enabled=True).all()}
    if not peers:
        return
    try:
        r = subprocess.run(
            ["wg", "show", cfg.interface_name, "latest-handshakes"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return
    if r.returncode != 0:
        return
    now = int(time.time())
    for line in r.stdout.strip().splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        pubkey, last_ts = parts[0], int(parts[1])
        peer = peers.get(pubkey)
        if peer is None:
            continue
        if last_ts == 0:
            # Jamais d'handshake, peut etre normal a la creation. On skip.
            continue
        silent = now - last_ts
        if silent > max_silent_sec:
            notifications.notify(
                db, "wireguard_peer_silent",
                subject=f"Peer WireGuard {peer.name} silencieux",
                body=f"Le peer '{peer.name}' n'a pas eu de handshake depuis "
                     f"{silent // 60} minutes.",
            )


def check_conntrack(db) -> None:
    """Alerte si la table conntrack est > 80% pleine."""
    try:
        count = int(Path("/proc/sys/net/netfilter/nf_conntrack_count").read_text().strip())
        maxi = int(Path("/proc/sys/net/netfilter/nf_conntrack_max").read_text().strip())
    except (OSError, ValueError):
        return
    if maxi == 0:
        return
    ratio = count / maxi
    if ratio > 0.80:
        notifications.notify(
            db, "conntrack_high",
            subject=f"Table conntrack a {int(ratio * 100)}%",
            body=f"{count}/{maxi} entrees dans la table conntrack. "
                 "Penser a augmenter nf_conntrack_max si la charge est legitime.",
        )


def check_ha_state_change(db, since_sec: int = 60) -> None:
    """Detecte les bascules VRRP via le journal keepalived."""
    if not _is_installed("keepalived"):
        return
    r = subprocess.run(
        ["journalctl", "-u", "keepalived", f"--since={since_sec} seconds ago",
         "--no-pager", "-q"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return
    seen: set[tuple[str, str]] = set()
    for line in r.stdout.splitlines():
        # Format keepalived : "VRRP_Instance(VI_INSIDE) Entering MASTER STATE"
        m = re.search(r"VRRP_Instance\((\S+)\)\s+(?:Entering|Transition\s+to)\s+(MASTER|BACKUP|FAULT)", line)
        if m:
            inst, state = m.group(1), m.group(2)
            if (inst, state) in seen:
                continue
            seen.add((inst, state))
            notifications.notify(
                db, "ha_state_change",
                subject=f"VRRP {inst} -> {state}",
                body=f"L'instance VRRP {inst} est passee en etat {state}.",
            )


_MUROS_UPDATE_CHECK_INTERVAL_SEC = 3600  # 1h max, polite to GitHub API
_muros_update_last_check_ts = 0
_muros_update_last_notified_version: str | None = None


def check_muros_update(db) -> None:
    """Alert by mail when a new MurOS release is published on GitHub.

    Polled at most once per hour to stay well below the 60 req/h limit
    of unauthenticated GitHub API. The notification rule is throttled to
    24h, so each new release triggers a single email per peer until the
    admin upgrades (or until 24h elapse, whichever comes first).

    Module-level cache `_muros_update_last_notified_version` prevents
    repeated logs on every loop iteration for the same release: the
    notify() call is throttled at the rules layer, but we still want to
    skip the GitHub round-trip when we know nothing changed.
    """
    global _muros_update_last_check_ts, _muros_update_last_notified_version
    now = int(time.time())
    if now - _muros_update_last_check_ts < _MUROS_UPDATE_CHECK_INTERVAL_SEC:
        return
    _muros_update_last_check_ts = now
    try:
        from app import updates
        status = updates.get_muros_status()
    except Exception:  # noqa: BLE001
        # Network down, GitHub rate-limited, or backend module not loaded
        # in this venv: silently retry next hour.
        return
    if not status.get("upgrade_available"):
        return
    candidate = status.get("candidate") or "unknown"
    if candidate == _muros_update_last_notified_version:
        return
    installed = status.get("installed") or "unknown"
    notifications.notify(
        db, "muros_update_available",
        subject=f"MurOS update available: {candidate}",
        body=(
            f"A new MurOS release is available on GitHub.\n"
            f"  Installed : {installed}\n"
            f"  Available : {candidate}\n\n"
            f"Install from the UI: System > Updates."
        ),
    )
    _muros_update_last_notified_version = candidate


def check_disk(db) -> None:
    """Alerte si /var est > 80% rempli."""
    try:
        import shutil
        usage = shutil.disk_usage("/var")
        ratio = usage.used / usage.total
    except OSError:
        return
    if ratio > 0.80:
        notifications.notify(
            db, "disk_high",
            subject=f"/var a {int(ratio * 100)}% d'utilisation",
            body=f"Espace utilise : {usage.used // (1024*1024*1024)} Go sur "
                 f"{usage.total // (1024*1024*1024)} Go. Penser a faire de la place.",
        )


def run_loop() -> None:
    log.info("Watcher demarre (interval=%ds).", CHECK_INTERVAL_SEC)
    while True:
        try:
            db = SessionLocal()
            try:
                notifications.ensure_default_rules(db)
                check_services(db)
                check_fail2ban_bans(db, since_sec=CHECK_INTERVAL_SEC * 2)
                check_ipsec_sas(db)
                check_wireguard_peers(db)
                check_ha_state_change(db, since_sec=CHECK_INTERVAL_SEC * 2)
                check_conntrack(db)
                check_disk(db)
                check_muros_update(db)
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            log.exception("Erreur dans la boucle watcher : %s", exc)
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    run_loop()
