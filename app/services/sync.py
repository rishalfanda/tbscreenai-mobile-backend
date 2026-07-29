"""Offline-first sync logic.

Guarantees:
- Idempotency: every pushed op carries a client-generated client_op_id;
  the unique (tenant_id, client_op_id) constraint on sync_logs means a
  retried request is answered with "skipped" instead of applied twice.
- Conflict safety: medical data is never auto-overwritten. If the client's
  base_updated_at is older than the server row's updated_at, the op is
  recorded as "conflict" and left for manual doctor review (FASE 4 UI).
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.diagnosis import Diagnosis
from app.models.patient import Patient
from app.models.sync_log import SyncLog
from app.schemas.diagnosis import DiagnosisCreate
from app.schemas.patient import PatientCreate, PatientUpdate
from app.schemas.sync import SyncPushItem, SyncPushResult

_ENTITY_MODEL = {"patient": Patient, "diagnosis": Diagnosis}


def _as_utc(dt: datetime) -> datetime:
    """Compare timestamps safely regardless of source.

    Postgres returns tz-aware datetimes; a naive one can still arrive (e.g. a
    backend on SQLite, or a client omitting the offset). Treat naive as UTC so
    conflict detection never crashes on a mixed comparison.
    """
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return aware.replace(microsecond=0)


def _already_seen(db: Session, tenant_id: UUID, op_id: UUID) -> SyncLog | None:
    return db.scalar(
        select(SyncLog).where(
            SyncLog.tenant_id == tenant_id, SyncLog.client_op_id == op_id
        )
    )


def _log(
    db: Session,
    *,
    tenant_id: UUID,
    item: SyncPushItem,
    device_id: str | None,
    status: str,
    entity_id: UUID | None,
) -> None:
    db.add(
        SyncLog(
            tenant_id=tenant_id,
            client_op_id=item.client_op_id,
            device_id=device_id,
            entity_type=item.entity_type,
            entity_id=entity_id,
            operation=item.operation,
            status=status,
            payload=item.payload,
        )
    )


def apply_push_item(
    db: Session,
    tenant_id: UUID,
    user_id: UUID,
    device_id: str | None,
    item: SyncPushItem,
) -> SyncPushResult:
    # --- Idempotency gate -------------------------------------------------
    seen = _already_seen(db, tenant_id, item.client_op_id)
    if seen is not None:
        return SyncPushResult(
            client_op_id=item.client_op_id,
            status="skipped",
            entity_id=seen.entity_id,
            detail="Duplicate op — already processed",
        )

    model = _ENTITY_MODEL[item.entity_type]

    # --- Update path: conflict detection ---------------------------------
    if item.operation == "update":
        if item.entity_id is None:
            raise ValueError("entity_id is required for update ops")
        row = db.scalar(
            select(model).where(
                model.id == item.entity_id, model.tenant_id == tenant_id
            )
        )
        if row is None:
            _log(db, tenant_id=tenant_id, item=item, device_id=device_id,
                 status="conflict", entity_id=item.entity_id)
            db.commit()
            return SyncPushResult(
                client_op_id=item.client_op_id,
                status="conflict",
                entity_id=item.entity_id,
                detail="Entity not found on server",
            )
        if (
            item.base_updated_at is not None
            and _as_utc(row.updated_at) > _as_utc(item.base_updated_at)
        ):
            _log(db, tenant_id=tenant_id, item=item, device_id=device_id,
                 status="conflict", entity_id=row.id)
            db.commit()
            return SyncPushResult(
                client_op_id=item.client_op_id,
                status="conflict",
                entity_id=row.id,
                detail="Server version is newer — manual review required",
            )

        if item.entity_type == "patient":
            fields = PatientUpdate.model_validate(item.payload).model_dump(
                exclude_unset=True
            )
        else:
            # For diagnoses only the validation verdict is client-mutable.
            allowed = {k: v for k, v in item.payload.items()
                       if k in ("status", "doctor_note")}
            fields = allowed
        for key, value in fields.items():
            setattr(row, key, value)
        _log(db, tenant_id=tenant_id, item=item, device_id=device_id,
             status="applied", entity_id=row.id)
        db.commit()
        return SyncPushResult(
            client_op_id=item.client_op_id, status="applied", entity_id=row.id
        )

    # --- Create path ------------------------------------------------------
    if item.entity_type == "patient":
        data = PatientCreate.model_validate(item.payload)
        row = Patient(tenant_id=tenant_id, **data.model_dump())
    else:
        data = DiagnosisCreate.model_validate(item.payload)
        row = Diagnosis(
            tenant_id=tenant_id,
            created_by=user_id,
            findings=data.findings.model_dump(),
            **data.model_dump(exclude={"findings"}, exclude_none=True),
        )
    if item.entity_id is not None:
        # Client pre-assigned the UUID offline — keep it so future updates match.
        row.id = item.entity_id
    db.add(row)
    _log(db, tenant_id=tenant_id, item=item, device_id=device_id,
         status="applied", entity_id=row.id)
    db.commit()
    return SyncPushResult(
        client_op_id=item.client_op_id, status="applied", entity_id=row.id
    )
