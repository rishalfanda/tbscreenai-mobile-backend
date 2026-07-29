"""Rate limiting, currently for the login endpoint only.

`POST /auth/login` is the one unauthenticated endpoint that checks a secret,
which makes it the only one worth guessing at. Nothing bounded attempts before
this, so a script could work through a password list as fast as bcrypt would
answer.

KNOWN LIMIT: the counter is per process. Behind Gunicorn with N workers the
effective allowance is N times the configured one, because each worker keeps
its own tally. That is still a vast improvement on unlimited, but the fix is a
shared store — the same Redis that token revocation (S-2) will need. Point
`limits` at it there rather than raising the number here.
"""

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.config import get_settings

# Fallback if the window cannot be read off the exception; a minute is the
# smallest sensible ask rather than a guess at the real limit.
_DEFAULT_RETRY_AFTER_SECONDS = 60

# Keyed on client IP. Behind a proxy this needs the real client address, which
# means uvicorn must run with --proxy-headers and the proxy must set
# X-Forwarded-For — otherwise every request keys to the proxy and one busy
# hospital locks out the rest.
limiter = Limiter(key_func=get_remote_address)


def login_rate_limit() -> str:
    """Read from settings on each call so tests can override the limit."""
    return get_settings().login_rate_limit


def rate_limit_exceeded_handler(_request: Request, exc: Exception) -> Response:
    """Answer a tripped limit with 429 in this API's own error shape.

    Replaces slowapi's built-in handler for two reasons. It omits Retry-After,
    which leaves a client to invent its own backoff — the tablet would keep
    hammering a server that is already saying stop. And it returns {"error":
    ...} while every other error here returns {"detail": ...}, so a client
    would need a special case for this one response.

    The message stays vague on purpose: telling an attacker the exact budget
    and window tells them precisely how slowly to go to stay under it.
    """
    retry_after = _DEFAULT_RETRY_AFTER_SECONDS
    if isinstance(exc, RateLimitExceeded) and exc.limit is not None:
        try:
            retry_after = int(exc.limit.limit.get_expiry())
        except (AttributeError, TypeError, ValueError):
            pass

    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={"detail": "Too many attempts. Please try again later."},
        headers={"Retry-After": str(retry_after)},
    )
