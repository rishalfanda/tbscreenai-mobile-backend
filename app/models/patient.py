from datetime import date

from sqlalchemy import Date, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UuidPkMixin


class Patient(Base, UuidPkMixin, TimestampMixin, TenantMixin):
    __tablename__ = "patients"
    # Patient code (TB000001, ...) is unique per hospital, not globally.
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_patient_tenant_code"),)

    code: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    age: Mapped[int] = mapped_column(Integer, nullable=False)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)  # Male | Female
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Normal")
    confidence: Mapped[int | None] = mapped_column(Integer)
    last_visit: Mapped[date | None] = mapped_column(Date)
    # List of history strings — matches the frontend's Patient.history contract.
    history: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
