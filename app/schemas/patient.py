from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

# tenant_id deliberately absent from create/update schemas:
# the server always derives it from the authenticated user's token.


class PatientBase(BaseModel):
    code: str = Field(max_length=20)
    name: str = Field(max_length=255)
    age: int = Field(ge=0, le=150)
    gender: str = Field(pattern="^(Male|Female)$")
    status: str = Field(default="Normal", pattern="^(Normal|Positive|Suspected)$")
    confidence: int | None = Field(default=None, ge=0, le=100)
    last_visit: date | None = None
    history: list[str] = Field(default_factory=list)


class PatientCreate(PatientBase):
    pass


class PatientUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    age: int | None = Field(default=None, ge=0, le=150)
    gender: str | None = Field(default=None, pattern="^(Male|Female)$")
    status: str | None = Field(default=None, pattern="^(Normal|Positive|Suspected)$")
    confidence: int | None = Field(default=None, ge=0, le=100)
    last_visit: date | None = None
    history: list[str] | None = None


class PatientOut(PatientBase):
    id: UUID
    tenant_id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
