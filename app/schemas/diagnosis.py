from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class Findings(BaseModel):
    """Finding percentages — mirrors the Flutter ValidationFindings contract."""

    consolidation: float = Field(ge=0, le=100)
    cavity: float = Field(ge=0, le=100)
    effusion: float = Field(ge=0, le=100)
    fibrotic: float = Field(ge=0, le=100)
    calcification: float = Field(ge=0, le=100)


class DiagnosisCreate(BaseModel):
    patient_id: UUID
    is_positive: bool
    confidence: int = Field(ge=0, le=100)
    model_version: str = Field(max_length=50)
    processing_time_ms: int | None = Field(default=None, ge=0)
    findings: Findings
    image_path: str | None = None
    diagnosed_at: datetime | None = None


class DiagnosisStatusUpdate(BaseModel):
    status: str = Field(pattern="^(pending|agreed|disagreed)$")
    doctor_note: str | None = Field(default=None, max_length=500)


class DiagnosisOut(BaseModel):
    id: UUID
    tenant_id: UUID
    patient_id: UUID
    created_by: UUID | None
    is_positive: bool
    confidence: int
    model_version: str
    processing_time_ms: int | None
    findings: Findings
    status: str
    doctor_note: str | None
    image_path: str | None
    diagnosed_at: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InferenceResult(BaseModel):
    """Structured MOCK inference response.

    Real model integration is out of scope — the contract is what matters:
    it mirrors the Flutter DiagnosisOutcome so the tablet can render it as-is.
    """

    is_positive: bool
    confidence: int
    processing_time_ms: int
    model_version: str
    findings: Findings
    is_mock: bool = True
