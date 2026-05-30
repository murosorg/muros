# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""User management : root grants web UI access to local Linux accounts.

The MurOS web UI and SSH authenticate against the system PAM stack, so
every local Linux account could in principle pass authentication. The
``ui_access`` flag on each mirror row decides who is actually allowed
into the web UI. By default only ``root`` is granted; from this page an
administrator can grant or revoke access to any other Linux account and
optionally promote it to administrator. These endpoints are admin-only.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, pam_auth, schemas
from app.auth import require_admin
from app.db import get_db

log = logging.getLogger("muros.users")

users_router = APIRouter(
    prefix="/api/users",
    tags=["users"],
    dependencies=[Depends(require_admin)],
)


def _to_admin_out(user: models.User) -> schemas.UserAdminOut:
    return schemas.UserAdminOut(
        id=user.id,
        username=user.username,
        is_admin=user.is_admin,
        ui_access=user.ui_access,
        must_change_password=user.must_change_password,
        last_login=user.last_login,
        exists_on_system=pam_auth.account_exists(user.username),
    )


@users_router.get("", response_model=schemas.UsersListOut)
def list_users(db: Session = Depends(get_db)):
    """List mirror rows plus the Linux accounts still available to grant."""
    rows = db.query(models.User).order_by(models.User.username).all()
    known = {u.username for u in rows}
    grantable = [
        name for name in pam_auth.list_login_accounts()
        if name not in known
    ]
    return schemas.UsersListOut(
        users=[_to_admin_out(u) for u in rows],
        grantable_accounts=grantable,
    )


@users_router.post("/grant", response_model=schemas.UserAdminOut, status_code=201)
def grant_access(data: schemas.GrantAccessRequest, db: Session = Depends(get_db)):
    """Grant web UI access to an existing Linux account."""
    username = data.username.strip()
    if not username:
        raise HTTPException(400, "Username is required")
    if not pam_auth.account_exists(username):
        raise HTTPException(
            404,
            f"No local Linux account named '{username}'. Create the system "
            "account first, then grant it access here.",
        )
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        user = models.User(
            username=username,
            password_hash="!",  # PAM is the source of truth, not this column
            is_admin=data.is_admin,
            ui_access=True,
            must_change_password=False,
        )
        db.add(user)
    else:
        user.ui_access = True
        user.is_admin = data.is_admin
    db.commit()
    db.refresh(user)
    log.info("UI access granted to %s (admin=%s)", username, user.is_admin)
    return _to_admin_out(user)


@users_router.put("/{user_id}", response_model=schemas.UserAdminOut)
def update_user(
    user_id: int,
    data: schemas.UpdateUserRequest,
    db: Session = Depends(get_db),
    actor: models.User = Depends(require_admin),
):
    """Toggle a user's UI access or administrator flag.

    root can never be demoted or locked out (self-recovery guarantee),
    and an administrator cannot revoke their own access or admin rights
    to avoid locking themselves out of the only management page.
    """
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.username == pam_auth.ADMIN_USER:
        raise HTTPException(400, "The root account cannot be modified")
    if user.id == actor.id:
        raise HTTPException(400, "You cannot change your own access from this page")

    if data.ui_access is not None:
        user.ui_access = data.ui_access
        # Losing UI access also drops admin rights : a locked-out account
        # must not keep management privileges.
        if not data.ui_access:
            user.is_admin = False
    if data.is_admin is not None:
        if data.is_admin and not user.ui_access:
            raise HTTPException(400, "Grant UI access before promoting to administrator")
        user.is_admin = data.is_admin
    db.commit()
    db.refresh(user)
    log.info(
        "User %s updated (ui_access=%s, admin=%s)",
        user.username, user.ui_access, user.is_admin,
    )
    return _to_admin_out(user)


@users_router.delete("/{user_id}", status_code=204)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: models.User = Depends(require_admin),
):
    """Remove a mirror row (revokes UI access).

    Does NOT touch the underlying Linux account or its password : it only
    removes MurOS's record and its UI grant. root and the calling account
    cannot be deleted.
    """
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(404, "User not found")
    if user.username == pam_auth.ADMIN_USER:
        raise HTTPException(400, "The root account cannot be removed")
    if user.id == actor.id:
        raise HTTPException(400, "You cannot remove your own account")
    db.delete(user)
    db.commit()
    log.info("User row removed for %s (UI access revoked)", user.username)
