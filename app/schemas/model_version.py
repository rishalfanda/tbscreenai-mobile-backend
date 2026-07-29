from datetime import date
from uuid import UUID

from pydantic import BaseModel


class ModelVersionOut(BaseModel):
    id: UUID
    version: str
    file_size_mb: float
    release_date: date
    changelog: list[str]
    is_latest: bool
    download_url: str | None

    model_config = {"from_attributes": True}
