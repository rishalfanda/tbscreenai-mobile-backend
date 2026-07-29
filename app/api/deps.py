"""Auth & tenant-isolation dependencies.

Design rule: NO route handler ever reads tenant_id from the request body or
query string. Tenant scope always comes from `CurrentTenant` below, which is
derived from the verified JWT — cross-hospital leakage is prevented at the
dependency layer, not by remembering to filter in each handler.
"""

from typing import Annotated
from uuid import UUID

import jwt as pyjwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.user import ROLE_SUPER_ADMIN, User

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(credentials.credentials)
    except pyjwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access token required",
        )

    user = db.get(User, UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: str):
    """Route guard: `dependencies=[Depends(require_roles("doctor", "admin_rs"))]`."""

    def _checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(roles)}",
            )
        return user

    return _checker


def get_tenant_id(
    user: CurrentUser,
    x_tenant_id: Annotated[UUID | None, Header()] = None,
) -> UUID:
    """Resolve the tenant every data query MUST be scoped to.

    - doctor / admin_rs: always their own hospital (header is ignored —
      a tenant-bound user can never choose another tenant).
    - super_admin: not tenant-bound, must state the target hospital
      explicitly via the X-Tenant-Id header.
    """
    if user.role == ROLE_SUPER_ADMIN:
        if x_tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="super_admin must provide X-Tenant-Id header",
            )
        return x_tenant_id

    if user.tenant_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not attached to a hospital",
        )
    return user.tenant_id


CurrentTenant = Annotated[UUID, Depends(get_tenant_id)]
DbSession = Annotated[Session, Depends(get_db)]
