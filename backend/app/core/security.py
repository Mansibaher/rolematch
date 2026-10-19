"""Password hashing (Argon2id) and opaque server-side sessions."""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuthSession, User

_hasher = PasswordHasher()  # argon2-cffi defaults: Argon2id, RFC 9106 low-memory profile


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    db.add(
        AuthSession(
            user_id=user.id,
            token_hash=_token_hash(token),
            expires_at=now + timedelta(hours=get_settings().session_ttl_hours),
            last_seen_at=now,
        )
    )
    db.commit()
    return token


def resolve_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    sess = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if sess is None or sess.expires_at <= datetime.now(UTC):
        return None
    return db.get(User, sess.user_id)


def revoke_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        db.commit()
