import uuid

from sqlalchemy import Boolean, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPkMixin

# Role values (kept as strings for MVP simplicity; validated in schemas):
#   doctor      — tablet app, tenant-bound
#   admin_rs    — hospital admin web, tenant-bound
#   super_admin — platform admin web, NOT tenant-bound (tenant_id is NULL)
ROLE_DOCTOR = "doctor"
ROLE_ADMIN_RS = "admin_rs"
ROLE_SUPER_ADMIN = "super_admin"
ALL_ROLES = (ROLE_DOCTOR, ROLE_ADMIN_RS, ROLE_SUPER_ADMIN)


class User(Base, UuidPkMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    # NULL only for super_admin
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("hospitals.id"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
