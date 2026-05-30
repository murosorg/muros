# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""MurOS API HTTP routes (submodule)."""
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import current_user
from app.db import get_db

import logging

log = logging.getLogger("muros.vpn")

_auth_dep = [Depends(current_user)]


# --- VPN : WireGuard ---
from app import service_dirty  # noqa: E402


def _stage_wireguard(db: Session, summary: str | None = None) -> None:
    """Save path : regenerate /etc/wireguard/<iface>.conf and flag dirty.

    Does NOT touch the netlink interface ; the live tunnel keeps its
    previous config until the operator clicks Apply.
    """
    from app import wireguard, models
    cfg = db.get(models.WireGuardConfig, 1)
    if cfg is not None:
        peers = db.query(models.WireGuardPeer).order_by(models.WireGuardPeer.id).all()
        try:
            wireguard.write_conf(cfg, peers)
        except Exception:
            # DB is the source of truth ; a write failure (e.g. perms
            # in dev) is surfaced at Apply time, not here.
            pass
    service_dirty.mark_dirty(db, "wireguard", summary=summary)


def _stage_ipsec(db: Session, summary: str | None = None) -> None:
    """Save path for IPsec / strongSwan."""
    from app import ipsec, models
    conns = db.query(models.IpsecConnection).filter_by(enabled=True).all()
    ca = db.get(models.IpsecCa, 1)
    if ca is not None and not ca.cert_pem:
        ca = None
    certs = db.query(models.IpsecCert).order_by(models.IpsecCert.id).all()
    revoked = [c for c in certs if c.revoked]
    try:
        ipsec.write_conf(conns, ca=ca, certs=certs, revoked_certs=revoked)
    except Exception as exc:  # noqa: BLE001
        log.debug("Deferred IPsec config write (will retry on next apply): %s", exc)
    service_dirty.mark_dirty(db, "ipsec", summary=summary)


wireguard_router = APIRouter(prefix="/api/wireguard", tags=["wireguard"], dependencies=_auth_dep)


@wireguard_router.get("/status", response_model=schemas.WireGuardStatus)
def wireguard_status():
    from app import wireguard
    return wireguard.get_status()


@wireguard_router.post("/install", response_model=schemas.WireGuardInstallResult)
def wireguard_install():
    """Installe wireguard + wireguard-tools via apt. Idempotent."""
    from app import wireguard
    try:
        return wireguard.install_packages()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


def _get_wg_config(db: Session) -> models.WireGuardConfig:
    cfg = db.get(models.WireGuardConfig, 1)
    if cfg is None:
        cfg = models.WireGuardConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@wireguard_router.get("/config", response_model=schemas.WireGuardConfigOut)
def wireguard_get_config(db: Session = Depends(get_db)):
    from app import wireguard
    cfg = _get_wg_config(db)
    # First-run defaults so the operator never has to think about keys,
    # tunnel subnet or port before creating their first peer.
    if wireguard.ensure_initialized(cfg):
        db.commit()
        db.refresh(cfg)
    return cfg


@wireguard_router.put("/config", response_model=schemas.WireGuardConfigOut)
def wireguard_update_config(data: schemas.WireGuardConfigIn, db: Session = Depends(get_db)):
    from app import wireguard
    cfg = _get_wg_config(db)
    # Si on a une cle privee mais pas de publique, on la derive automatiquement.
    if data.private_key and not data.public_key:
        try:
            data.public_key = wireguard._pubkey_from_priv(data.private_key)
        except (ValueError, Exception) as exc:
            raise HTTPException(400, f"Invalid private key: {exc}")
    for field, value in data.model_dump().items():
        setattr(cfg, field, value)
    db.commit()
    db.refresh(cfg)
    _stage_wireguard(db, summary="WireGuard config updated")
    return cfg


@wireguard_router.post("/keypair", response_model=schemas.WireGuardKeypair)
def wireguard_generate_keypair():
    from app import wireguard
    return wireguard.generate_keypair()


@wireguard_router.post("/psk", response_model=schemas.WireGuardPresharedKeyOut)
def wireguard_generate_psk():
    from app import wireguard
    return {"preshared_key": wireguard.generate_psk()}


@wireguard_router.get("/peers", response_model=list[schemas.WireGuardPeerOut])
def wireguard_list_peers(db: Session = Depends(get_db)):
    return db.query(models.WireGuardPeer).order_by(models.WireGuardPeer.id).all()


# A handshake older than this many seconds is considered stale/disconnected.
# WireGuard renews a handshake roughly every 2 min on active tunnels; 180s
# leaves a small margin before flagging a peer as down.
_WG_HANDSHAKE_WINDOW_S = 180


@wireguard_router.get("/peers/status", response_model=list[schemas.WireGuardPeerStatus])
def wireguard_peers_status(db: Session = Depends(get_db)):
    """Live per-peer runtime (handshake, transfer, connectivity) via `wg`.

    Joins the kernel runtime from `wg show dump` with the DB peers so the UI
    can label each line. Returns an empty list when no WG interface is up.
    """
    from app import wireguard
    runtime = wireguard.peer_runtime_status()
    peers_by_key = {p.public_key: p for p in db.query(models.WireGuardPeer).all()}
    now = int(time.time())
    out: list[schemas.WireGuardPeerStatus] = []
    for public_key, rt in runtime.items():
        hs = rt["latest_handshake"]
        age = (now - hs) if hs else None
        peer = peers_by_key.get(public_key)
        out.append(schemas.WireGuardPeerStatus(
            peer_id=peer.id if peer else None,
            name=peer.name if peer else None,
            public_key=public_key,
            interface=rt["interface"],
            endpoint=rt["endpoint"],
            latest_handshake=hs,
            handshake_age_seconds=age,
            connected=age is not None and age < _WG_HANDSHAKE_WINDOW_S,
            rx_bytes=rt["rx_bytes"],
            tx_bytes=rt["tx_bytes"],
        ))
    out.sort(key=lambda s: (s.name or "\uffff", s.public_key))
    return out


@wireguard_router.post("/peers", response_model=schemas.WireGuardPeerOut)
def wireguard_create_peer(data: schemas.WireGuardPeerIn, db: Session = Depends(get_db)):
    peer = models.WireGuardPeer(**data.model_dump())
    db.add(peer)
    db.commit()
    db.refresh(peer)
    _stage_wireguard(db, summary="WireGuard peer added")
    return peer


@wireguard_router.put("/peers/{peer_id}", response_model=schemas.WireGuardPeerOut)
def wireguard_update_peer(peer_id: int, data: schemas.WireGuardPeerIn, db: Session = Depends(get_db)):
    peer = db.get(models.WireGuardPeer, peer_id)
    if peer is None:
        raise HTTPException(404, "Peer not found")
    for field, value in data.model_dump().items():
        setattr(peer, field, value)
    db.commit()
    db.refresh(peer)
    _stage_wireguard(db, summary="WireGuard peer updated")
    return peer


@wireguard_router.delete("/peers/{peer_id}")
def wireguard_delete_peer(peer_id: int, db: Session = Depends(get_db)):
    peer = db.get(models.WireGuardPeer, peer_id)
    if peer is None:
        raise HTTPException(404, "Peer not found")
    db.delete(peer)
    db.commit()
    _stage_wireguard(db, summary="WireGuard peer removed")
    return {"ok": True}


@wireguard_router.post("/peers/{peer_id}/export", response_model=schemas.WireGuardPeerExport)
def wireguard_export_peer(peer_id: int, db: Session = Depends(get_db), peer_private_key: str | None = None):
    """Export la conf cote client pour ce peer + QR code SVG."""
    from app import wireguard
    cfg = _get_wg_config(db)
    peer = db.get(models.WireGuardPeer, peer_id)
    if peer is None:
        raise HTTPException(404, "Peer not found")
    text = wireguard.render_peer_client_config(cfg, peer, peer_private_key)
    qr = None
    try:
        qr = wireguard.render_peer_qr_svg(text)
    except RuntimeError:
        # qrcode pas installe : on retourne quand meme le texte.
        pass
    return {"config_text": text, "qr_svg": qr}


def _refresh_nftables_for_vpn(db: Session, trigger: str) -> None:
    """Recompile and apply the nftables ruleset after a VPN change.

    Forward accept + masquerade for the WireGuard subnet are emitted
    by the compiler. Without this refresh, those rules would only land
    in the kernel the next time the operator hits Apply on the
    firewall page, which defeats the "VPN works out of the box"
    promise. We auto-confirm because the change is incremental and
    the operator already proved network reachability by using the API.
    """
    import logging
    log = logging.getLogger(__name__)
    # Wrap the whole helper: any error here is a best-effort nicety
    # and must NEVER turn the WireGuard apply route into a 500.
    try:
        from app.apply import manager as apply_manager
        from app.compiler import compile_ruleset
    except ImportError as exc:
        log.warning("Could not import apply/compiler for %s refresh: %s",
                    trigger, exc)
        return
    try:
        ruleset = compile_ruleset(db)
        status_obj = apply_manager.apply(ruleset)
        if status_obj.state == "pending":
            try:
                apply_manager.confirm()
            except RuntimeError as exc:
                log.warning("Auto-confirm after %s failed: %s", trigger, exc)
        elif status_obj.state == "failed":
            log.warning("nftables apply after %s failed: %s",
                        trigger, status_obj.message)
    except RuntimeError as exc:
        # Another apply is already pending: leave it alone, the user
        # will confirm or roll back from the firewall page.
        log.info("Skipped nftables refresh after %s: %s", trigger, exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("Unexpected error refreshing nftables after %s: %s",
                    trigger, exc)


@wireguard_router.post("/apply", response_model=schemas.WireGuardApplyResult)
def wireguard_apply(db: Session = Depends(get_db)):
    from app import wireguard, ha_sync
    cfg = _get_wg_config(db)
    peers = db.query(models.WireGuardPeer).order_by(models.WireGuardPeer.id).all()
    try:
        res = wireguard.reload(cfg, peers)
    except wireguard.WireGuardApplyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(500, str(exc))
    service_dirty.mark_clean(db, "wireguard", summary="wg-quick reload")
    _refresh_nftables_for_vpn(db, "wireguard-apply")
    ha_sync.maybe_auto_push(db, triggered_by="wireguard-apply")
    return res


@wireguard_router.get("/pending")
def wireguard_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "wireguard")


# --- VPN : IPsec (strongSwan) ---
ipsec_router = APIRouter(prefix="/api/ipsec", tags=["ipsec"], dependencies=_auth_dep)


@ipsec_router.get("/status", response_model=schemas.IpsecStatus)
def ipsec_status():
    from app import ipsec
    return ipsec.get_status()


@ipsec_router.post("/install", response_model=schemas.IpsecInstallResult)
def ipsec_install():
    """Installe strongswan + strongswan-swanctl via apt. Idempotent."""
    from app import ipsec
    try:
        return ipsec.install_packages()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@ipsec_router.get("/connections", response_model=list[schemas.IpsecConnectionOut])
def ipsec_list_connections(db: Session = Depends(get_db)):
    return db.query(models.IpsecConnection).order_by(models.IpsecConnection.id).all()


@ipsec_router.post("/connections", response_model=schemas.IpsecConnectionOut)
def ipsec_create_connection(data: schemas.IpsecConnectionIn, db: Session = Depends(get_db)):
    existing = db.query(models.IpsecConnection).filter_by(name=data.name).first()
    if existing:
        raise HTTPException(400, f"Connection '{data.name}' already exists.")
    conn = models.IpsecConnection(**data.model_dump())
    db.add(conn)
    db.commit()
    db.refresh(conn)
    _stage_ipsec(db, summary="IPsec connection added")
    return conn


@ipsec_router.put("/connections/{conn_id}", response_model=schemas.IpsecConnectionOut)
def ipsec_update_connection(conn_id: int, data: schemas.IpsecConnectionIn, db: Session = Depends(get_db)):
    conn = db.get(models.IpsecConnection, conn_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    # Verifie unicite du nom si change.
    if data.name != conn.name:
        other = db.query(models.IpsecConnection).filter_by(name=data.name).first()
        if other:
            raise HTTPException(400, f"Connection '{data.name}' already exists.")
    for field, value in data.model_dump().items():
        setattr(conn, field, value)
    db.commit()
    db.refresh(conn)
    _stage_ipsec(db, summary="IPsec connection updated")
    return conn


@ipsec_router.delete("/connections/{conn_id}")
def ipsec_delete_connection(conn_id: int, db: Session = Depends(get_db)):
    conn = db.get(models.IpsecConnection, conn_id)
    if conn is None:
        raise HTTPException(404, "Connection not found")
    db.delete(conn)
    db.commit()
    _stage_ipsec(db, summary="IPsec connection removed")
    return {"ok": True}


@ipsec_router.get("/config", response_model=schemas.IpsecGlobalConfig)
def ipsec_get_global_config(db: Session = Depends(get_db)):
    from app import ipsec
    cfg = ipsec.get_or_create_global_config(db)
    return {"enabled": bool(cfg.enabled)}


@ipsec_router.put("/config", response_model=schemas.IpsecGlobalConfig)
def ipsec_put_global_config(
    data: schemas.IpsecGlobalConfig, db: Session = Depends(get_db),
):
    """Flip the global IPsec server toggle and apply immediately.

    Disabling stops strongswan and disables its unit at boot. Enabling
    re-applies the saved connections : if at least one is enabled,
    strongswan is started ; otherwise the service stays down (same
    semantics as Apply with no connection enabled).
    """
    from app import ipsec
    cfg = ipsec.get_or_create_global_config(db)
    cfg.enabled = bool(data.enabled)
    db.commit()
    conns = db.query(models.IpsecConnection).order_by(models.IpsecConnection.id).all()
    ca = db.get(models.IpsecCa, 1)
    if ca is not None and not ca.cert_pem:
        ca = None
    certs = db.query(models.IpsecCert).order_by(models.IpsecCert.id).all()
    revoked = [c for c in certs if c.revoked]
    try:
        ipsec.apply_config(conns, ca=ca, certs=certs, revoked_certs=revoked,
                           globally_enabled=cfg.enabled)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(500, str(exc))
    service_dirty.mark_clean(db, "ipsec", summary="IPsec global toggle")
    return {"enabled": bool(cfg.enabled)}


@ipsec_router.post("/apply", response_model=schemas.IpsecApplyResult)
def ipsec_apply(db: Session = Depends(get_db)):
    from app import ipsec
    cfg = ipsec.get_or_create_global_config(db)
    conns = db.query(models.IpsecConnection).order_by(models.IpsecConnection.id).all()
    ca = db.get(models.IpsecCa, 1)
    if ca is not None and not ca.cert_pem:
        ca = None
    certs = db.query(models.IpsecCert).order_by(models.IpsecCert.id).all()
    revoked = [c for c in certs if c.revoked]
    try:
        res = ipsec.reload(conns, ca=ca, certs=certs, revoked_certs=revoked,
                           globally_enabled=cfg.enabled)
    except ipsec.IpsecApplyError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(500, str(exc))
    service_dirty.mark_clean(db, "ipsec", summary="strongSwan reload")
    from app import ha_sync
    ha_sync.maybe_auto_push(db, triggered_by="ipsec-apply")
    return res


@ipsec_router.get("/pending")
def ipsec_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "ipsec")


def _cert_to_out(c: models.IpsecCert) -> dict:
    """Serialise un IpsecCert pour le response_model (ajoute has_key)."""
    return {
        "id": c.id,
        "name": c.name,
        "subject_cn": c.subject_cn,
        "san": c.san,
        "cert_pem": c.cert_pem,
        "is_local": c.is_local,
        "serial": c.serial,
        "revoked": c.revoked,
        "revoked_at": c.revoked_at,
        "validity_days": c.validity_days,
        "created_at": c.created_at,
        "expires_at": c.expires_at,
        "has_key": bool(c.key_pem),
    }


@ipsec_router.get("/ca")
def ipsec_get_ca(db: Session = Depends(get_db)):
    """Return the CA if it exists, otherwise null.

    We avoid response_model=schemas.IpsecCaOut | None: depending on the
    FastAPI/Pydantic version it can break. We serialize by hand.
    """
    ca = db.get(models.IpsecCa, 1)
    if ca is None or not ca.cert_pem:
        return None
    return {
        "id": ca.id,
        "subject_cn": ca.subject_cn,
        "subject_o": ca.subject_o,
        "cert_pem": ca.cert_pem,
        "validity_days": ca.validity_days,
        "created_at": ca.created_at.isoformat() if ca.created_at else None,
        "expires_at": ca.expires_at.isoformat() if ca.expires_at else None,
    }


@ipsec_router.post("/ca", response_model=schemas.IpsecCaOut)
def ipsec_generate_ca(data: schemas.IpsecCaGenerate, db: Session = Depends(get_db)):
    """Generate a new root CA. Replaces the existing one if present."""
    from app import ipsec_pki
    try:
        result = ipsec_pki.generate_ca(
            subject_cn=data.subject_cn,
            subject_o=data.subject_o,
            validity_days=data.validity_days,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Generation CA echouee : {exc}")

    ca = db.get(models.IpsecCa, 1)
    if ca is None:
        ca = models.IpsecCa(id=1)
        db.add(ca)
    ca.subject_cn = data.subject_cn
    ca.subject_o = data.subject_o
    ca.validity_days = data.validity_days
    ca.cert_pem = result["cert_pem"]
    ca.key_pem = result["key_pem"]
    ca.expires_at = result["expires_at"]
    db.commit()
    db.refresh(ca)
    _stage_ipsec(db, summary="IPsec CA generated")
    return ca


@ipsec_router.get("/certs", response_model=list[schemas.IpsecCertOut])
def ipsec_list_certs(db: Session = Depends(get_db)):
    return [
        _cert_to_out(c)
        for c in db.query(models.IpsecCert).order_by(models.IpsecCert.id).all()
    ]


@ipsec_router.post("/certs", response_model=schemas.IpsecCertOut)
def ipsec_create_cert(data: schemas.IpsecCertGenerate, db: Session = Depends(get_db)):
    """Genere un nouveau cert signe par la CA MurOS."""
    from app import ipsec_pki
    ca = db.get(models.IpsecCa, 1)
    if ca is None or not ca.cert_pem:
        raise HTTPException(400, "The MurOS CA has not been generated yet.")

    existing = db.query(models.IpsecCert).filter_by(name=data.name).first()
    if existing:
        raise HTTPException(400, f"Cert '{data.name}' already exists.")

    try:
        result = ipsec_pki.sign_cert(
            ca=ca,
            subject_cn=data.subject_cn,
            san=data.san,
            validity_days=data.validity_days,
            is_local=data.is_local,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"Generation cert echouee : {exc}")

    cert = models.IpsecCert(
        name=data.name,
        subject_cn=data.subject_cn,
        san=data.san,
        cert_pem=result["cert_pem"],
        key_pem=result["key_pem"],
        is_local=data.is_local,
        serial=result["serial"],
        validity_days=data.validity_days,
        expires_at=result["expires_at"],
    )
    db.add(cert)
    db.commit()
    db.refresh(cert)
    _stage_ipsec(db, summary="IPsec cert generated")
    return _cert_to_out(cert)


@ipsec_router.post("/certs/import", response_model=schemas.IpsecCertOut)
def ipsec_import_cert(data: schemas.IpsecCertImport, db: Session = Depends(get_db)):
    """Importe un cert distant en PEM (pour valider l'identite d'un peer)."""
    from app import ipsec_pki
    existing = db.query(models.IpsecCert).filter_by(name=data.name).first()
    if existing:
        raise HTTPException(400, f"Cert '{data.name}' already exists.")
    try:
        result = ipsec_pki.import_remote_cert(data.name, data.cert_pem)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    cert = models.IpsecCert(
        name=data.name,
        subject_cn=result["subject_cn"],
        san=None,
        cert_pem=result["cert_pem"],
        key_pem=None,
        is_local=False,
        serial=result["serial"],
        validity_days=0,
        expires_at=result["expires_at"],
    )
    db.add(cert)
    db.commit()
    db.refresh(cert)
    _stage_ipsec(db, summary="IPsec remote cert imported")
    return _cert_to_out(cert)


@ipsec_router.post("/certs/{cert_id}/revoke", response_model=schemas.IpsecCertOut)
def ipsec_revoke_cert(cert_id: int, db: Session = Depends(get_db)):
    cert = db.get(models.IpsecCert, cert_id)
    if cert is None:
        raise HTTPException(404, "Cert not found")
    cert.revoked = True
    cert.revoked_at = datetime.utcnow()
    db.commit()
    db.refresh(cert)
    _stage_ipsec(db, summary="IPsec cert revoked")
    return _cert_to_out(cert)


@ipsec_router.delete("/certs/{cert_id}")
def ipsec_delete_cert(cert_id: int, db: Session = Depends(get_db)):
    cert = db.get(models.IpsecCert, cert_id)
    if cert is None:
        raise HTTPException(404, "Cert not found")
    # Refuse la suppression si reference par une connexion enabled.
    in_use = db.query(models.IpsecConnection).filter(
        (models.IpsecConnection.local_cert_id == cert_id)
        | (models.IpsecConnection.remote_cert_id == cert_id)
    ).first()
    if in_use:
        raise HTTPException(400, f"Cert utilise par la connexion '{in_use.name}'. Modifier la connexion d'abord.")
    db.delete(cert)
    db.commit()
    _stage_ipsec(db, summary="IPsec cert removed")
    return {"ok": True}


