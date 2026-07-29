from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPkMixin


class Hospital(Base, UuidPkMixin, TimestampMixin):
    """A tenant. All medical data is scoped to exactly one hospital."""

    __tablename__ = "hospitals"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
