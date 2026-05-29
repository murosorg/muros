# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import models, schemas
from app.auth import create_token, current_user, hash_password, verify_password
from app.db import get_db

_auth_dep = [Depends(current_user)]
_auth_log = logging.getLogger("muros.auth")


# --- Auth ---
auth_router = APIRouter(prefix="/api/auth", tags=["auth"])


@auth_router.post("/login", response_model=schemas.LoginResponse)
def login(request: Request, data: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == data.username).first()
    if not user or not verify_password(data.password, user.password_hash):
        # Log avec l'IP du client pour que fail2ban puisse parser.
        # Format consomme par /etc/fail2ban/filter.d/muros-api.conf.
        client_ip = (request.client.host if request.client else "?") or "?"
        _auth_log.warning("auth failed for %s from %s", data.username, client_ip)
        raise HTTPException(401, "Invalid credentials")
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
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(400, "Mot de passe actuel incorrect")
    from app import password_policy
    try:
        password_policy.validate(data.new_password, username=user.username)
    except password_policy.PasswordPolicyError as exc:
        # On retourne les raisons separees pour affichage en liste cote UI.
        raise HTTPException(400, "Mot de passe refuse : " + " ; ".join(exc.reasons))
    user.password_hash = hash_password(data.new_password)
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


