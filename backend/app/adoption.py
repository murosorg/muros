# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Kernel-state adoption at install time.

When the muros package is installed on a machine already in production
(e.g. a Debian 13 box that has been running for 6 months), the kernel
already holds a valid network configuration: IPs on interfaces (DHCP
or static), default routes, MTU, etc.

The postinst removes NetworkManager / systemd-networkd. Without
capturing the existing state, muros-boot would replay an empty DB at
the next reboot and the admin would lose the network (sometimes before
they even had a chance to log into the UI).

Clean solution: on the first run of muros-boot.service, we snapshot
the current kernel state into the DB with dirty=False (nothing to
re-apply, the kernel already has it). From then on, the MurOS DB is
officially the source of truth.

Adoption marker: /var/lib/muros/.adopted. If present, the snapshot is
skipped. Idempotent: can be forced with --force to re-adopt (useful in
recovery).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from app import models

log = logging.getLogger("muros.adoption")

ADOPTED_MARKER = Path(os.environ.get(
    "MUROS_ADOPTED_MARKER", "/var/lib/muros/.adopted",
))

# Interfaces qu'on ignore systematiquement (ni adoptees ni gerees).
_IGNORED_PREFIXES = (
    "lo",
    "docker", "br-", "veth", "virbr", "tun", "tap",
    # Tunnels managed by dedicated MurOS pages (WireGuard, IPsec,
    # generic IP tunnels). They show up as kernel netdevs but are not
    # "physical links" and must not appear in /network.
    "wg", "ipsec", "xfrm", "gre", "gretap", "sit", "ip6tnl", "ppp",
)


def _ip_link_show() -> list[dict]:
    """Renvoie `ip -j -d link show` parse, ou liste vide en erreur."""
    try:
        out = subprocess.check_output(
            ["ip", "-j", "-d", "link", "show"], text=True, timeout=5,
        )
        return json.loads(out)
    except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning("ip link show a echoue : %s", exc)
        return []


def _ip_addr_show() -> dict[str, list[str]]:
    """Renvoie {iface: [cidr1, cidr2...]} pour les adresses IPv4 globales."""
    out: dict[str, list[str]] = {}
    try:
        raw = subprocess.check_output(
            ["ip", "-j", "-4", "addr", "show"], text=True, timeout=5,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return out
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return out
    for entry in data:
        name = entry.get("ifname")
        if not name:
            continue
        addrs: list[str] = []
        for info in entry.get("addr_info", []):
            ip = info.get("local")
            plen = info.get("prefixlen")
            scope = info.get("scope")
            if not ip or plen is None or scope != "global":
                continue
            addrs.append(f"{ip}/{plen}")
        if addrs:
            out[name] = addrs
    return out


def _ip_route_show() -> list[dict]:
    """Renvoie `ip -j -4 route show` parse."""
    try:
        raw = subprocess.check_output(
            ["ip", "-j", "-4", "route", "show"], text=True, timeout=5,
        )
        return json.loads(raw)
    except (subprocess.SubprocessError, FileNotFoundError, json.JSONDecodeError) as exc:
        log.warning("ip route show a echoue : %s", exc)
        return []


def _is_relevant_iface(name: str, link_info: dict) -> bool:
    """Filtre : ignore loopback, docker, bridges virtuels, etc."""
    if not name or name == "lo":
        return False
    if any(name.startswith(p) for p in _IGNORED_PREFIXES):
        return False
    # On garde les VLAN (kind=vlan), bonds, ethernet phys, wireless
    # (mais wifi devrait pas etre sur un firewall en general).
    kind = link_info.get("linkinfo", {}).get("info_kind")
    if kind in ("docker", "bridge", "veth", "tun"):
        return False
    return True


def _adopt_interfaces(db: Session) -> int:
    """Cree ou met a jour les rows Interface a partir du kernel.

    Retourne le nombre d'interfaces touchees.
    """
    links = _ip_link_show()
    addrs = _ip_addr_show()
    touched = 0
    for link in links:
        name = link.get("ifname")
        if not _is_relevant_iface(name, link):
            continue
        mtu = link.get("mtu") or 1500
        state = link.get("operstate", "UNKNOWN")
        enabled = state.upper() in ("UP", "UNKNOWN")
        live_addrs = addrs.get(name, [])
        # On prend la premiere IPv4 globale comme primaire.
        primary_cidr = live_addrs[0] if live_addrs else None

        # VLAN ?
        info = link.get("linkinfo", {})
        is_vlan = info.get("info_kind") == "vlan"
        vlan_id = info.get("info_data", {}).get("id") if is_vlan else None
        parent = link.get("link") if is_vlan else None

        existing = db.query(models.Interface).filter_by(name=name).one_or_none()
        if existing is None:
            iface = models.Interface(
                name=name,
                type="vlan" if is_vlan else "physical",
                parent_interface=parent,
                vlan_id=vlan_id,
                ip_mode="static" if primary_cidr else "none",
                ip_address=primary_cidr,
                gateway=None,  # rempli par _adopt_routes
                mtu=mtu,
                enabled=enabled,
                dirty=False,
            )
            db.add(iface)
            log.info("Adopte interface %s (mode=%s, ip=%s)", name, iface.ip_mode, primary_cidr or "-")
        elif primary_cidr and existing.ip_mode == "none":
            # Cas du upgrade : l'iface existe en DB avec ip_mode=none mais
            # le kernel a une IP. On fige l'IP plutot que de la perdre.
            existing.ip_mode = "static"
            existing.ip_address = primary_cidr
            if not existing.mtu:
                existing.mtu = mtu
            existing.enabled = enabled
            existing.dirty = False
            log.info("Mise a niveau interface %s : ip_mode none -> static (%s)", name, primary_cidr)
        else:
            continue
        touched += 1
    db.commit()
    return touched


def _adopt_routes(db: Session) -> int:
    """Cree des rows StaticRoute pour les routes non-connectees du kernel.

    On capture :
    - La/les routes default (`destination=default`) avec leur gateway
    - Les routes statiques non-default (manuel ou DHCP option 121) si
      elles n'ont pas le scope link (les routes 'link' sont auto-derivees
      des IPs sur les interfaces, pas la peine de les persister).

    On met a jour aussi Interface.gateway sur l'interface qui porte la
    default route (utile pour le mode static ulterieur).
    """
    routes = _ip_route_show()
    touched = 0
    iface_by_name = {i.name: i for i in db.query(models.Interface).all()}
    for r in routes:
        dst = r.get("dst") or ""
        gw = r.get("gateway")
        dev = r.get("dev")
        scope = r.get("scope")
        protocol = r.get("protocol")
        # Routes connectees auto (scope=link, protocol=kernel) : ignorees,
        # elles sont implicites une fois l'IP posee sur l'interface.
        if scope == "link" and protocol == "kernel":
            continue
        # Fallback semantique : une route sans gateway (next-hop) est forcement
        # une route connectee (link-scoped) meme si scope/protocol ne sont pas
        # renseignes par ip-route (cas DHCP/manuel sur certaines distros).
        # MurOS ne stocke que les routes a next-hop ; les connectees sont
        # auto-derivees de l'IP d'interface.
        if not gw and dst != "default":
            continue
        # On normalise 'default' (laisse tel quel par ip route).
        if not dst:
            continue
        # Si default avec gateway : note la gateway sur l'interface
        # correspondante (utile en mode static). On NE cree PAS de
        # StaticRoute pour la default : elle est deja materialisee par
        # Interface.gateway au _restore_interfaces. Creer une
        # StaticRoute en plus produirait un doublon de la default route
        # au kernel (une avec metric 0 via l'iface, une avec le metric
        # capture du kernel, typiquement 1002 quand dhclient l'avait
        # posee a l'install).
        if dst == "default":
            if gw and dev and dev in iface_by_name:
                iface = iface_by_name[dev]
                # Capture the kernel default gateway on the interface row
                # regardless of ip_mode. The gateway describes the kernel
                # state and must surface in the UI (Network and Routing
                # pages) even when the interface is DHCP-driven or in
                # "none" mode. Without this, an admin re-importing kernel
                # config sees an empty Routing page despite having a
                # working default route.
                if not iface.gateway:
                    iface.gateway = gw
                    touched += 1
                    log.info("Adopted gateway %s on interface %s", gw, dev)
            continue
        # Cree la StaticRoute si absente.
        exists = (
            db.query(models.StaticRoute)
            .filter_by(destination=dst, gateway=gw)
            .one_or_none()
        )
        if exists is not None:
            continue
        route = models.StaticRoute(
            destination=dst,
            gateway=gw,
            interface_id=iface_by_name[dev].id if dev in iface_by_name else None,
            metric=r.get("metric") or 0,
            enabled=True,
            comment="Adopted at install",
            dirty=False,
        )
        db.add(route)
        touched += 1
        log.info("Adopte route %s via %s dev %s", dst, gw or "-", dev or "-")
    db.commit()
    return touched


def should_adopt(db: Session) -> bool:
    """True si l'adoption n'a pas encore eu lieu.

    Critere : marker absent ET DB sans aucune interface enregistree. Si
    une interface existe deja (cas upgrade depuis un installage MurOS
    anterieur), on ne lance pas l'adoption complete - juste le filet
    interface ip_mode=none + IP active geree dans _adopt_interfaces.
    """
    if ADOPTED_MARKER.exists():
        return False
    has_iface = db.query(models.Interface).first() is not None
    return not has_iface


def adopt_kernel_state(db: Session, force: bool = False) -> dict:
    """Capture l'etat reseau du kernel dans la DB. Idempotent via marker.

    Retourne {interfaces_touched, routes_touched, skipped}.
    """
    if not force and not should_adopt(db):
        log.info("Adoption deja effectuee (marker %s present), skip", ADOPTED_MARKER)
        return {"interfaces_touched": 0, "routes_touched": 0, "skipped": True}
    log.info("=== Adoption de la conf reseau kernel ===")
    n_iface = _adopt_interfaces(db)
    n_route = _adopt_routes(db)
    try:
        ADOPTED_MARKER.parent.mkdir(parents=True, exist_ok=True)
        ADOPTED_MARKER.touch()
    except OSError as exc:
        log.warning("Impossible de creer le marker %s : %s", ADOPTED_MARKER, exc)
    log.info("Adoption terminee : %d interfaces, %d routes", n_iface, n_route)
    return {"interfaces_touched": n_iface, "routes_touched": n_route, "skipped": False}
