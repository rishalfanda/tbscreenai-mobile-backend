"""Offline-first sync logic.

Guarantees:
- Idempotency: every pushed op carries a client-generated client_op_id;
  the unique (tenant_id, client_op_id) constraint on sync_logs means a
  retried request is answered with "skipped" instead of applied twice.
- Conflict safety: medical data is never auto-overwritten. If the client's
  base_updated_at is older than the server row's updated_at, the op is
  recorded as "conflict" and left for manual doctor review (FASE 4 UI).
"""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.diagnosis import Diagnosis
from app.models.patient import Patient
from app.models.sync_log import SyncLog
from app.schemas.diagnosis import DiagnosisCreate, DiagnosisStatusUpdate
from app.schemas.patient import PatientCreate, PatientUpdate
from app.schemas.sync import SyncPushItem, SyncPushResult

SyncableRow = Patient | Diagnosis

# Typed rather than left as dict[str, type[Base]]: both rows carry id, tenant_id
# and version, and saying so lets the checker verify the generic update path
# instead of trusting it.
_ENTITY_MODEL: dict[str, type[Patient] | type[Diagnosis]] = {
    "patient": Patient,
    "diagnosis": Diagnosis,
}


def _as_utc(dt: datetime) -> datetime:
    """Compare timestamps safely regardless of source.

    Postgres returns tz-aware datetimes; a naive one can still arrive (e.g. a
    backend on SQLite, or a client omitting the offset). Treat naive as UTC so
    conflict detection never crashes on a mixed comparison.

    The second-level truncation is NOT cosmetic: the tablet caches server
    timestamps through drift, which stores DateTime as unix seconds, so a
    client's base_updated_at genuinely carries no sub-second information.
    Comparing at full precision would flag a conflict on every tablet edit.
    That is also precisely why this path cannot detect a real same-second
    conflict — which is what base_version is for. See _has_conflict.
    """
    aware = dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
    return aware.replace(microsecond=0)


def _has_conflict(row: SyncableRow, item: SyncPushItem) -> bool:
    """Has the server row moved on since the client last read it?

    Version first: an integer the server bumps on every UPDATE, compared
    exactly. If the client sent one and it differs, somebody wrote in between.
    No precision caveats, no clock skew.

    Timestamps are the fallback for clients that do not send a version yet.
    That path is second-granular and so CANNOT separate two edits landing in
    the same second — it lets the later write win, silently. Medical data
    deserves better, which is the whole reason base_version exists.
    """
    if item.base_version is not None:
        return row.version != item.base_version
    if item.base_updated_at is not None:
        return _as_utc(row.updated_at) > _as_utc(item.base_updated_at)
    return False


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
        stmt = select(model).where(
            model.id == item.entity_id, model.tenant_id == tenant_id
        )
        if hasattr(model, "deleted_at"):
            # A soft-deleted record is gone as far as clients are concerned.
            stmt = stmt.where(model.deleted_at.is_(None))
        # cast, not a type: ignore. `model` is picked from _ENTITY_MODEL at
        # runtime, so SQLAlchemy widens select(model) back to the Base class
        # and the concrete row type is genuinely unrecoverable by inference.
        # _ENTITY_MODEL is the thing that guarantees it is one of these two.
        row = cast(SyncableRow | None, db.scalar(stmt))
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
        if _has_conflict(row, item):
            _log(db, tenant_id=tenant_id, item=item, device_id=device_id,
                 status="conflict", entity_id=row.id)
            db.commit()
            return SyncPushResult(
                client_op_id=item.client_op_id,
                status="conflict",
                entity_id=row.id,
                detail="Server version is newer — manual review required",
            )

        # Both write paths validate through the same schemas. Skipping them
        # here once let a tablet store arbitrary diagnosis statuses and record
        # disagreements with no clinical note — the REST endpoint rejects both.
        if item.entity_type == "patient":
            fields = PatientUpdate.model_validate(item.payload).model_dump(
                exclude_unset=True
            )
        else:
            # Only the doctor's verdict is client-mutable on a diagnosis;
            # DiagnosisStatusUpdate carries no other field, so anything else
            # in the payload is ignored rather than trusted.
            fields = DiagnosisStatusUpdate.model_validate(item.payload).model_dump()
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
        patient_data = PatientCreate.model_validate(item.payload)
        row = Patient(tenant_id=tenant_id, **patient_data.model_dump())
    else:
        diagnosis_data = DiagnosisCreate.model_validate(item.payload)
        row = Diagnosis(
            tenant_id=tenant_id,
            created_by=user_id,
            findings=diagnosis_data.findings.model_dump(),
            **diagnosis_data.model_dump(exclude={"findings"}, exclude_none=True),
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
