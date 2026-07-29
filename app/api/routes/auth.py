from uuid import UUID

import jwt as pyjwt
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select

from app.api.deps import DbSession
from app.core.rate_limit import limiter, login_rate_limit
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenPair, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_pair(user: User) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user.id, user.role, user.tenant_id),
        refresh_token=create_refresh_token(user.id, user.role, user.tenant_id),
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenPair)
@limiter.limit(login_rate_limit)
def login(request: Request, body: LoginRequest, db: DbSession) -> TokenPair:
    """Exchange credentials for a token pair.

    The `request` parameter is not used by the handler — slowapi requires it in
    the signature to find the client address. Rate limited because this is the
    only unauthenticated endpoint that checks a secret, and so the only one
    worth guessing at; see app/core/rate_limit.py for the per-process caveat.
    """
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(body.password, user.hashed_password):
        # Same message for both cases — don't leak which emails exist.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )
    return _token_pair(user)


@router.post("/refresh", response_model=TokenPair)
def refresh(body: RefreshRequest, db: DbSession) -> TokenPair:
    try:
        payload = decode_token(body.refresh_token)
    except pyjwt.PyJWTError:
        # `from None` — see app/api/deps.py: why the token failed is not the
        # client's business, and should not ride along in a traceback either.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        ) from None
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token required"
        )

    user = db.get(User, UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    return _token_pair(user)
