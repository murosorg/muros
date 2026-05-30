# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Routes HTTP de l'API MurOS (sous-module)."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app import models, pam_auth, schemas, totp
from app.auth import create_token, create_mfa_token, current_user, decode_mfa_token
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

    # Password step passed. If the account has TOTP enabled, do not issue
    # an access token yet: hand back a short-lived MFA token that must be
    # exchanged with a valid 6-digit code at /login/verify.
    if user.totp_enabled and user.totp_secret:
        return schemas.LoginResponse(
            mfa_required=True,
            mfa_token=create_mfa_token(user),
        )

    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return schemas.LoginResponse(
        access_token=create_token(user),
        must_change_password=user.must_change_password,
    )


@auth_router.post("/login/verify", response_model=schemas.LoginResponse)
def login_verify(
    request: Request, data: schemas.MfaVerifyRequest, db: Session = Depends(get_db)
):
    """Second step of a two-factor login: verify the TOTP code."""
    payload = decode_mfa_token(data.mfa_token)
    user = db.get(models.User, int(payload.get("sub", 0)))
    if user is None or not user.totp_enabled or not user.totp_secret:
        raise HTTPException(401, "Invalid token")
    client_ip = (request.client.host if request.client else "?") or "?"
    if not totp.verify(user.totp_secret, data.code):
        _auth_log.warning("2FA failed for %s from %s", user.username, client_ip)
        raise HTTPException(401, "Invalid verification code")
    user.last_login = datetime.now(timezone.utc)
    db.commit()
    return schemas.LoginResponse(
        access_token=create_token(user),
        must_change_password=user.must_change_password,
    )


@auth_router.get("/me", response_model=schemas.UserOut)
def me(user: models.User = Depends(current_user)):
    return user


@auth_router.get("/2fa/status", response_model=schemas.TwoFAStatusOut)
def twofa_status(user: models.User = Depends(current_user)):
    return schemas.TwoFAStatusOut(enabled=bool(user.totp_enabled))


@auth_router.post("/2fa/setup", response_model=schemas.TwoFASetupOut)
def twofa_setup(
    user: models.User = Depends(current_user), db: Session = Depends(get_db)
):
    """Start enrolment: generate a fresh secret (stored, not yet enabled).

    Returns the otpauth URI and a QR code. The secret only becomes active
    once a valid code is confirmed via /2fa/enable.
    """
    secret = totp.new_secret()
    user.totp_secret = secret
    user.totp_enabled = False
    db.commit()
    uri = totp.provisioning_uri(secret, user.username)
    return schemas.TwoFASetupOut(secret=secret, otpauth_uri=uri, qr_svg=totp.qr_svg(uri))


@auth_router.post("/2fa/enable", response_model=schemas.TwoFAStatusOut)
def twofa_enable(
    data: schemas.TwoFACodeRequest,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not user.totp_secret:
        raise HTTPException(400, "Start the 2FA setup first")
    if not totp.verify(user.totp_secret, data.code):
        raise HTTPException(400, "Invalid verification code")
    user.totp_enabled = True
    db.commit()
    return schemas.TwoFAStatusOut(enabled=True)


@auth_router.post("/2fa/disable", response_model=schemas.TwoFAStatusOut)
def twofa_disable(
    data: schemas.TwoFACodeRequest,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Disable 2FA. Requires a current valid code (proves possession)."""
    if not user.totp_enabled:
        return schemas.TwoFAStatusOut(enabled=False)
    if not totp.verify(user.totp_secret, data.code):
        raise HTTPException(400, "Invalid verification code")
    user.totp_secret = None
    user.totp_enabled = False
    db.commit()
    return schemas.TwoFAStatusOut(enabled=False)


@auth_router.post("/change-password", response_model=schemas.UserOut)
def change_password(
    data: schemas.ChangePasswordRequest,
    user: models.User = Depends(current_user),
    db: Session = Depends(get_db),
):
    if not pam_auth.authenticate(user.username, data.current_password):
        raise HTTPException(400, "Current password is incorrect")
    from app import password_policy
    try:
        password_policy.validate(data.new_password, username=user.username)
    except password_policy.PasswordPolicyError as exc:
        # On retourne les raisons separees pour affichage en liste cote UI.
        raise HTTPException(400, "Password rejected: " + " ; ".join(exc.reasons))
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


