from datetime import datetime, timezone

from fastapi import APIRouter
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.api.deps import CurrentTenant, CurrentUser, DbSession
from app.models.diagnosis import Diagnosis
from app.models.model_version import ModelVersion
from app.models.patient import Patient
from app.schemas.model_version import ModelVersionOut
from app.schemas.sync import (
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncPushResult,
)
from app.services.sync import apply_push_item

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/push", response_model=SyncPushResponse)
def push(
    body: SyncPushRequest, db: DbSession, tenant_id: CurrentTenant, user: CurrentUser
) -> SyncPushResponse:
    """Apply offline changes from the tablet. Idempotent per client_op_id.

    One bad item must never sink the batch. Every op is isolated: a rejection
    becomes that op's own `conflict` verdict and the rest still run. Without
    this the request 500s and the client loses the verdicts for ops the server
    already committed — it cannot tell which of its offline work landed.
    """
    results: list[SyncPushResult] = []
    for item in body.items:
        try:
            results.append(
                apply_push_item(db, tenant_id, user.id, body.device_id, item)
            )
        except (ValidationError, ValueError, IntegrityError, StaleDataError) as exc:
            # IntegrityError: duplicate client-assigned id, or a patient code
            # already taken in this hospital. StaleDataError: another writer
            # won the race on the version counter. Both mean "this op cannot
            # be applied as-is" — a conflict for a human to look at, not a 500.
            db.rollback()
            results.append(
                SyncPushResult(
                    client_op_id=item.client_op_id,
                    status="conflict",
                    entity_id=item.entity_id,
                    detail=f"Rejected: {exc.__class__.__name__}",
                )
            )
    return SyncPushResponse(results=results)


@router.get("/pull", response_model=SyncPullResponse)
def pull(
    db: DbSession,
    tenant_id: CurrentTenant,
    _user: CurrentUser,
    since: datetime | None = None,
) -> SyncPullResponse:
    """Server changes for this hospital since `since` (full snapshot if omitted),
    plus the latest AI model version.

    Soft-deleted patients are handled differently per mode, on purpose:
    a full snapshot omits them (the client rebuilds its cache from what it
    receives), while a delta includes them as tombstones — `deleted_at` set —
    because that is the only way the tablet learns to drop a row it already has.
    """
    patients_stmt = select(Patient).where(Patient.tenant_id == tenant_id)
    diagnoses_stmt = select(Diagnosis).where(Diagnosis.tenant_id == tenant_id)
    if since is not None:
        patients_stmt = patients_stmt.where(Patient.updated_at > since)
        diagnoses_stmt = diagnoses_stmt.where(Diagnosis.updated_at > since)
    else:
        patients_stmt = patients_stmt.where(Patient.deleted_at.is_(None))

    latest_model = db.scalar(
        select(ModelVersion).where(ModelVersion.is_latest.is_(True))
    )

    return SyncPullResponse(
        server_time=datetime.now(timezone.utc),
        patients=list(db.scalars(patients_stmt)),
        diagnoses=list(db.scalars(diagnoses_stmt)),
        latest_model=(
            ModelVersionOut.model_validate(latest_model) if latest_model else None
        ),
    )


@router.get("/model-version", response_model=ModelVersionOut | None)
def latest_model_version(db: DbSession, _user: CurrentUser):
    """Latest AI model release — powers the ModelUpdateCard check."""
    latest = db.scalar(select(ModelVersion).where(ModelVersion.is_latest.is_(True)))
    return ModelVersionOut.model_validate(latest) if latest else None
