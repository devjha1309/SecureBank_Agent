"""Development OAuth2 credentials. Replace with an OIDC provider before production."""

import hashlib
import secrets
from pathlib import Path
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config.settings import get_settings
from core.errors import BankError
from mock_bank.database.session import get_db
from mock_bank.models.entities import AuthSession, RolePermission, User, UserRole, now

hasher = PasswordHasher()
oauth = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


class AuthContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    user_id: str
    customer_id: str | None
    session_id: str
    permissions: frozenset[str]
    roles: tuple[str, ...]


def secret() -> str:
    settings = get_settings()
    configured = settings.jwt_secret.get_secret_value()
    if configured:
        if len(configured) < 32:
            raise RuntimeError("JWT_SECRET must have at least 32 characters")
        return configured
    if settings.environment != "development":
        raise RuntimeError("JWT_SECRET is required outside development")
    path = Path(".local/jwt-secret")
    path.parent.mkdir(mode=0o700, exist_ok=True)
    try:
        with path.open("x") as f:
            path.chmod(0o600)
            f.write(secrets.token_urlsafe(48))
    except FileExistsError:
        pass
    return path.read_text()


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def verify_password(password: str, encoded: str) -> bool:
    try:
        return hasher.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


def token_pair(db: Session, user: User, session: AuthSession | None = None) -> dict:
    settings = get_settings()
    refresh = secrets.token_urlsafe(48)
    if session is None:
        session = AuthSession(
            user_id=user.id, refresh_hash=digest(refresh), expires_at=now() + settings.session_minutes * 60
        )
        db.add(session)
    else:
        session.refresh_hash = digest(refresh)
    db.flush()
    token = jwt.encode(
        {
            "sub": user.id,
            "sid": session.id,
            "iat": now(),
            "exp": now() + settings.access_minutes * 60,
            "iss": settings.jwt_issuer,
            "aud": settings.jwt_audience,
            "jti": secrets.token_hex(16),
        },
        secret(),
        algorithm="HS256",
    )
    return {
        "access_token": token,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": settings.access_minutes * 60,
    }


def authenticate(token: str | None, db: Session) -> AuthContext:
    settings = get_settings()
    try:
        claims = jwt.decode(
            token or "",
            secret(),
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            options={"require": ["sub", "sid", "exp", "iat", "iss", "aud", "jti"]},
        )
        session = db.get(AuthSession, claims["sid"])
        user = db.get(User, claims["sub"])
        if (
            not session
            or session.revoked
            or session.expires_at <= now()
            or not user
            or not user.active
            or session.user_id != user.id
        ):
            raise ValueError("session")
    except (jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise BankError("AUTH_REQUIRED", "Please sign in again.", 401) from None
    roles = tuple(db.scalars(select(UserRole.role).where(UserRole.user_id == user.id)))
    permissions = frozenset(
        db.scalars(select(RolePermission.permission).where(RolePermission.role.in_(roles)))
    )
    return AuthContext(
        user_id=user.id,
        customer_id=user.customer_id,
        session_id=session.id,
        roles=roles,
        permissions=permissions,
    )


def current_auth(
    token: Annotated[str | None, Depends(oauth)], db: Annotated[Session, Depends(get_db)]
) -> AuthContext:
    return authenticate(token, db)
