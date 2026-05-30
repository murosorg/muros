# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas, service_dirty
from app.auth import current_user
from app.db import get_db

_auth_dep = [Depends(current_user)]


# --- TLS UI (certificat nginx) ---
tls_router = APIRouter(prefix="/api/tls", tags=["tls"], dependencies=_auth_dep)


@tls_router.get("/status", response_model=schemas.TlsStatus)
def tls_status():
    from app import tls
    return tls.get_status()


def _snapshot_current_tls() -> dict:
    """Lit l'ancien cert+key dans /etc/nginx/ssl/ pour pouvoir rollback."""
    from app import tls as tls_mod
    try:
        cert_pem = tls_mod.CERT_PATH.read_text(encoding="utf-8") if tls_mod.CERT_PATH.exists() else ""
        # The key may be 0600, but the backend runs as root so it is OK.
        key_pem = tls_mod.KEY_PATH.read_text(encoding="utf-8") if tls_mod.KEY_PATH.exists() else ""
        return {"cert_pem": cert_pem, "key_pem": key_pem}
    except OSError:
        return {"cert_pem": "", "key_pem": ""}


@tls_router.post("/upload", response_model=schemas.TlsApplyResult)
def tls_upload(data: schemas.TlsUploadIn):
    from app import tls as tls_mod
    from app import pending_apply
    snapshot = _snapshot_current_tls()
    try:
        res = tls_mod.upload_cert(data.cert_pem, data.key_pem)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

    pending_id = None
    if res.get("applied") and snapshot["cert_pem"]:
        pending = pending_apply.create_pending(
            "tls", snapshot, new_config_summary="Upload cert TLS", timeout_seconds=10,
        )
        pending_id = pending.id

    return {
        **res,
        "pending_apply_id": pending_id,
        "rollback_timeout_seconds": 10 if pending_id else None,
    }


@tls_router.post("/regenerate-self-signed", response_model=schemas.TlsApplyResult)
def tls_regenerate(_data: schemas.TlsRegenerateIn | None = None):
    """Regenere le cert snakeoil (delegate a make-ssl-cert).

    Les parametres subject_cn/san/validity_days passes par le client sont
    ignores : on prend le snakeoil standard de Debian (CN = hostname).
    Pour un vrai cert custom, utilise l'upload PEM.
    """
    from app import tls as tls_mod
    from app import pending_apply
    snapshot = _snapshot_current_tls()
    try:
        res = tls_mod.regenerate_snakeoil()
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(500, str(exc))

    pending_id = None
    if res.get("applied") and snapshot["cert_pem"]:
        pending = pending_apply.create_pending(
            "tls", snapshot, new_config_summary="Regen cert snakeoil", timeout_seconds=10,
        )
        pending_id = pending.id

    return {
        **res,
        "pending_apply_id": pending_id,
        "rollback_timeout_seconds": 10 if pending_id else None,
    }


# --- SSH config ---
ssh_router = APIRouter(prefix="/api/ssh", tags=["ssh"], dependencies=_auth_dep)


def _get_ssh_config(db: Session) -> models.SshConfig:
    cfg = db.get(models.SshConfig, 1)
    if cfg is None:
        cfg = models.SshConfig(id=1)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


@ssh_router.get("/status", response_model=schemas.SshStatus)
def ssh_status(db: Session = Depends(get_db)):
    from app import ssh_config, service_dirty
    cfg = _get_ssh_config(db)
    # Out-of-band reconcile: if the operator re-enabled sshd from the
    # serial console (or the .deb postinst did) after the UI toggle was
    # flipped off, clear the stale admin_disabled flag so the page
    # stops labelling a running daemon as "disabled by admin".
    if service_dirty._reconcile_ssh_admin_flag(db, source="ssh.status"):
        db.refresh(cfg)
    return ssh_config.get_status(admin_disabled=bool(cfg.admin_disabled))


@ssh_router.post("/service/toggle", response_model=schemas.SshServiceToggleResult)
def ssh_service_toggle(
    data: schemas.SshServiceToggleIn,
    db: Session = Depends(get_db),
):
    """Enable or disable the sshd service from the UI.

    Distinct from /api/ssh/apply which only reloads sshd to pick up
    new drop-in settings. This route flips the unit on or off entirely
    and persists the intent so the Monitoring page can label it
    'disabled by admin' instead of red-flagging an unexpected outage.
    """
    from app import ssh_config
    cfg = _get_ssh_config(db)
    try:
        result = ssh_config.set_service_enabled(data.enabled)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    cfg.admin_disabled = (not data.enabled)
    db.commit()
    db.refresh(cfg)
    return {
        "applied": bool(result.get("applied")),
        "admin_disabled": bool(cfg.admin_disabled),
        "service_active": bool(result.get("active")),
        "message": str(result.get("message", "")),
    }


@ssh_router.post("/install", response_model=schemas.SshInstallResult)
def ssh_install():
    """Installe openssh-server via apt. Idempotent."""
    from app import ssh_config
    try:
        return ssh_config.install_packages()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@ssh_router.get("/config", response_model=schemas.SshConfigOut)
def ssh_get_config(db: Session = Depends(get_db)):
    return _get_ssh_config(db)


@ssh_router.put("/config", response_model=schemas.SshConfigOut)
def ssh_update_config(data: schemas.SshConfigIn, db: Session = Depends(get_db)):
    cfg = _get_ssh_config(db)
    payload = data.model_dump(exclude={"confirm_loopback", "skip_rollback"})
    for field, value in payload.items():
        setattr(cfg, field, value)
    db.commit()
    db.refresh(cfg)
    service_dirty.mark_dirty(db, "ssh", summary="SSH config updated")
    return cfg


@ssh_router.get("/pending")
def ssh_pending(db: Session = Depends(get_db)):
    return service_dirty.get_state(db, "ssh")


@ssh_router.post("/apply", response_model=schemas.SshApplyResult)
def ssh_apply(skip_rollback: bool = False, db: Session = Depends(get_db)):
    """Applique la conf SSH et cree un pending pour rollback automatique."""
    from app import ssh_config, pending_apply
    cfg = _get_ssh_config(db)

    old = {
        "port": cfg.port,
        "listen_address": cfg.listen_address,
        "permit_root_login": cfg.permit_root_login,
        "password_authentication": cfg.password_authentication,
        "pubkey_authentication": cfg.pubkey_authentication,
        "max_auth_tries": cfg.max_auth_tries,
        "client_alive_interval": cfg.client_alive_interval,
        "client_alive_count_max": cfg.client_alive_count_max,
    }
    try:
        res = ssh_config.apply_config(cfg)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

    pending_id = None
    if res.get("applied") and not skip_rollback:
        new_summary = f"port {cfg.port} listen {cfg.listen_address}"
        pending = pending_apply.create_pending(
            "ssh", old, new_config_summary=new_summary,
            timeout_seconds=10,
        )
        pending_id = pending.id

    if res.get("applied"):
        service_dirty.mark_clean(db, "ssh", summary="sshd reload")

    return {
        **res,
        "pending_apply_id": pending_id,
        "rollback_timeout_seconds": 10 if pending_id else None,
    }


@ssh_router.get("/keys", response_model=list[schemas.SshAuthorizedKey])
def ssh_list_keys():
    from app import ssh_config
    return ssh_config.list_authorized_keys()


@ssh_router.post("/keys", response_model=schemas.SshKeyAddResult)
def ssh_add_key(data: schemas.SshKeyAdd):
    from app import ssh_config
    try:
        return ssh_config.add_authorized_key(data.key_text)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


@ssh_router.delete("/keys/{key_b64:path}")
def ssh_delete_key(key_b64: str):
    from app import ssh_config
    try:
        return ssh_config.delete_authorized_key(key_b64)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))


