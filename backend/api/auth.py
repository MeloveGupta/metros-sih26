"""FastAPI auth wiring: JWT bearer dependency + role-based access control.

`security.py` stays framework-agnostic (hashing, token encode/decode); this
module is where that gets turned into FastAPI dependencies. `METROS_AUTH_DISABLED`
bypasses the token check entirely (local development only -- refused in
production by `core.config.production_safety_check`, enforced at API startup).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import jwt as pyjwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..core.config import get_settings
from .security import decode_token, role_allows

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    sub: str
    role: str
    name: str = ""


def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    settings = get_settings()
    if settings.auth_disabled:
        return CurrentUser(sub="dev-officer", role="officer", name="Dev Officer")
    if creds is None:
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        payload = decode_token(creds.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return CurrentUser(sub=payload["sub"], role=payload.get("role", "officer"),
                       name=payload.get("name", ""))


def require_role(*roles: str):
    """Dependency factory: 401 with no/bad token, 403 if the role doesn't match."""

    def _dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not role_allows(user.role, roles):
            raise HTTPException(status_code=403, detail=f"requires one of roles {roles}")
        return user

    return _dep
