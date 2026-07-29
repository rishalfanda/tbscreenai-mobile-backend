from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


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
    """The doctor's verdict — the ONLY part of a diagnosis a client may change.

    Both write paths validate through this schema: the REST endpoint and the
    offline sync push. That is deliberate. The rules used to live in the REST
    handler only, so a tablet syncing offline work could store an arbitrary
    status, or record a disagreement with no clinical note — bypassing the
    exact safeguard the endpoint enforced.
    """

    status: str = Field(pattern="^(pending|agreed|disagreed)$")
    doctor_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _disagreement_needs_a_reason(self) -> "DiagnosisStatusUpdate":
        # Overruling the AI without stating why leaves no clinical audit trail.
        if self.status == "disagreed" and not (self.doctor_note or "").strip():
            raise ValueError("A clinical note is required when disagreeing")
        return self


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
    # Echo this back as `base_version` when pushing an edit — see SyncPushItem.
    version: int

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
