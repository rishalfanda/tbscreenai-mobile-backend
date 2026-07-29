from datetime import date

from sqlalchemy import Date, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import (
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UuidPkMixin,
    VersionedMixin,
)


class Patient(
    Base, UuidPkMixin, TimestampMixin, TenantMixin, VersionedMixin, SoftDeleteMixin
):
    __tablename__ = "patients"
    # Patient code (TB000001, ...) is unique per hospital, not globally — and
    # only among live rows, so a code becomes reusable once the record is
    # soft-deleted. A plain UNIQUE constraint would keep blocking it forever.
    __table_args__ = (
        Index(
            "uq_patient_tenant_code",
            "tenant_id",
            "code",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # /sync/pull filters on updated_at — without this it is a sequential scan.
        Index("ix_patients_updated_at", "updated_at"),
    )

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)  # Male | Female
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Normal")
    confidence: Mapped[int | None] = mapped_column(Integer)
    last_visit: Mapped[date | None] = mapped_column(Date)
    # List of history strings — matches the frontend's Patient.history contract.
    history: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
