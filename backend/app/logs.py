"""Lecture des logs firewall (journald).

Le noyau Linux emet ses logs nftables dans le buffer kernel, recuperes par
journald (ou rsyslog). On filtre via journalctl sur le prefixe "[muros]"
qu'on injecte dans les regles via la directive `log prefix`.

Format de prefixe genere par compiler.py :
    [muros <ACTION> r=<RULE_ID> <CHAIN>]
ex.: [muros DROP r=123 input]
Le parseur ci-dessous extrait action / rule_id / chain pour que l'UI les
affiche en clair plutot que de noyer l'admin dans le brut kernel.

En dev (pas de logs reels), on retourne une liste vide.
"""
import json
import logging
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

log = logging.getLogger("muros.logs")

# Prefixe enrichi : [muros ACTION r=ID CHAIN]
# Ancien format (retrocompat) : [muros]
_PREFIX_RE = re.compile(
    r"\[muros(?:\s+(?P<action>[A-Z]+)\s+r=(?P<rule_id>\d+)(?:\s+(?P<chain>[a-z]+))?)?\s*\]"
)


class FirewallLogEntry(TypedDict):
    timestamp: str
    message: str
    hostname: str | None
    syslog_identifier: str | None
    action: str | None
    rule_id: int | None
    chain: str | None


def read_firewall_logs(
    limit: int = 200,
    search: str | None = None,
    scope: str = "muros",
) -> list[FirewallLogEntry]:
    """Lit les dernieres entrees du journal.

    scope:
      - 'muros' (defaut) : uniquement les lignes prefixees [muros] (regles nft avec log)
      - 'kernel'          : tous les logs noyau recents
    """
    args = [
        "journalctl",
        "--output=json",
        "--no-pager",
        "-k",
        "-n", str(min(max(limit, 1), 2000)),
    ]
    if search:
        args += ["-g", search]
    elif scope == "muros":
        args += ["-g", "\\[muros\\]"]
    # scope == 'kernel' : pas de filtre, tout le buffer kernel
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        log.warning("journalctl indisponible: %s", e)
        raise RuntimeError(f"journalctl indisponible : {e}") from e

    if res.returncode not in (0, 1):  # 1 = pas de match, OK
        stderr = (res.stderr or "").strip()
        raise RuntimeError(stderr or f"journalctl code {res.returncode}")

    entries: list[FirewallLogEntry] = []
    for line in res.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_us = obj.get("__REALTIME_TIMESTAMP")
        try:
            ts = datetime.fromtimestamp(int(ts_us) / 1_000_000).isoformat() if ts_us else ""
        except (ValueError, TypeError):
            ts = ""
        message = obj.get("MESSAGE", "")
        action: str | None = None
        rule_id: int | None = None
        chain: str | None = None
        m = _PREFIX_RE.search(message)
        if m:
            action = m.group("action")
            rid = m.group("rule_id")
            if rid:
                try:
                    rule_id = int(rid)
                except ValueError:
                    rule_id = None
            chain = m.group("chain")
        entries.append(FirewallLogEntry(
            timestamp=ts,
            message=message,
            hostname=obj.get("_HOSTNAME"),
            syslog_identifier=obj.get("SYSLOG_IDENTIFIER"),
            action=action,
            rule_id=rule_id,
            chain=chain,
        ))
    return entries


@dataclass(slots=True)
class SystemLogEntry:
    timestamp: str
    unit: str
    priority: int
    message: str


# Single source of truth for which systemd units the System journal log
# viewer is allowed to query. The set is enforced at the read entry
# point and the same list is exposed read-only through `list_known_units`
# so the UI dropdown matches what the API accepts. Keep this list in
# sync with the `_SERVICE_CATALOG` in `system_actions.py`: every MurOS
# daemon plus every feature daemon should be reachable from the System
# journal viewer.
ALLOWED_UNITS = {
    # MurOS daemons.
    "muros-backend.service",
    "muros-boot.service",
    "muros-nft.service",
    "muros-self-upgrade.service",
    "muros-wan-monitor.service",
    "muros-watcher.service",

    # Management plane.
    "nginx.service",
    "ssh.service",
    "openssh-server.service",
    "fail2ban.service",
    "snmpd.service",
    "chrony.service",

    # LAN services published by the firewall.
    "kea-dhcp4-server.service",
    "unbound.service",

    # High availability.
    "keepalived.service",
    "conntrackd.service",

    # VPN.
    "strongswan.service",
    "strongswan-starter.service",
    "wg-quick@wg0.service",
}


def read_system_logs(
    unit: str = "muros-backend.service",
    limit: int = 200,
    since_minutes: int | None = None,
    search: str | None = None,
    priority: str | None = None,
) -> list[SystemLogEntry]:
    """Lit les dernieres entrees journald d'un service systemd.

    - `unit` : nom de l'unit systemd. Restreint a une whitelist pour
      eviter une injection vers `journalctl -u <untrusted>`.
    - `priority` : 'err' (3), 'warning' (4), 'info' (6) ou None (tout).
    """
    if unit not in ALLOWED_UNITS:
        raise RuntimeError(f"unit '{unit}' not allowed")
    args = [
        "journalctl",
        "--output=json",
        "--no-pager",
        "-u", unit,
        "-n", str(min(max(limit, 1), 2000)),
    ]
    if since_minutes:
        args += ["--since", f"-{int(since_minutes)} min"]
    if search:
        args += ["-g", search]
    if priority in ("err", "warning", "info", "debug"):
        prio_map = {"err": "3", "warning": "4", "info": "6", "debug": "7"}
        args += ["-p", prio_map[priority]]
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        raise RuntimeError(f"journalctl unavailable: {e}") from e
    if res.returncode not in (0, 1):
        raise RuntimeError((res.stderr or "").strip() or f"journalctl code {res.returncode}")
    entries: list[SystemLogEntry] = []
    for line in res.stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts_us = obj.get("__REALTIME_TIMESTAMP")
        try:
            ts = datetime.fromtimestamp(int(ts_us) / 1_000_000).isoformat() if ts_us else ""
        except (ValueError, TypeError):
            ts = ""
        try:
            prio = int(obj.get("PRIORITY", 6))
        except (ValueError, TypeError):
            prio = 6
        entries.append(SystemLogEntry(
            timestamp=ts,
            unit=obj.get("_SYSTEMD_UNIT") or obj.get("UNIT") or unit,
            priority=prio,
            message=obj.get("MESSAGE", ""),
        ))
    return entries


def list_known_units() -> list[str]:
    """Liste les units pour lesquelles on accepte journalctl -u.

    Renvoie une liste ordonnee (ordre d'importance pour MurOS) plutot
    que le set ALLOWED_UNITS pour que le menu deroulant de la page
    Logs > System journal soit deterministe.
    """
    return [
        # Always-on stack.
        "muros-backend.service",
        "nginx.service",
        "ssh.service",
        "fail2ban.service",
        "snmpd.service",
        "chrony.service",

        # MurOS optional daemons.
        "muros-watcher.service",
        "muros-wan-monitor.service",

        # LAN services published by the firewall.
        "kea-dhcp4-server.service",
        "unbound.service",

        # High availability.
        "keepalived.service",
        "conntrackd.service",

        # VPN.
        "strongswan.service",
        "wg-quick@wg0.service",

        # Other MurOS internal units (boot-time and self-upgrade).
        "muros-boot.service",
        "muros-nft.service",
        "muros-self-upgrade.service",
    ]


def get_logs_status(db_session) -> dict:
    """Retourne quelques stats utiles a la page Logs.

    - `rules_with_log` : nombre de regles avec log=true (activees ou non)
    - `rules_with_log_enabled` : idem mais seulement les enabled
    - `journalctl_available` : True si on a pu invoquer journalctl
    - `is_root` : si l'API tourne root (necessaire pour `journalctl -k`)
    """
    from app import models
    import os
    total = db_session.query(models.FirewallRule).filter(models.FirewallRule.log.is_(True)).count()
    enabled = (
        db_session.query(models.FirewallRule)
        .filter(models.FirewallRule.log.is_(True), models.FirewallRule.enabled.is_(True))
        .count()
    )
    journalctl_ok = True
    try:
        subprocess.run(["journalctl", "--version"], capture_output=True, timeout=2)
    except (subprocess.SubprocessError, FileNotFoundError):
        journalctl_ok = False
    return {
        "rules_with_log": total,
        "rules_with_log_enabled": enabled,
        "journalctl_available": journalctl_ok,
        "is_root": os.geteuid() == 0,
    }
