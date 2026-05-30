# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app import lockout_guard, models, schemas
from app.apply import manager as apply_manager
from app.audit import _client_ip
from app.auth import current_user
from app.compiler import compile_ruleset
from app.db import SessionLocal, get_db
from app.routing import apply_route
from app.system import list_system_interfaces

_auth_dep = [Depends(current_user)]


def _get_apply_state(db: Session) -> models.FirewallApplyState:
    """Lazy-creates the singleton row that tracks 'global' firewall dirty.

    Used to flag pending state in cases the per-row dirty flag misses:
    deletion of the last rule of a chain (no sibling to flag), deletion
    of an unreferenced zone, deletion of the last NAT rule, etc.
    """
    state = db.get(models.FirewallApplyState, 1)
    if state is None:
        state = models.FirewallApplyState(id=1, dirty=False)
        db.add(state)
        db.flush()
    return state


def mark_firewall_dirty(db: Session) -> None:
    """Sets the global firewall apply singleton dirty=True.

    Caller commits the session. Idempotent and cheap.
    """
    _get_apply_state(db).dirty = True


# --- Zones ---

zones_router = APIRouter(prefix="/api/zones", tags=["zones"], dependencies=_auth_dep)


@zones_router.get("", response_model=list[schemas.ZoneOut])
def list_zones(db: Session = Depends(get_db)):
    return db.query(models.Zone).order_by(models.Zone.id).all()


@zones_router.post("", response_model=schemas.ZoneOut, status_code=status.HTTP_201_CREATED)
def create_zone(data: schemas.ZoneCreate, db: Session = Depends(get_db)):
    if db.query(models.Zone).filter(models.Zone.name == data.name).first():
        raise HTTPException(409, f"Zone '{data.name}' already exists")
    zone = models.Zone(**data.model_dump(), dirty=True)
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return zone


@zones_router.put("/{zone_id}", response_model=schemas.ZoneOut)
def update_zone(zone_id: int, data: schemas.ZoneUpdate, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        raise HTTPException(404, "Zone not found")
    changed = False
    for k, v in data.model_dump(exclude_unset=True).items():
        if getattr(zone, k) != v:
            setattr(zone, k, v)
            changed = True
    if changed:
        zone.dirty = True
    db.commit()
    db.refresh(zone)
    return zone


@zones_router.delete("/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_zone(zone_id: int, db: Session = Depends(get_db)):
    zone = db.get(models.Zone, zone_id)
    if not zone:
        raise HTTPException(404, "Zone not found")
    # Deletion is a kernel-affecting change; mark a global marker so the
    # pending counter reflects it. We do that by bumping another zone's
    # dirty bit if any exists, otherwise rely on the rules/NAT count
    # being already dirty for the same ruleset reload.
    db.delete(zone)
    # Cascade: rules pointing to this zone will see src/dst_zone_id NULL,
    # which is a semantic change in the ruleset. Flag those rules dirty.
    db.query(models.FirewallRule).filter(
        (models.FirewallRule.src_zone_id == zone_id)
        | (models.FirewallRule.dst_zone_id == zone_id)
    ).update({"dirty": True}, synchronize_session=False)
    # Global flag covers the case where the deleted zone wasn't referenced
    # by any rule: nothing got dirty-flagged otherwise, the kernel still
    # has the old zone in its named sets, the admin must Apply.
    mark_firewall_dirty(db)
    db.commit()


# --- Interfaces ---
interfaces_router = APIRouter(prefix="/api/interfaces", tags=["interfaces"], dependencies=_auth_dep)


@interfaces_router.get("/system", response_model=list[schemas.SystemInterfaceOut])
def list_system_ifaces():
    """List the interfaces detected on the system (read-only, via ip -j)."""
    return list_system_interfaces()


_LAST_ADOPTION_SWEEP: float = 0.0
_ADOPTION_SWEEP_TTL = 30.0  # seconds


def _sweep_new_interfaces(db: Session) -> None:
    """Discover kernel netdevs that are not yet in the MurOS DB and adopt
    them with dirty=False. Throttled to once every _ADOPTION_SWEEP_TTL
    seconds to keep GET /api/interfaces snappy. Errors are swallowed:
    listing must always succeed even if the kernel scan blows up.

    Rationale: MurOS' UI promises that "what you see in MurOS is what
    is on the box". If an admin plugs in a new NIC after install, it
    should show up in /network without requiring an explicit "Import"
    click. The dashboard tile "Interfaces UP" matches the Network
    page row count as a result.
    """
    import time
    from app import adoption

    global _LAST_ADOPTION_SWEEP
    now = time.monotonic()
    if now - _LAST_ADOPTION_SWEEP < _ADOPTION_SWEEP_TTL:
        return
    _LAST_ADOPTION_SWEEP = now
    try:
        adoption._adopt_interfaces(db)
    except Exception:
        # Best-effort sweep; never break the listing endpoint.
        db.rollback()


@interfaces_router.get("", response_model=list[schemas.InterfaceOut])
def list_interfaces(db: Session = Depends(get_db)):
    _sweep_new_interfaces(db)
    return db.query(models.Interface).order_by(models.Interface.id).all()


@interfaces_router.post("", response_model=schemas.InterfaceOut, status_code=status.HTTP_201_CREATED)
def create_interface(data: schemas.InterfaceCreate, db: Session = Depends(get_db)):
    from app import network
    if db.query(models.Interface).filter(models.Interface.name == data.name).first():
        raise HTTPException(409, f"Interface '{data.name}' already exists")
    if data.zone_id and not db.get(models.Zone, data.zone_id):
        raise HTTPException(400, "invalid zone_id")

    # Validation VLAN avant insertion DB
    if data.type == "vlan":
        try:
            network.validate_vlan_params(data.name, data.parent_interface, data.vlan_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
    else:
        # Pour une interface physique, parent et vlan_id n'ont pas de sens
        if data.parent_interface or data.vlan_id:
            raise HTTPException(400, "parent_interface et vlan_id sont reserves au type 'vlan'")

    iface = models.Interface(**data.model_dump())
    iface.dirty = True  # apply manuel via POST /api/network/apply
    db.add(iface)
    db.commit()
    db.refresh(iface)
    return iface


@interfaces_router.put("/{iface_id}", response_model=schemas.InterfaceOut)
def update_interface(
    iface_id: int,
    data: schemas.InterfaceUpdate,
    response: Response,
    db: Session = Depends(get_db),
):
    iface = db.get(models.Interface, iface_id)
    if not iface:
        raise HTTPException(404, "Interface not found")
    payload = data.model_dump(exclude_unset=True)
    if "zone_id" in payload and payload["zone_id"] is not None:
        if not db.get(models.Zone, payload["zone_id"]):
            raise HTTPException(400, "invalid zone_id")

    # If the IP/MTU/state/parent config REALLY changes, we mark dirty for a
    # manual apply. Otherwise (the admin re-submits the form without changing
    # anything, or reverts to the initial value), we leave dirty as-is so as
    # not to artificially inflate the pending counter.
    dirty_keys = ("ip_mode", "ip_address", "gateway", "mtu", "enabled", "parent_interface", "vlan_id")
    changed = False
    for k, v in payload.items():
        if k in dirty_keys and getattr(iface, k) != v:
            changed = True
        setattr(iface, k, v)
    if changed:
        iface.dirty = True
    db.commit()
    db.refresh(iface)
    return iface


@interfaces_router.post("/{iface_id}/import-current", response_model=schemas.InterfaceOut)
def import_current_ip(iface_id: int, db: Session = Depends(get_db)):
    """Aspire l'IP et la gateway actuellement actives sur l'interface,
    et les fige en mode 'static' dans la DB MurOS.

    Cas d'usage typique : une appliance fraichement installee a recu
    son IP via DHCP. Apres le premier reboot, muros-boot ecraserait
    cette IP (ip_mode='none' en DB par defaut sur les vieux installs)
    et l'admin perdrait l'acces. Ce bouton recupere l'IP en cours et
    la persiste en DB AVANT que ca se produise.

    Marque dirty=False : pas besoin de re-appliquer, l'IP EST deja sur
    l'interface, on ne fait que documenter ce qui est en cours.
    """
    from app.system import list_system_interfaces, get_default_gateway
    iface = db.get(models.Interface, iface_id)
    if not iface:
        raise HTTPException(404, "Interface not found")
    live = next(
        (s for s in list_system_interfaces() if s["name"] == iface.name),
        None,
    )
    if not live:
        raise HTTPException(404, f"Interface '{iface.name}' absente du noyau")
    cidr: str | None = None
    for addr in live["addresses"]:
        ip_part = addr.split("/", 1)[0]
        if ":" in ip_part or ip_part.startswith(("169.254.", "127.")):
            continue
        cidr = addr
        break
    if not cidr:
        raise HTTPException(
            400,
            f"Aucune IPv4 globale trouvee sur '{iface.name}'. "
            "Configurez l'IP manuellement.",
        )
    iface.ip_mode = "static"
    iface.ip_address = cidr
    iface.gateway = get_default_gateway(iface.name)
    if live["mtu"] and not iface.mtu:
        iface.mtu = live["mtu"]
    iface.enabled = live["state"] != "DOWN"
    # We do NOT mark dirty: the IP is already applied to the kernel, we only
    # record the state in the DB. If the admin wants to change it later, the
    # normal apply flow will pick up from there.
    iface.dirty = False
    db.commit()
    db.refresh(iface)
    return iface


@interfaces_router.delete("/{iface_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_interface(iface_id: int, db: Session = Depends(get_db)):
    iface = db.get(models.Interface, iface_id)
    if not iface:
        raise HTTPException(404, "Interface not found")
    # VLAN: deferred deletion for symmetry with the add (which is applied to
    # the kernel only at POST /apply). We mark pending_delete + dirty so the
    # Apply catches the change, and we keep the row in the DB until the admin
    # clicks Apply (or Cancel deletion).
    if iface.type == "vlan":
        iface.pending_delete = True
        iface.dirty = True
        db.commit()
        return
    # Physical interface: no kernel delete possible (ip link del refuses
    # physical NICs), we only remove the MurOS tracking. Before detaching, we
    # clean up the IPs that MurOS had set, otherwise they stay on the kernel
    # as ghost addresses until the next reboot, which is misleading for the
    # admin (the "deleted" interface keeps responding).
    #   - static mode: ip addr flush dev <iface> is enough.
    #   - dhcp mode  : dhclient runs as a daemon; a plain flush would be
    #                  overwritten immediately by the lease. We first send
    #                  dhclient -r to release the lease and kill the daemon,
    #                  then flush for the rest.
    # We do not touch the admin link up/down state: leaving the NIC up is
    # consistent with the physical reality of the cable.
    if iface.ip_mode in ("static", "dhcp") and iface.name:
        from app import network
        try:
            if iface.ip_mode == "dhcp":
                network.dhcp_release(iface.name)
            network.flush_addresses(iface.name)
        except ValueError:
            # Invalid interface name: we do not block the DB delete.
            pass
    db.delete(iface)
    db.commit()


@interfaces_router.post("/{iface_id}/cancel-delete", response_model=schemas.InterfaceOut)
def cancel_interface_delete(iface_id: int, db: Session = Depends(get_db)):
    """Annule un delete VLAN en attente d'apply.

    Si l'interface est marquee pending_delete=True mais l'Apply n'a pas
    encore eu lieu, l'admin peut revenir en arriere. dirty repasse a False
    car la conf en DB correspond toujours a ce qui est sur le noyau.
    """
    iface = db.get(models.Interface, iface_id)
    if not iface:
        raise HTTPException(404, "Interface not found")
    if not iface.pending_delete:
        raise HTTPException(409, "Interface is not pending deletion")
    iface.pending_delete = False
    iface.dirty = False
    db.commit()
    db.refresh(iface)
    return iface


# --- Reseau : apply groupe des changes en attente ---
network_router = APIRouter(prefix="/api/network", tags=["network"], dependencies=_auth_dep)


@network_router.post("/adopt", response_model=schemas.NetworkAdoptResult)
def network_adopt(db: Session = Depends(get_db)):
    """Capture the kernel's current network config into the MurOS DB.

    Use case: appliance installed on a machine already in production, or
    recovery after a mistake. Idempotent via the .adopted marker, but called
    here we force it to allow a deliberate re-adoption by the admin (useful
    if the kernel has a new hot-added iface).
    """
    from app import adoption
    result = adoption.adopt_kernel_state(db, force=True)
    return {
        "interfaces_touched": result["interfaces_touched"],
        "routes_touched": result["routes_touched"],
        "skipped": result["skipped"],
    }


@network_router.get("/environment", response_model=schemas.NetworkEnvironmentOut)
def network_environment():
    """Diagnose the network environment to warn if a competing manager runs
    in parallel with MurOS.

    If NetworkManager or systemd-networkd is active, any IP change pushed by
    MurOS will be overwritten a few seconds later. This is the typical case
    on an Ubuntu / Fedora development machine.
    """
    from app import network
    return {
        "apply_enabled": network.APPLY_ENABLED,
        "competing_managers": network.detect_competing_managers(),
    }


@network_router.get("/pending", response_model=schemas.NetworkPendingOut)
def network_pending(db: Session = Depends(get_db)):
    """List the network changes not yet applied to the kernel.

    Includes interfaces and static routes with dirty=True.
    """
    ifaces = (
        db.query(models.Interface)
        .filter(models.Interface.dirty == True)  # noqa: E712
        .all()
    )
    routes = (
        db.query(models.StaticRoute)
        .filter(models.StaticRoute.dirty == True)  # noqa: E712
        .all()
    )
    return {
        "count": len(ifaces) + len(routes),
        "interfaces": [
            {
                "id": i.id,
                "name": i.name,
                "type": i.type,
                "ip_mode": i.ip_mode,
                "ip_address": i.ip_address,
                "pending_delete": i.pending_delete,
            }
            for i in ifaces
        ],
        "routes": [
            {"id": r.id, "destination": r.destination, "gateway": r.gateway, "metric": r.metric}
            for r in routes
        ],
    }


@network_router.post("/apply")
def network_apply(db: Session = Depends(get_db)):
    """Apply all pending interface/route changes to the kernel.

    Creates a single safe_apply.manager.register for a global rollback if the
    admin does not confirm within the timeout (60s by default). The rollback
    restores the kernel state snapshotted before the apply.
    """
    from app import network, safe_apply
    from app.routing import apply_route as apply_route_kernel

    dirty_ifaces = (
        db.query(models.Interface)
        .filter(models.Interface.dirty == True)  # noqa: E712
        .all()
    )
    dirty_routes = (
        db.query(models.StaticRoute)
        .filter(models.StaticRoute.dirty == True)  # noqa: E712
        .all()
    )

    if not dirty_ifaces and not dirty_routes:
        return {"applied": False, "message": "No pending network change.", "pending_id": None}

    # Snapshot the kernel BEFORE, for rollback
    iface_snapshots: list[dict] = []
    for iface in dirty_ifaces:
        snap = network.snapshot_interface(iface.name)
        iface_snapshots.append({"name": iface.name, "snapshot": snap, "iface_id": iface.id})

    # For routes: we do not snapshot the kernel (too complex), we just invert
    # the operations on rollback (del if we added, and vice versa). We keep
    # the pre-DB state to be able to revert.
    route_actions: list[dict] = []

    errors: list[str] = []

    # Apply interfaces
    deleted_ifaces: list[str] = []
    for iface in dirty_ifaces:
        # Pending delete: remove from the kernel (VLAN only, physical ones
        # do not follow this path, cf delete_interface) then drop the DB row.
        # Skip the whole IP configuration phase.
        if iface.pending_delete:
            if iface.type == "vlan":
                rc, msg = network.delete_interface(iface.name)
                if rc != 0 and "does not exist" not in msg.lower() \
                        and "cannot find device" not in msg.lower():
                    errors.append(f"VLAN {iface.name} delete: {msg}")
                    continue
            deleted_ifaces.append(iface.name)
            db.delete(iface)
            continue
        # VLAN: create the link if not already there
        if iface.type == "vlan":
            rc, msg = network.create_vlan(iface.name, iface.parent_interface, iface.vlan_id)
            if rc != 0 and "exists" not in msg.lower():
                errors.append(f"VLAN {iface.name} : {msg}")
                continue
        try:
            err_list = network.apply_interface_config(
                iface.name,
                ip_mode=iface.ip_mode,
                ip_address=iface.ip_address,
                gateway=iface.gateway,
                mtu=iface.mtu,
                enabled=iface.enabled,
            )
            if err_list:
                errors.extend([f"{iface.name} : {e}" for e in err_list])
        except ValueError as exc:
            errors.append(f"{iface.name} : {exc}")
            continue
        iface.dirty = False

    # Apply routes (replace = add ou update)
    for route in dirty_routes:
        if route.enabled:
            try:
                apply_route_kernel(route, "replace")
                route_actions.append({"id": route.id, "action": "replaced"})
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Route {route.destination} : {exc}")
                continue
        route.dirty = False

    db.commit()

    # Snapshot summary pour le pending change
    descr_parts = []
    if dirty_ifaces:
        descr_parts.append(f"{len(dirty_ifaces)} interface(s)")
    if dirty_routes:
        descr_parts.append(f"{len(dirty_routes)} route(s)")
    description = f"Network changes applied: {', '.join(descr_parts)}"

    # List of applied ids to re-mark dirty in case of rollback: the rollback
    # restores the kernel but the DB config stays the new one, so we set
    # dirty=True again so the Apply button becomes active again and the admin
    # can re-push or adjust their change.
    applied_iface_ids = [iface.id for iface in dirty_ifaces]
    applied_route_ids = [r.id for r in dirty_routes]

    def _rollback() -> None:
        # Restore the kernel for each interface
        for snap_data in iface_snapshots:
            try:
                network.restore_interface(snap_data["snapshot"])
            except Exception:  # noqa: BLE001
                pass
        # For routes: remove the ones we added
        with SessionLocal() as db2:
            for action in route_actions:
                row = db2.get(models.StaticRoute, action["id"])
                if row:
                    try:
                        apply_route_kernel(row, "del")
                    except Exception:  # noqa: BLE001
                        pass
            # Re-marquer comme pending : DB != noyau a nouveau
            for iid in applied_iface_ids:
                row = db2.get(models.Interface, iid)
                if row:
                    row.dirty = True
            for rid in applied_route_ids:
                row = db2.get(models.StaticRoute, rid)
                if row:
                    row.dirty = True
            db2.commit()

    # Enriched detail: we include the IPs (without the CIDR prefix) applied on
    # each interface so the rollback modal can offer a concrete reconnection
    # URL ("open https://NEW_IP:443 in another tab to confirm"). This is the
    # info most often missing when the admin changes the management IP.
    iface_ips: list[str] = []
    for iface in dirty_ifaces:
        if iface.ip_mode == "static" and iface.ip_address:
            # ip_address is in the form "10.0.0.1/24", we keep just the IP
            iface_ips.append(iface.ip_address.split("/", 1)[0])

    change = safe_apply.manager.register(
        kind="interface",
        description=description,
        rollback_fn=_rollback,
        detail={
            "interfaces": [i.name for i in dirty_ifaces],
            "routes": [r.destination for r in dirty_routes],
            "new_ips": iface_ips,
        },
    )

    return {
        "applied": True,
        "message": description + (f" (with {len(errors)} error(s))" if errors else ""),
        "errors": errors,
        "pending_id": change.id,
    }


# --- Firewall rules ---
firewall_router = APIRouter(prefix="/api/firewall", tags=["firewall"], dependencies=_auth_dep)


@firewall_router.get("/rules", response_model=list[schemas.FirewallRuleOut])
def list_rules(db: Session = Depends(get_db)):
    return (
        db.query(models.FirewallRule)
        .order_by(models.FirewallRule.chain, models.FirewallRule.position, models.FirewallRule.id)
        .all()
    )


@firewall_router.post("/rules", response_model=schemas.FirewallRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(data: schemas.FirewallRuleCreate, db: Session = Depends(get_db)):
    rule = models.FirewallRule(**data.model_dump(), dirty=True)
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


@firewall_router.put("/rules/{rule_id}", response_model=schemas.FirewallRuleOut)
def update_rule(rule_id: int, data: schemas.FirewallRuleUpdate, db: Session = Depends(get_db)):
    rule = db.get(models.FirewallRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    # Only flag dirty when a value actually changes. A form re-submit
    # with identical content must not bump the pending counter.
    changed = False
    for k, v in data.model_dump(exclude_unset=True).items():
        if getattr(rule, k) != v:
            setattr(rule, k, v)
            changed = True
    # Enforce the chain/zone invariant on the resulting rule: the firewall
    # itself is a fixed endpoint on input (no destination zone) and output
    # (no source zone). Clear the stale zone even if the client did not
    # touch it (e.g. the chain changed but a zone was left behind).
    if rule.chain == "input" and rule.dst_zone_id is not None:
        rule.dst_zone_id = None
        changed = True
    elif rule.chain == "output" and rule.src_zone_id is not None:
        rule.src_zone_id = None
        changed = True
    if changed:
        rule.dirty = True
    db.commit()
    db.refresh(rule)
    return rule


@firewall_router.delete("/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = db.get(models.FirewallRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    db.delete(rule)
    # The kernel ruleset still contains the rule until next Apply. Flag
    # the global apply singleton: it covers both the common case (rules
    # remain in the chain) and the edge case where we just removed the
    # last rule of any chain (no sibling to flag with the per-row dirty).
    mark_firewall_dirty(db)
    db.commit()


@firewall_router.post("/rules/reorder", response_model=list[schemas.FirewallRuleOut])
def reorder_rules(
    payload: schemas.FirewallReorderIn,
    db: Session = Depends(get_db),
):
    """Renumber a chain's positions in multiples of 10.

    After drag-and-drop in the UI, the front sends the desired order as a
    list of IDs (rule_ids) for a given chain. We reassign position = 10, 20,
    30... for each rule in that order.

    The "catch-all" rules (position >= 900) are preserved at their current
    position: we do not renumber them, they stay at the end of the chain.
    """
    chain = payload.chain
    if chain not in ("input", "forward", "output"):
        raise HTTPException(400, "chain must be 'input', 'forward' or 'output'")
    requested_ids = list(payload.rule_ids)
    # Toutes les regles non catch-all de la chaine, mappees par id.
    existing = (
        db.query(models.FirewallRule)
        .filter(
            models.FirewallRule.chain == chain,
            models.FirewallRule.position < 900,
        )
        .all()
    )
    by_id = {r.id: r for r in existing}
    if set(requested_ids) != set(by_id.keys()):
        raise HTTPException(
            400,
            "rule_ids must be exactly the non-catch-all rules of the chain "
            f"({sorted(by_id.keys())} expected, got {sorted(requested_ids)})",
        )
    for index, rid in enumerate(requested_ids):
        new_pos = (index + 1) * 10
        if by_id[rid].position != new_pos:
            by_id[rid].position = new_pos
            by_id[rid].dirty = True
    db.commit()
    # Return: the full list of the chain (with catch-all), sorted.
    out = (
        db.query(models.FirewallRule)
        .filter(models.FirewallRule.chain == chain)
        .order_by(models.FirewallRule.position, models.FirewallRule.id)
        .all()
    )
    return out


@firewall_router.post("/rules/{rule_id}/move", response_model=schemas.FirewallRuleOut)
def move_rule(
    rule_id: int,
    direction: str,
    db: Session = Depends(get_db),
):
    """Move a rule one notch up or down in its chain.

    Standard stateful firewall convention: rules are evaluated IN ORDER,
    so the admin must be able to reorder with two simple "Up" / "Down"
    buttons on each row. We just swap the position with the adjacent rule IN
    THE SAME CHAIN.

    direction = "up" decreases the position (moves up), "down" increases it.
    NOP if the rule is already at the edge of the chain.
    """
    if direction not in ("up", "down"):
        raise HTTPException(400, "direction must be 'up' or 'down'")
    rule = db.get(models.FirewallRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    # Find the adjacent rule in the same chain
    if direction == "up":
        neighbor = (
            db.query(models.FirewallRule)
            .filter(
                models.FirewallRule.chain == rule.chain,
                models.FirewallRule.position < rule.position,
            )
            .order_by(models.FirewallRule.position.desc())
            .first()
        )
    else:
        neighbor = (
            db.query(models.FirewallRule)
            .filter(
                models.FirewallRule.chain == rule.chain,
                models.FirewallRule.position > rule.position,
            )
            .order_by(models.FirewallRule.position.asc())
            .first()
        )
    if neighbor is None:
        # Deja en haut ou en bas, no-op
        return rule
    rule.position, neighbor.position = neighbor.position, rule.position
    db.commit()
    db.refresh(rule)
    return rule


@firewall_router.get("/preview", response_model=schemas.RulesetPreview)
def preview_ruleset(db: Session = Depends(get_db)):
    return schemas.RulesetPreview(ruleset=compile_ruleset(db))


@firewall_router.post("/check", response_model=schemas.RulesetCheckOut)
def check_ruleset(db: Session = Depends(get_db)):
    """Compile + validate the syntax (nft -c -f -) without touching the kernel."""
    ruleset = compile_ruleset(db)
    ok, message = apply_manager.check(ruleset)
    return schemas.RulesetCheckOut(ok=ok, message=message, ruleset=ruleset)


@firewall_router.get("/apply/status", response_model=schemas.ApplyStatusOut)
def apply_status():
    return schemas.ApplyStatusOut(**apply_manager.status.to_dict())


@firewall_router.get("/stats", response_model=schemas.FirewallStatsOut)
def firewall_stats():
    """Return live nft counters per DB rule (filter + NAT).

    Reads `nft -j list ruleset`, parses the JSON, and maps each entry
    back to its DB id through the [muros r=<id>] / [muros nat=<id>]
    comment marker emitted by the compiler. Counters reset on every
    Apply, so the UI displays activity since the last Apply.
    Empty payload if nft is unreachable or the ruleset is empty.
    """
    from app import firewall_stats as fs
    data = fs.collect_counters()
    # JSON object keys must be strings.
    return schemas.FirewallStatsOut(
        rules={str(k): schemas.FirewallCounter(**v) for k, v in data["rules"].items()},
        nat={str(k): schemas.FirewallCounter(**v) for k, v in data["nat"].items()},
    )


@firewall_router.get("/pending", response_model=schemas.FirewallPendingOut)
def firewall_pending(db: Session = Depends(get_db)):
    """Return how many firewall/NAT/zone rows diverge from the kernel.

    A row is dirty when the DB has been mutated after the last
    successful POST /api/firewall/apply (or never applied). The UI
    surfaces this counter on the Apply button so the admin always
    knows when the live ruleset trails behind.
    """
    rules = (
        db.query(models.FirewallRule).filter(models.FirewallRule.dirty == True).count()  # noqa: E712
    )
    nat = (
        db.query(models.NatRule).filter(models.NatRule.dirty == True).count()  # noqa: E712
    )
    zones = (
        db.query(models.Zone).filter(models.Zone.dirty == True).count()  # noqa: E712
    )
    # Global singleton counts as +1 if set. It's bumped when a deletion
    # leaves no per-row dirty flag (last rule of a chain, etc.) so the
    # UI always shows the pending dot in that case.
    state = db.get(models.FirewallApplyState, 1)
    global_dirty = 1 if (state is not None and state.dirty) else 0
    return schemas.FirewallPendingOut(
        rules=rules, nat=nat, zones=zones,
        total=rules + nat + zones + global_dirty,
    )


@firewall_router.get("/apply/lockout-check", response_model=schemas.LockoutCheckOut)
def apply_lockout_check(request: Request, db: Session = Depends(get_db)):
    """Static pre-apply check: would the pending input chain still accept
    NEW management connections (web UI, SSH) from the caller's source?

    The commit-confirm modal cannot catch this: the operator stays
    connected through conntrack's established/related accept even when the
    rule allowing new management connections was removed. This endpoint
    lets the UI surface a blocking warning before applying.
    """
    report = lockout_guard.analyze(db, _client_ip(request))
    return schemas.LockoutCheckOut(**report)


@firewall_router.post("/apply", response_model=schemas.ApplyStatusOut)
def apply_ruleset(
    req: schemas.ApplyRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    # Management-lockout guard: refuse to apply a ruleset that would block
    # NEW management connections from the operator's source, unless they
    # explicitly acknowledged the risk. This is a safety net for scripted
    # callers too; the UI runs the same check up front (lockout-check).
    if not req.acknowledge_lockout:
        report = lockout_guard.analyze(db, _client_ip(request))
        if report["blocked"]:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "management_lockout",
                    "message": report["message"],
                    "report": report,
                },
            )

    ruleset = compile_ruleset(db)
    try:
        status_obj = apply_manager.apply(ruleset, timeout=req.timeout_seconds)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    # Clear dirty flags on successful apply. We do this BEFORE the
    # auto-confirm timeout: if the ruleset is later rolled back, the
    # rows do NOT get re-flagged automatically (a rollback restores
    # the kernel to its previous state, while the DB still holds the
    # new values, which is then again pending). So we re-flag on
    # rollback below.
    db.query(models.FirewallRule).filter(models.FirewallRule.dirty == True).update(  # noqa: E712
        {"dirty": False}, synchronize_session=False,
    )
    db.query(models.NatRule).filter(models.NatRule.dirty == True).update(  # noqa: E712
        {"dirty": False}, synchronize_session=False,
    )
    db.query(models.Zone).filter(models.Zone.dirty == True).update(  # noqa: E712
        {"dirty": False}, synchronize_session=False,
    )
    # Clear the global singleton too. last_applied_at is informational,
    # used by the UI footer ("last apply 3 min ago") for context.
    state = _get_apply_state(db)
    state.dirty = False
    from datetime import datetime, timezone
    state.last_applied_at = datetime.now(timezone.utc)
    db.commit()
    from app import ha_sync
    ha_sync.maybe_auto_push(db, triggered_by="firewall-apply")
    return schemas.ApplyStatusOut(**status_obj.to_dict())


@firewall_router.post("/apply/confirm", response_model=schemas.ApplyStatusOut)
def apply_confirm():
    try:
        status_obj = apply_manager.confirm()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return schemas.ApplyStatusOut(**status_obj.to_dict())


@firewall_router.post("/apply/rollback", response_model=schemas.ApplyStatusOut)
def apply_rollback(db: Session = Depends(get_db)):
    try:
        status_obj = apply_manager.rollback()
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    # The kernel has been restored to its previous ruleset but the DB
    # still holds the new (rolled-back) values. So every row is again
    # pending. We can't tell which were dirty before the apply call,
    # so we conservatively re-flag everything: the worst case is the
    # admin re-applies the same content, which is a no-op for nft.
    db.query(models.FirewallRule).update(
        {"dirty": True}, synchronize_session=False,
    )
    db.query(models.NatRule).update(
        {"dirty": True}, synchronize_session=False,
    )
    db.query(models.Zone).update(
        {"dirty": True}, synchronize_session=False,
    )
    mark_firewall_dirty(db)
    db.commit()
    return schemas.ApplyStatusOut(**status_obj.to_dict())


# --- NAT rules ---
nat_router = APIRouter(prefix="/api/nat", tags=["nat"], dependencies=_auth_dep)


# --- Static routes ---
routes_router = APIRouter(prefix="/api/routes", tags=["routes"], dependencies=_auth_dep)


@routes_router.get("", response_model=list[schemas.StaticRouteOut])
def list_routes(db: Session = Depends(get_db)):
    return (
        db.query(models.StaticRoute)
        .order_by(models.StaticRoute.metric, models.StaticRoute.id)
        .all()
    )


@routes_router.post("", response_model=schemas.StaticRouteOut, status_code=status.HTTP_201_CREATED)
def create_route(data: schemas.StaticRouteCreate, db: Session = Depends(get_db)):
    if data.interface_id and not db.get(models.Interface, data.interface_id):
        raise HTTPException(400, "invalid interface_id")
    route = models.StaticRoute(**data.model_dump())
    route.dirty = True  # apply manuel via POST /api/network/apply
    db.add(route)
    db.commit()
    db.refresh(route)
    return route


@routes_router.put("/{route_id}", response_model=schemas.StaticRouteOut)
def update_route(
    route_id: int,
    data: schemas.StaticRouteUpdate,
    response: Response,
    db: Session = Depends(get_db),
):
    route = db.get(models.StaticRoute, route_id)
    if not route:
        raise HTTPException(404, "Route not found")
    payload = data.model_dump(exclude_unset=True)
    if "interface_id" in payload and payload["interface_id"] is not None:
        if not db.get(models.Interface, payload["interface_id"]):
            raise HTTPException(400, "invalid interface_id")
    # We mark dirty only if a value really changes (otherwise the pending
    # counter inflates for no reason when the admin re-submits the form).
    dirty_keys = ("destination", "gateway", "interface_id", "metric", "enabled")
    changed = False
    for k, v in payload.items():
        if k in dirty_keys and getattr(route, k) != v:
            changed = True
        setattr(route, k, v)
    if changed:
        route.dirty = True
    db.commit()
    db.refresh(route)
    return route


@routes_router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_route(route_id: int, db: Session = Depends(get_db)):
    route = db.get(models.StaticRoute, route_id)
    if not route:
        raise HTTPException(404, "Route not found")
    if route.enabled:
        apply_route(route, "del")
    db.delete(route)
    db.commit()

