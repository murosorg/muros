# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import models, pam_auth, schemas
from app.auth import create_token, current_user
from app.db import get_db

_auth_dep = [Depends(current_user)]
_auth_log = logging.getLogger("muros.auth")


# --- Auth ---
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/login", response_model=schemas.LoginResponse)
def login(request: Request, data: schemas.LoginRequest, db: Session = Depends(get_db)):
    # Credentials are validated against the system PAM stack: the web UI
    # account and the SSH account are the same Linux user. The DB row is
    # only a thin mirror used to carry the JWT subject, the admin flag,
    # the must_change_password hint and the last login timestamp.
    client_ip = (request.client.host if request.client else "?") or "?"
    if not pam_auth.authenticate(data.username, data.password):
        # Log avec l'IP du client pour que fail2ban puisse parser.
        # Format consomme par /etc/fail2ban/filter.d/muros-api.conf.
        _auth_log.warning("auth failed for %s from %s", data.username, client_ip)
        raise HTTPException(401, "Invalid credentials")

    # PAM accepted the credentials, but passing PAM is not enough to enter
    # the web UI. The UI and SSH share the system accounts, so any local
    # Linux user could authenticate here. Only accounts explicitly granted
    # ui_access (root by default) are allowed in; every other account is
    # refused until root enables it from Access > Users.
    user = db.query(models.User).filter(models.User.username == data.username).first()
    is_root = data.username == pam_auth.ADMIN_USER
    if user is None:
        # No mirror row yet. root is materialized as a granted admin; any
        # other account is materialized locked out (ui_access=False) so it
        # shows up in the Access > Users list for root to grant later.
        user = models.User(
            username=data.username,
            password_hash="!",  # PAM is the source of truth, not this column
            is_admin=is_root,
            ui_access=is_root,
            must_change_password=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    # root is always allowed in (self-recovery: it can never be locked
    # out by a bad grant change). Any other account needs ui_access.
    if not is_root and not user.ui_access:
        _auth_log.warning(
            "auth refused (no UI access) for %s from %s", data.username, client_ip
        )
        raise HTTPException(403, "This account is not allowed to access the MurOS UI")

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return schemas.LoginResponse(
        access_token=create_token(user),
        must_change_password=user.must_change_password,
    )


@auth_router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(current_user)):
    return user


@auth_router.post("/change-password", response_model=schemas.UserOut)
def change_password(
    data: schemas.ChangePasswordRequest,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not pam_auth.authenticate(user.username, data.current_password):
        raise HTTPException(400, "Mot de passe actuel incorrect")
    from app import password_policy
    try:
        password_policy.validate(data.new_password, username=user.username)
    except password_policy.PasswordPolicyError as exc:
        # On retourne les raisons separees pour affichage en liste cote UI.
        raise HTTPException(400, "Mot de passe refuse : " + " ; ".join(exc.reasons))
    # Write the new password to the system account (chpasswd). Because the
    # web UI and SSH share the same Linux user, this also rotates the SSH
    # password in one shot.
    try:
        pam_auth.set_password(user.username, data.new_password)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))
    user.must_change_password = False
    db.commit()
    db.refresh(user)
    return user


@auth_router.get("/password-policy", response_model=schemas.PasswordPolicyOut)
def get_password_policy():
    """Expose les regles de mot de passe pour affichage cote UI.

    Garde meme si non consommee aujourd'hui : utilisee par le formulaire
    de changement de mot de passe pour afficher les exigences (au lieu
    de les hardcoder en TSX). 10 lignes, cout maintenance nul.
    """
    from app import password_policy
    return {
        "min_length": password_policy.MIN_LENGTH,
        "require_uppercase": True,
        "require_lowercase": True,
        "require_digit": True,
        "require_special": True,
        "forbid_common": True,
        "forbid_username": True,
    }


