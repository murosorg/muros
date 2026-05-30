"""Hardening kernel via sysctl (READ-ONLY cote API).

MurOS livre une drop-in `/etc/sysctl.d/99-muros-hardening.conf` avec le
paquet (cf packaging/etc/sysctl.d/). Le postinst applique au installage
via `sysctl --system`. L'admin ne peut pas modifier cette drop-in depuis
l'UI : c'est une garantie structurelle de l'appliance, pas un parametre.

Ce module expose seulement `get_status()` pour les checks de diagnostic
(lire la valeur actuelle du noyau pour chaque cle).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.apply import APPLY_ENABLED

DROPIN_PATH = Path(os.environ.get("MUROS_SYSCTL_PATH", "/etc/sysctl.d/99-muros-hardening.conf"))

# Managed keys + recommended value. The order is preserved in the written file.
# We distinguish:
# - "functional" keys (ip_forward...): required for a firewall to do its
#   transit job. Without them, the box forwards nothing.
# - "security" keys: hardening against spoofing, floods, redirects.
# Principle: we manage ONLY the keys where Debian 13 stock != the value a
# firewall needs. Everything already hardened by default on Debian
# (syncookies, source_route, icmp_echo_ignore_broadcasts, somaxconn 4096...)
# is left to the kernel/systemd, we do not pin it in the MurOS drop-in.
RECOMMENDED: dict[str, str] = {
    # Forwarding: the basis of a firewall. Debian default = 0.
    "net.ipv4.ip_forward": "1",
    "net.ipv6.conf.all.forwarding": "1",
    "net.ipv6.conf.default.forwarding": "1",

    # Conntrack : Debian par defaut ~65536, trop juste pour un firewall a
    # vrai trafic. On monte a 262144.
    "net.netfilter.nf_conntrack_max": "262144",

    # SYN backlog: Debian default 1024-2048, we smooth the peaks to 4096.
    "net.ipv4.tcp_max_syn_backlog": "4096",

    # Anti IP spoofing : systemd 50-default.conf met rp_filter=2 (loose).
    # On force strict (1), valable pour un firewall a topologie symetrique.
    "net.ipv4.conf.all.rp_filter": "1",
    "net.ipv4.conf.default.rp_filter": "1",

    # ICMP redirects: Debian default accept=1 and send=1 (classic host).
    # A firewall has no reason to accept or emit them.
    "net.ipv4.conf.all.accept_redirects": "0",
    "net.ipv4.conf.default.accept_redirects": "0",
    "net.ipv4.conf.all.send_redirects": "0",
    "net.ipv4.conf.default.send_redirects": "0",
    "net.ipv6.conf.all.accept_redirects": "0",
    "net.ipv6.conf.default.accept_redirects": "0",

    # Logging des paquets martians : Debian par defaut 0, utile en enquete.
    "net.ipv4.conf.all.log_martians": "1",
}

# Categories pour grouper l'UI : "Fonctionnel" (le firewall ne marche pas sans),
# "Tuning" (defauts noyau insuffisants pour un firewall a vrai trafic), et
# "Securite" pour le durcissement.
CATEGORIES: dict[str, str] = {
    "net.ipv4.ip_forward": "Fonctionnel",
    "net.ipv6.conf.all.forwarding": "Fonctionnel",
    "net.ipv6.conf.default.forwarding": "Fonctionnel",
    "net.netfilter.nf_conntrack_max": "Tuning",
    "net.ipv4.tcp_max_syn_backlog": "Tuning",
}  # tout le reste sera "Securite" par defaut


def _category_of(key: str) -> str:
    return CATEGORIES.get(key, "Securite")

ALLOWED_KEYS = frozenset(RECOMMENDED.keys())

# Short description of each key, shown as a tooltip in the UI.
# Stay factual: what the key does + why we want it at the recommended value.
DESCRIPTIONS: dict[str, str] = {
    "net.ipv4.ip_forward": (
        "Active le forwarding IPv4 : autorise le noyau a router les paquets "
        "entre interfaces. Sans cette valeur a 1, le boitier ne fait pas "
        "transiter le trafic et ne sert pas de firewall. Indispensable."
    ),
    "net.ipv6.conf.all.forwarding": (
        "Active le forwarding IPv6 sur toutes les interfaces. Symetrique de "
        "ip_forward pour la pile v6. Necessaire si vous routez de l'IPv6."
    ),
    "net.ipv6.conf.default.forwarding": (
        "Meme chose que forwarding all mais applique aux nouvelles interfaces "
        "creees apres le boot (VLAN ajoute a chaud par exemple)."
    ),
    "net.netfilter.nf_conntrack_max": (
        "Taille maximum de la table de suivi de connexions (conntrack). Quand "
        "elle se remplit, le noyau drop des nouvelles connexions et logge "
        "'nf_conntrack: table full'. Defaut Debian 65536 = OK pour un poste, "
        "trop juste pour un firewall. On vise 262144."
    ),
    "net.ipv4.tcp_max_syn_backlog": (
        "Taille de la queue des SYN en attente. Conjugue aux SYN cookies "
        "(deja actives par defaut sur Debian), donne une marge avant que le "
        "noyau ne commence a encoder les SYN."
    ),
    "net.ipv4.conf.all.rp_filter": (
        "Reverse-path filter strict (1) : le noyau drop un paquet si sa "
        "source ne correspond pas a la route de retour. Empeche le spoofing "
        "d'adresses IP source. Mode strict, valable pour un firewall a la "
        "topologie symetrique."
    ),
    "net.ipv4.conf.default.rp_filter": (
        "Meme chose que rp_filter all, mais pour les nouvelles interfaces "
        "creees apres le boot (VLAN ajoute a chaud par exemple)."
    ),
    "net.ipv4.conf.all.accept_redirects": (
        "Refuse les ICMP redirect entrants. Un attaquant local peut envoyer "
        "un faux redirect pour faire router le trafic via une passerelle "
        "pirate. Aucun cas legitime sur un firewall."
    ),
    "net.ipv4.conf.default.accept_redirects": (
        "Idem accept_redirects all, mais pour les nouvelles interfaces."
    ),
    "net.ipv4.conf.all.send_redirects": (
        "N'emet pas d'ICMP redirect sortants. Le firewall n'a pas a indiquer "
        "aux clients un meilleur chemin : c'est son role d'imposer la route."
    ),
    "net.ipv4.conf.default.send_redirects": (
        "Idem send_redirects all, pour les nouvelles interfaces."
    ),
    "net.ipv4.conf.all.log_martians": (
        "Logge dans le journal noyau les paquets avec adresse impossible "
        "(martians) : source dans 127.0.0.0/8 en entree externe, etc. "
        "Bruit modere, tres utile en cas d'enquete."
    ),
    "net.ipv6.conf.all.accept_redirects": (
        "Refuse les redirects ICMPv6. Meme rationnel qu'en IPv4."
    ),
    "net.ipv6.conf.default.accept_redirects": (
        "Idem pour les nouvelles interfaces IPv6."
    ),
}


def _read_kernel_value(key: str) -> str | None:
    """Current value in the kernel. None if the key does not exist."""
    try:
        out = subprocess.check_output(
            ["sysctl", "-n", key], text=True, timeout=2, stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None


def _read_dropin() -> dict[str, str]:
    """Parse la drop-in MurOS si elle existe."""
    if not DROPIN_PATH.is_file():
        return {}
    out: dict[str, str] = {}
    for line in DROPIN_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in ALLOWED_KEYS:
            out[k] = v
    return out


def get_status() -> dict:
    """Return the current status for each whitelisted key."""
    dropin = _read_dropin()
    items: list[dict] = []
    hardened = True
    for key, recommended in RECOMMENDED.items():
        current = _read_kernel_value(key)
        managed = key in dropin
        ok = current is not None and current == recommended
        if not ok:
            hardened = False
        items.append({
            "key": key,
            "recommended": recommended,
            "current": current,
            "managed_by_muros": managed,
            "ok": ok,
            "description": DESCRIPTIONS.get(key, ""),
            "category": _category_of(key),
        })
    return {
        "items": items,
        "hardened": hardened,
        "dropin_path": str(DROPIN_PATH),
        "dropin_exists": DROPIN_PATH.is_file(),
        "apply_enabled": APPLY_ENABLED,
    }


# The drop-in /etc/sysctl.d/99-muros-hardening.conf is shipped by the package
# and applied at postinst via `sysctl --system`. MurOS no longer exposes an
# apply/reset endpoint from the UI: it is a structural guarantee of the
# appliance, not a configurable setting. The module only keeps `get_status`
# for diagnostic checks.
