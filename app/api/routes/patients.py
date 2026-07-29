from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import CurrentTenant, CurrentUser, DbSession
from app.models.patient import Patient
from app.schemas.patient import PatientCreate, PatientOut, PatientUpdate

router = APIRouter(prefix="/patients", tags=["patients"])


def _get_owned_patient(db: Session, tenant_id: UUID, patient_id: UUID) -> Patient:
    """Fetch by id AND tenant, live rows only. A patient of another hospital —
    or one that was soft-deleted — yields the same 404 as a nonexistent id, so
    there is nothing to probe for."""
    patient = db.scalar(
        select(Patient).where(
            Patient.id == patient_id,
            Patient.tenant_id == tenant_id,
            Patient.deleted_at.is_(None),
        )
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found"
        )
    return patient


@router.get("", response_model=list[PatientOut])
def list_patients(
    db: DbSession,
    tenant_id: CurrentTenant,
    _user: CurrentUser,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Patient]:
    stmt = select(Patient).where(
        Patient.tenant_id == tenant_id, Patient.deleted_at.is_(None)
    )
    if q:
        pattern = f"%{q}%"
        stmt = stmt.where(or_(Patient.name.ilike(pattern), Patient.code.ilike(pattern)))
    stmt = (
        stmt.order_by(Patient.last_visit.desc().nulls_last())
        .limit(min(limit, 500))
        .offset(offset)
    )
    return list(db.scalars(stmt))


@router.post("", response_model=PatientOut, status_code=status.HTTP_201_CREATED)
def create_patient(
    body: PatientCreate, db: DbSession, tenant_id: CurrentTenant, _user: CurrentUser
) -> Patient:
    exists = db.scalar(
        select(Patient).where(
            Patient.tenant_id == tenant_id,
            Patient.code == body.code,
            # A soft-deleted record does not keep its code reserved.
            Patient.deleted_at.is_(None),
        )
    )
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Patient code {body.code} already exists in this hospital",
        )
    patient = Patient(tenant_id=tenant_id, **body.model_dump())
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


@router.get("/{patient_id}", response_model=PatientOut)
def get_patient(
    patient_id: UUID, db: DbSession, tenant_id: CurrentTenant, _user: CurrentUser
) -> Patient:
    return _get_owned_patient(db, tenant_id, patient_id)


@router.put("/{patient_id}", response_model=PatientOut)
def update_patient(
    patient_id: UUID,
    body: PatientUpdate,
    db: DbSession,
    tenant_id: CurrentTenant,
    _user: CurrentUser,
) -> Patient:
    patient = _get_owned_patient(db, tenant_id, patient_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(patient, field, value)
    db.commit()
    db.refresh(patient)
    return patient


@router.delete("/{patient_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_patient(
    patient_id: UUID, db: DbSession, tenant_id: CurrentTenant, _user: CurrentUser
) -> None:
    """Soft delete — the record stops being visible but is never destroyed.

    Clinical data has retention obligations, and the diagnoses referencing this
    patient must keep resolving. A hard DELETE violated that foreign key and
    surfaced as a 500 for any patient who had ever been screened.
    """
    patient = _get_owned_patient(db, tenant_id, patient_id)
    patient.deleted_at = datetime.now(UTC)
    db.commit()
