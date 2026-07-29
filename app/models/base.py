import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column


class Base(DeclarativeBase):
    pass


class UuidPkMixin:
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class VersionedMixin:
    """Row version, bumped by SQLAlchemy on every UPDATE.

    Sync conflict detection must NOT rely on `updated_at`. The tablet caches
    server timestamps through drift, which stores DateTime as unix *seconds* —
    so a client's `base_updated_at` is only second-accurate, and two edits
    landing inside the same second are indistinguishable. Comparing timestamps
    there either misses real conflicts (silently overwriting medical data) or
    invents false ones.

    An integer counter has no precision to lose: the client echoes back the
    version it edited, and any mismatch is a conflict. SQLAlchemy also appends
    `WHERE version = :old` to each UPDATE, so a concurrent writer raises
    StaleDataError instead of winning a race.
    """

    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict:  # noqa: N805
        return {"version_id_col": cls.version}


class SoftDeleteMixin:
    """Medical records are never physically removed.

    Two reasons, and the second is easy to miss: retention rules for clinical
    data expect the row to survive, AND a hard DELETE of a patient that has
    diagnoses violates the foreign key — which surfaced as an unhandled 500.

    Every read path must filter `deleted_at IS NULL`.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class TenantMixin:
    """Every medical-data table carries the owning hospital's id.

    Row-level isolation is enforced in the API layer (see app/api/deps.py):
    all queries are filtered by the authenticated user's tenant_id.
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(
            Uuid, ForeignKey("hospitals.id"), index=True, nullable=False
        )
