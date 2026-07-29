import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UuidPkMixin

# Validation status values (kept in sync with the Flutter app):
#   pending | agreed | disagreed
DIAGNOSIS_STATUSES = ("pending", "agreed", "disagreed")


class Diagnosis(Base, UuidPkMixin, TimestampMixin, TenantMixin):
    __tablename__ = "diagnoses"

    patient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("patients.id"), index=True, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))

    is_positive: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    # {"consolidation": float, "cavity": float, "effusion": float,
    #  "fibrotic": float, "calcification": float} — frontend findings contract.
    findings: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    doctor_note: Mapped[str | None] = mapped_column(Text)
    image_path: Mapped[str | None] = mapped_column(String(500))
    diagnosed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
