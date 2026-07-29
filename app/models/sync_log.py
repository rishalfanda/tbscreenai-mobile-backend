import uuid

from sqlalchemy import String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UuidPkMixin

SYNC_OPERATIONS = ("create", "update", "delete")
SYNC_STATUSES = ("applied", "conflict", "skipped")


class SyncLog(Base, UuidPkMixin, TimestampMixin, TenantMixin):
    """Audit + idempotency ledger for offline-first sync.

    The tablet sends a client-generated op id with every pushed change;
    the unique (tenant_id, client_op_id) constraint guarantees a retried
    request can never be applied twice.
    """

    __tablename__ = "sync_logs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "client_op_id", name="uq_synclog_tenant_op"),
    )

    client_op_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    operation: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
