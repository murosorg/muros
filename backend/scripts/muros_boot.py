# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
#!/usr/bin/env python3
"""Restaure la config reseau de MurOS au boot.

Execute par `muros-boot.service` (oneshot, avant le backend) :
1. Cree les interfaces VLAN au noyau (ip link add ... type vlan)
2. Applique IP/MTU/state a toutes les interfaces enregistrees
3. Reapplique les routes statiques activees
4. Charge le ruleset nftables compile depuis la DB

C'est la source of truth : la DB SQLite. On ne lit pas de fichiers
intermediaires (sauf le ruleset nftables qui peut deja etre dans
/etc/muros/nftables.conf si l'admin a applique avant un reboot).
On regenere tout depuis la DB pour etre certain d'avoir l'etat voulu.

Ce script est idempotent : peut etre relance a la main sans casser.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Ajoute le repertoire parent (backend/) au sys.path pour pouvoir
# importer `app.*` quand on est lance depuis /opt/muros/backend/scripts/.
SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

# Force MUROS_APPLY=true : si on est dans ce script, c'est qu'on est en prod
# et qu'on veut appliquer pour de vrai.
os.environ["MUROS_APPLY"] = "true"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("muros.boot")


def _restore_vlans(db) -> None:
    """Cree les interfaces VLAN au noyau. Idempotent : si l'interface existe deja, on ignore."""
    from app import models, network
    vlans = db.query(models.Interface).filter(models.Interface.type == "vlan").all()
    for v in vlans:
        if not v.parent_interface or not v.vlan_id:
            log.warning("VLAN %s incomplet (parent/vlan_id manquant), ignore", v.name)
            continue
        if network.link_exists(v.name):
            log.info("VLAN %s deja present au noyau", v.name)
            continue
        rc, msg = network.create_vlan(v.name, v.parent_interface, v.vlan_id)
        if rc == 0:
            log.info("VLAN %s cree (%s.%d)", v.name, v.parent_interface, v.vlan_id)
        else:
            log.error("VLAN %s : ip link add a echoue : %s", v.name, msg)


def _purge_foreign_network_state() -> None:
    """Defensive cleanup of state left by other network stacks.

    MurOS is the sole control plane (postinst masks systemd-networkd,
    networking.service, dhcpcd, dhclient, systemd-resolved). But on an
    ISO that was upgraded from a vanilla Debian, or if an admin manually
    re-enabled one of those units, parasite state can survive a reboot
    and race muros-boot :
      - a dhclient process still alive holding a lease,
      - default routes added by ifupdown's dhclient (typical metric 1002
        = 1000 + ifindex), or by systemd-networkd's DHCP (metric 1024),
      - DHCP-proto routes the kernel keeps until lease expiry.

    We kill stray DHCP clients and flush routes tagged proto=dhcp/boot,
    so that _restore_interfaces and _restore_routes start from a clean
    slate. Safe to run unconditionally : if nothing matches, nothing
    happens. Static routes (proto static / kernel) are NOT touched.
    """
    import subprocess
    for proc in ("dhclient", "dhcpcd"):
        subprocess.run(["pkill", "-x", proc], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for proto in ("dhcp", "boot", "ra"):
        res = subprocess.run(
            ["ip", "route", "flush", "proto", proto],
            capture_output=True, text=True, timeout=5,
        )
        # Non-zero is fine when there is nothing to flush.
        if res.returncode == 0 and res.stdout.strip():
            log.info("Flushed routes proto=%s : %s", proto, res.stdout.strip())


def _restore_interfaces(db) -> None:
    """Applique IP/MTU/state pour toutes les interfaces.

    Filet de securite : si une interface est en ip_mode='none' mais a
    une IP active au noyau (typiquement IP donnee par DHCP a l'install
    et pas encore figee en DB), on LOG en ERROR pour que l'admin voie
    immediatement le pourquoi de la perte d'acces dans journalctl.
    """
    from app import models, network
    from app.system import list_system_interfaces

    # Snapshot des IPs vivantes AVANT modification, pour detection
    # "j'allais effacer une IP active".
    live = {sif["name"]: sif["addresses"] for sif in list_system_interfaces()}

    ifaces = db.query(models.Interface).all()
    for i in ifaces:
        live_addrs = live.get(i.name, [])
        # Filtre link-local et IPv6
        live_global = [
            a for a in live_addrs
            if not a.startswith("169.254.")
            and not a.startswith("127.")
            and ":" not in a.split("/", 1)[0]
        ]
        if i.ip_mode == "none" and live_global:
            log.error(
                "Interface %s : ip_mode=none en DB MAIS le noyau a une IP "
                "active %s. Cette IP va etre EFFACEE. Si vous perdez "
                "l'acces : connectez-vous en console et lancez 'sudo "
                "muros-import-ip %s' OU bien depuis l'UI Interfaces, "
                "cliquez sur 'Importer l'IP active' pour figer cette "
                "config en mode static.",
                i.name, live_global, i.name,
            )
        try:
            errors = network.apply_interface_config(
                i.name,
                ip_mode=i.ip_mode,
                ip_address=i.ip_address,
                gateway=i.gateway,
                mtu=i.mtu,
                enabled=i.enabled,
            )
            if errors:
                for e in errors:
                    log.warning("Interface %s : %s", i.name, e)
            else:
                log.info("Interface %s : conf appliquee", i.name)
        except ValueError as exc:
            log.error("Interface %s : %s", i.name, exc)


def _migrate_drop_default_static_routes(db) -> None:
    """One-shot cleanup of parasite default StaticRoute rows.

    Earlier MurOS versions (<= 2026.05) captured the default route both
    as Interface.gateway AND as a StaticRoute(destination='default') at
    adoption time. The kernel then ended up with two default routes at
    every boot : one via Interface.gateway (metric 0) and one via the
    StaticRoute (typically metric 1002 inherited from dhclient at
    install). The default route is now exclusively represented by
    Interface.gateway, so we drop any leftover row here. Idempotent.
    """
    from app import models
    rows = (
        db.query(models.StaticRoute)
        .filter(models.StaticRoute.destination == "default")
        .all()
    )
    if not rows:
        return
    for r in rows:
        db.delete(r)
    db.commit()
    log.info(
        "Migration : %d default StaticRoute parasite(s) supprimee(s) "
        "(default route portee par Interface.gateway)", len(rows),
    )


def _restore_routes(db) -> None:
    """Rejoue les routes statiques activees."""
    from app import routing
    _migrate_drop_default_static_routes(db)
    routing.apply_all_routes(db)
    log.info("Routes statiques rejouees")


def _restore_nftables(db) -> None:
    """Compile et charge le ruleset nftables."""
    from app import compiler, apply
    ruleset = compiler.compile_ruleset(db)
    ok, msg = apply.manager.check(ruleset)
    if not ok:
        log.error("Ruleset nftables invalide : %s", msg)
        return
    # On charge directement via `nft -f` (pas de timer de rollback ici, on
    # est au boot, l'admin n'est pas devant l'ecran).
    import subprocess
    res = subprocess.run(["nft", "-f", "-"], input=ruleset, capture_output=True, text=True, timeout=15)
    if res.returncode == 0:
        log.info("Ruleset nftables charge")
    else:
        log.error("Echec nft -f : %s", (res.stderr or res.stdout).strip())


def _restore_wireguard(db) -> None:
    """Reecrit /etc/wireguard/<iface>.conf et monte l'interface si activee."""
    from app import wireguard, models, service_dirty
    cfg = db.get(models.WireGuardConfig, 1)
    if cfg is None or not cfg.enabled:
        return
    peers = db.query(models.WireGuardPeer).order_by(models.WireGuardPeer.id).all()
    try:
        res = wireguard.apply_config(cfg, peers, defer_start=True)
        log.info("WireGuard restaure : %s", res.get("message", ""))
        # On-disk conf has been rewritten and the tunnel restarted, so
        # any pending Save before the reboot has now landed. Clear the
        # dirty flag to avoid a phantom orange dot in the UI.
        service_dirty.mark_clean(db, "wireguard", summary="muros-boot restore")
    except Exception as exc:  # noqa: BLE001
        log.warning("Echec restauration WireGuard : %s", exc)


def _restore_ipsec(db) -> None:
    """Reecrit /etc/swanctl/conf.d/muros.conf + secrets + PKI et reload."""
    from app import ipsec, models
    global_cfg = ipsec.get_or_create_global_config(db)
    conns = db.query(models.IpsecConnection).filter_by(enabled=True).all()
    ca = db.get(models.IpsecCa, 1)
    if ca is not None and not ca.cert_pem:
        ca = None
    certs = db.query(models.IpsecCert).order_by(models.IpsecCert.id).all()
    revoked = [c for c in certs if c.revoked]
    if not conns and not certs and global_cfg.enabled:
        return
    from app import service_dirty
    try:
        res = ipsec.apply_config(conns, ca=ca, certs=certs,
                                 revoked_certs=revoked, defer_start=True,
                                 globally_enabled=global_cfg.enabled)
        log.info("IPsec restaure : %s", res.get("message", ""))
        service_dirty.mark_clean(db, "ipsec", summary="muros-boot restore")
    except Exception as exc:  # noqa: BLE001
        log.warning("Echec restauration IPsec : %s", exc)


def _restore_ha(db) -> None:
    """Ecrit les conf keepalived/conntrackd et (re)demarre les services HA."""
    from app import ha, models
    cfg = db.get(models.HaConfig, 1)
    if cfg is None:
        return
    if not cfg.enabled:
        # HA desactivee : on s'assure que les services sont stoppes.
        ha._stop_services()
        for p in (ha.KEEPALIVED_CONF, ha.CONNTRACKD_CONF):
            if p.exists():
                p.unlink()
        log.info("HA desactivee, services stoppes")
        return
    # Garde-fou : HA marquee enabled mais conf incomplete (peer/sync iface
    # non remplis). On evite d'appeler apply_config qui lancerait `ip dev ""`
    # et generait du bruit "Device \"\" does not exist." dans le journal.
    # L'UI doit forcer la saisie de ces champs avant d'activer HA, mais une
    # DB issue d'une release plus vieille peut presenter cet etat.
    missing = []
    if not (cfg.peer_address or "").strip():
        missing.append("peer_address")
    if not (cfg.sync_interface or "").strip():
        missing.append("sync_interface")
    if missing:
        log.warning(
            "HA activee mais champs requis manquants (%s), restauration "
            "ignoree. Completer la conf depuis l'UI puis appliquer.",
            ", ".join(missing),
        )
        return
    vips = db.query(models.HaVip).order_by(models.HaVip.vrid).all()
    cfg_dict = {
        "enabled": cfg.enabled, "role": cfg.role,
        "peer_address": cfg.peer_address, "sync_interface": cfg.sync_interface,
        "conntrack_sync": cfg.conntrack_sync, "preempt": cfg.preempt,
    }
    vips_dict = [
        {
            "vrid": v.vrid, "interface": v.interface, "vip_cidr": v.vip_cidr,
            "auth_pass": v.auth_pass, "priority": v.priority,
            "description": v.description, "enabled": v.enabled,
        }
        for v in vips
    ]
    try:
        res = ha.apply_config(cfg_dict, vips_dict, defer_start=True)
        log.info("HA restauree : %s", res.get("message", ""))
    except Exception as exc:  # noqa: BLE001
        log.warning("Echec restauration HA : %s", exc)


def _restore_watcher(db) -> None:
    """Reconcile muros-watcher.service against NotificationConfig.enabled.

    Unlike WireGuard / IPsec / HA, the watcher has no on-disk config:
    it reads everything from the SQLite DB at runtime. Its boot
    persistence therefore relies entirely on the systemd `enable`
    symlink, which is created by the PUT /api/notifications/config
    route the first time the operator flips the toggle.

    In practice that symlink can go missing in legitimate scenarios:
      - the .deb postinst running deb-systemd-helper after a package
        upgrade and rewriting the enable state,
      - a previous install where the unit was masked,
      - the systemctl enable --now call exiting non-zero (logged as a
        WARNING by the route, but non blocking for the Save).
    When that happens the watcher silently stays down after a reboot
    even though the UI shows "Notifications enabled".

    We close the loop here, same way _restore_ha does for keepalived:
    treat the DB as the source of truth and reconcile both is-enabled
    (symlink for boot) and is-active (current running state).
    """
    import subprocess
    from app import models
    cfg = db.get(models.NotificationConfig, 1)
    enabled = bool(cfg and cfg.enabled)
    unit = "muros-watcher.service"

    def _run(args: list[str]) -> tuple[int, str]:
        r = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return r.returncode, (r.stderr or r.stdout or "").strip()

    if enabled:
        # `enable --now` is idempotent: it creates the symlink if
        # missing and starts the unit if not already running. We log
        # the outcome so the boot journal records the reconcile.
        rc, msg = _run(["systemctl", "enable", "--now", unit])
        if rc == 0:
            log.info("Watcher restored: enabled and started (%s)", unit)
        else:
            log.warning("Watcher reconcile (enable --now) failed rc=%s: %s",
                        rc, msg)
    else:
        rc, msg = _run(["systemctl", "disable", "--now", unit])
        if rc == 0:
            log.info("Watcher disabled per DB state (%s)", unit)
        else:
            # Disabling an already-disabled unit returns 0, so a non
            # zero here usually means the unit is masked or missing;
            # not fatal at boot, only logged.
            log.warning("Watcher reconcile (disable --now) failed rc=%s: %s",
                        rc, msg)


def _restore_ntp(db) -> None:
    """Reconcile the chrony drop-in (server list + server mode).

    chrony server mode emits ``allow all``; exposure is controlled at the
    firewall. Re-applying here ensures NTP server mode survives a reboot.
    """
    from app import ntp
    try:
        ntp.apply_config(db)
        if ntp.get_config(db).serve_lan:
            log.info("NTP restored (server mode on, exposure gated by firewall)")
        else:
            log.info("NTP restored (client mode, not serving)")
    except Exception as exc:  # noqa: BLE001
        log.warning("NTP reconcile failed: %r", exc)


def main() -> int:
    from app.db import SessionLocal, init_db
    from app import adoption
    log.info("=== Restauration de la config reseau MurOS ===")
    # Cree les tables SQLite si elles n'existent pas encore (cas premiere
    # installation : le backend FastAPI n'a pas encore demarre, donc son
    # lifespan n'a pas appele init_db). Sans ca, le premier SELECT plante
    # avec "no such table: interfaces".
    init_db()
    with SessionLocal() as db:
        try:
            # Premiere etape : si la DB est vide (premiere installation),
            # on aspire l'etat reseau actuel du kernel (interfaces, IPs,
            # default route) et on le persiste. Sans ca, le rejeu d'une
            # DB vide ecraserait la conf DHCP initiale -> perte de reseau.
            result = adoption.adopt_kernel_state(db)
            if not result["skipped"]:
                log.info(
                    "Adoption initiale : %d interface(s), %d route(s) capturees du kernel",
                    result["interfaces_touched"], result["routes_touched"],
                )
            # Seed des zones par defaut + regles d acces admin permissives
            # AVANT _restore_nftables, sinon le ruleset compile est vide et
            # le default drop sur input bloque SSH/HTTPS. Doit tourner ici
            # plutot que dans le lifespan FastAPI, car muros-boot.service
            # est ordonne AVANT muros-backend.service.
            from app.seed import seed_if_empty
            seed_if_empty(db)
            _purge_foreign_network_state()
            _restore_vlans(db)
            _restore_interfaces(db)
            _restore_routes(db)
            _restore_nftables(db)
            _restore_wireguard(db)
            _restore_ipsec(db)
            _restore_ha(db)
            _restore_watcher(db)
            _restore_ntp(db)
        except Exception:
            log.exception("Echec lors de la restauration")
            return 1
    log.info("=== Restauration terminee ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
