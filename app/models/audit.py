"""Who read or changed what, and when.

`sync_logs` records synchronisation operations — what a device pushed and
whether it applied. It says nothing about a doctor opening a patient record on
screen, and medical-record regulation asks for exactly that: a trail of access,
not just of writes.

The table is append-only by design. There is no update path and no delete path
in the application, because a trail that can be edited is not a trail. Rows are
written even when the request was refused: a 403 on another hospital's patient
is more interesting than a 200 on your own.

What is deliberately absent matters as much as what is here. No request bodies,
no response bodies, no query strings, no patient names. An audit log that
copies the record it is auditing becomes a second store of the same protected
data, doubling the surface that has to be defended and the retention rules that
apply to it. Identifiers are enough to reconstruct who touched what; the record
itself already lives in its own table.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UuidPkMixin

# The verbs worth distinguishing when someone asks "who saw this patient".
ACCESS_ACTIONS = ("read", "create", "update", "delete")

# Kinds of thing an action lands on. Kept as a short string rather than an enum
# so a new resource does not need a migration to become auditable.
RESOURCE_TYPES = ("patient", "diagnosis", "device", "image", "session", "sync")


class AccessLog(Base, UuidPkMixin):
    """One recorded access attempt, successful or not."""

    __tablename__ = "access_logs"

    # Not TimestampMixin: `updated_at` on an append-only table would be a
    # field that can only ever lie. Indexed because every audit question
    # ("who opened this record last month") is bounded by time first.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    # Nullable, unlike every other table: a failed login has no tenant yet, and
    # refusing to record it would blind the log to exactly the events worth
    # watching.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hospitals.id"), nullable=True, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id"), nullable=True, index=True
    )
    # A snapshot, not a join. Roles change; the trail has to say what the actor
    # was allowed to do at the time, not what they are allowed to do today.
    actor_role: Mapped[str | None] = mapped_column(String(20), nullable=True)

    action: Mapped[str] = mapped_column(String(10), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(
        String(20), nullable=True, index=True
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, nullable=True, index=True
    )

    method: Mapped[str] = mapped_column(String(10), nullable=False)
    # Path only, never the query string. A search parameter can carry a patient
    # name, and that name has no business being copied here.
    path: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)

    # Long enough for IPv6. Nullable because a request through a proxy that
    # strips the header leaves nothing honest to record, and an invented value
    # is worse than an empty one.
    client_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    # Ties this row to the application log lines for the same request.
    request_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
