from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.diagnosis import DiagnosisOut
from app.schemas.model_version import ModelVersionOut
from app.schemas.patient import PatientOut


class SyncPushItem(BaseModel):
    # Client-generated UUID; retrying the same op can never double-apply.
    client_op_id: UUID
    entity_type: Literal["patient", "diagnosis"]
    operation: Literal["create", "update"]
    entity_id: UUID | None = None
    # Server-side updated_at the client last saw — used for conflict detection.
    base_updated_at: datetime | None = None
    payload: dict


class SyncPushRequest(BaseModel):
    device_id: str | None = Field(default=None, max_length=100)
    items: list[SyncPushItem] = Field(max_length=500)


class SyncPushResult(BaseModel):
    client_op_id: UUID
    # applied  — change persisted
    # skipped  — op id seen before (idempotent retry), nothing changed
    # conflict — server version is newer; needs manual doctor review
    status: Literal["applied", "skipped", "conflict"]
    entity_id: UUID | None = None
    detail: str | None = None


class SyncPushResponse(BaseModel):
    results: list[SyncPushResult]


class SyncPullResponse(BaseModel):
    server_time: datetime
    patients: list[PatientOut]
    diagnoses: list[DiagnosisOut]
    latest_model: ModelVersionOut | None
