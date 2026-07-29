from datetime import date

from sqlalchemy import Boolean, Date, Float, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPkMixin


class ModelVersion(Base, UuidPkMixin, TimestampMixin):
    """AI model releases. Global (not tenant-scoped) — every hospital pulls
    the same model catalog through the sync endpoints."""

    __tablename__ = "model_versions"

    version: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    file_size_mb: Mapped[float] = mapped_column(Float, nullable=False)
    release_date: Mapped[date] = mapped_column(Date, nullable=False)
    changelog: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    download_url: Mapped[str | None] = mapped_column(String(500))
