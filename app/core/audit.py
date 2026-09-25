"""Recording every attempt on the API, refused or not.

Written as middleware rather than a call inside each route, because a trail
with holes answers no question worth asking. A route added next month is
audited without anyone remembering to add it; an explicit call is one that can
be forgotten, and the forgetting is invisible until someone needs the record.

The actor cannot be read here directly — authentication happens in a
dependency, inside the route, after middleware has already started. So
`get_current_user` leaves a snapshot of the user on `request.state` and this
reads it afterwards. When authentication failed there is nothing to read, and
the row is written anyway with an empty actor: a rejected token is exactly the
event an audit trail exists for.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.database import SessionLocal
from app.models.audit import AccessLog

logger = logging.getLogger(__name__)

# Paths that say nothing about who touched which record. Health checks run
# every few seconds from the container runtime; auditing them would bury the
# clinical events under machine noise.
_UNAUDITED = ("/health", "/docs", "/redoc", "/openapi.json")

# HTTP verb to the word an auditor would use.
_ACTIONS = {
    "GET": "read",
    "HEAD": "read",
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}

@dataclass(frozen=True)
class AuditActor:
    """Who made the request, as plain values copied while they were readable.

    Not the User row itself. This is read after the route has returned and the
    request's session has been closed, and a rollback on the way — a refused
    duplicate, a sync item that conflicts — expires every object in that
    session. An expired row with no session left cannot be read at all, so
    the middleware crashed on exactly the requests the trail most needs: the
    response became a 500 and the row was never written. Plain values survive
    both the rollback and the closed session.
    """

    id: uuid.UUID
    role: str
    tenant_id: uuid.UUID | None


# Path segment to the kind of thing it names.
_RESOURCES = {
    "patients": "patient",
    "diagnoses": "diagnosis",
    "devices": "device",
    "auth": "session",
    "sync": "sync",
}


def _classify(path: str) -> tuple[str | None, uuid.UUID | None]:
    """Read the resource kind and id out of the path, if it names one.

    A listing has a kind but no id, which is the honest answer: the request
    touched every row the caller may see, not one of them.
    """
    segments = [segment for segment in path.split("/") if segment]
    kind: str | None = None
    identifier: uuid.UUID | None = None
    for segment in segments:
        if kind is None and segment in _RESOURCES:
            kind = _RESOURCES[segment]
            continue
        if kind is not None and identifier is None:
            try:
                identifier = uuid.UUID(segment)
            except ValueError:
                continue
    return kind, identifier


def _correlation_id(supplied: str | None) -> str:
    """Accept the caller's correlation id only when it is a well-formed UUID.

    The column holds 36 characters. A longer header would make the insert fail,
    and because an audit failure is logged rather than raised, a client could
    quietly switch off its own trail simply by sending a long enough value.
    Requiring a UUID also keeps control characters and other junk out of both
    the table and the log lines that quote it.

    A malformed value is replaced rather than refused: rejecting a clinical
    request over a correlation header would trade a cosmetic problem for an
    operational one.
    """
    if supplied is not None:
        try:
            return str(uuid.UUID(supplied))
        except ValueError:
            logger.warning("Ignoring malformed X-Request-Id header")
    return str(uuid.uuid4())


@contextmanager
def _own_session() -> Iterator[Session]:
    """A session of the middleware's own, deliberately.

    The request's session may have been rolled back by the very failure we are
    here to record. A trail that vanishes whenever the thing it witnessed went
    wrong is worse than no trail: it would be silently biased toward success.

    Held behind `app.state.audit_session` so tests can point the middleware at
    the same database the rest of the suite uses.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def install_access_audit(app: FastAPI) -> None:
    """Attach the middleware and its default session source."""
    app.state.audit_session = _own_session
    app.add_middleware(AccessAuditMiddleware)


class AccessAuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _correlation_id(request.headers.get("X-Request-Id"))
        request.state.request_id = request_id
        request.state.actor = None

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id

        if not request.url.path.startswith(_UNAUDITED):
            self._record(request, response, request_id)
        return response

    def _record(self, request: Request, response: Response, request_id: str) -> None:
        actor = getattr(request.state, "actor", None)
        kind, identifier = _classify(request.url.path)
        # The tenant the request was actually scoped to, which for a
        # super_admin is the hospital named in X-Tenant-Id rather than their
        # own — they have none. Falling back to the actor's home tenant covers
        # the endpoints that never resolve one, such as login.
        tenant_id = getattr(request.state, "tenant_id", None) or getattr(
            actor, "tenant_id", None
        )
        # request.url.path, never request.url — a query string can carry a
        # patient name, and that name has no business being copied here.
        entry = AccessLog(
            tenant_id=tenant_id,
            actor_user_id=getattr(actor, "id", None),
            actor_role=getattr(actor, "role", None),
            action=_ACTIONS.get(request.method, request.method.lower()[:10]),
            resource_type=kind,
            resource_id=identifier,
            method=request.method,
            path=request.url.path[:255],
            status_code=response.status_code,
            client_ip=request.client.host if request.client else None,
            request_id=request_id,
        )
        try:
            with request.app.state.audit_session() as session:
                session.add(entry)
                session.commit()
        except Exception:
            # Logged loudly, but the response still goes out. Refusing a
            # clinician access to a patient record because the audit table
            # hiccupped trades a bookkeeping failure for a clinical one. That
            # is the one decision in this file that is policy rather than
            # engineering, and it is flagged in the PR for review.
            logger.error(
                "Access audit write failed for %s %s (request %s)",
                request.method,
                request.url.path,
                request_id,
                exc_info=True,
            )
