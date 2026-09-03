"""Authentication + role-based access control.

Passwords are bcrypt-hashed; sessions are stateless JWTs. Three roles gate the
API: officer (scan + verify), admin (manage), auditor (read-only).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import bcrypt
import jwt

from ..core.config import get_settings

ROLES = ("officer", "admin", "auditor")

# bcrypt hashes at most the first 72 bytes; truncate explicitly so long inputs
# are accepted deterministically rather than raising.
_BCRYPT_MAX = 72


def _pw_bytes(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX]


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, pw_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_pw_bytes(password), pw_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def create_access_token(sub: str, role: str,
                        expires_minutes: Optional[int] = None) -> str:
    settings = get_settings()
    minutes = expires_minutes or settings.jwt_expire_minutes
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "role": role,
        "iat": now,
        "exp": now + timedelta(minutes=minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_alg)


def decode_token(token: str) -> dict:
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_alg])


def role_allows(role: str, allowed: Iterable[str]) -> bool:
    return role in set(allowed)
