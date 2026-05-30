# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (c) 2026 MurOS contributors.
"""Authentication: JWT issuance/validation + FastAPI dependencies.

Credentials are verified against the Linux account database via PAM (see
``app.pam_auth``); MurOS stores no password hash of its own (the mirror
``users.password_hash`` column is a sentinel ``"!"``). This module only
mints and validates the bearer tokens and exposes the ``current_user`` /
``require_admin`` dependencies.
"""
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import models
from app.db import get_db

_SECRET_PATH = Path(os.environ.get("MUROS_SECRET_FILE", "./muros-secret.key"))


def _load_or_create_secret() -> str:
    # En prod, mettre MUROS_JWT_SECRET en env var. Sinon on persiste un secret local.
    env = os.environ.get("MUROS_JWT_SECRET")
    if env:
        return env
    if _SECRET_PATH.exists():
        return _SECRET_PATH.read_text().strip()
    secret = secrets.token_urlsafe(48)
    _SECRET_PATH.write_text(secret)
    _SECRET_PATH.chmod(0o600)
    return secret


JWT_SECRET = _load_or_create_secret()
JWT_ALGO = "HS256"
TOKEN_TTL = timedelta(hours=8)
# Short-lived token issued between the password step and the TOTP step of
# a two-factor login. It only proves the password was accepted; it cannot
# be used as an access token (guarded by the "scope": "mfa" claim).
MFA_TOKEN_TTL = timedelta(minutes=5)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def create_token(user: models.User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "iat": int(now.timestamp()),
        "exp": int((now + TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def create_mfa_token(user: models.User) -> str:
    """Intermediate token proving the password step passed (TOTP pending)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user.id),
        "username": user.username,
        "scope": "mfa",
        "iat": int(now.timestamp()),
        "exp": int((now + MFA_TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)


def decode_mfa_token(token: str) -> dict:
    """Decode and validate an MFA step token; raise 401 otherwise."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if payload.get("scope") != "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return payload


def _decode_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    # An MFA step token must never be accepted as a full access token.
    if payload.get("scope") == "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")
    return payload


def current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> models.User:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentification requise")
    payload = _decode_token(token)
    user_id = int(payload.get("sub", 0))
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


def require_admin(user: models.User = Depends(current_user)) -> models.User:
    """Dependency guarding admin-only endpoints (user management).

    Only accounts flagged is_admin (root, plus any account root has
    promoted) may manage which Linux users are allowed into the web UI.
    """
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator rights required")
    return user
