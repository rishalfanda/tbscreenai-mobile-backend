from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    CLINICAL_ROLES,
    READ_ROLES,
    CurrentTenant,
    CurrentUser,
    DbSession,
    require_roles,
)
from app.core.config import get_settings
from app.models.diagnosis import Diagnosis
from app.models.patient import Patient
from app.schemas.diagnosis import (
    DiagnosisCreate,
    DiagnosisOut,
    DiagnosisStatusUpdate,
    InferenceResult,
)
from app.services.image_validation import read_validated_image
from app.services.inference import latest_model_version, run_mock_inference

router = APIRouter(prefix="/diagnoses", tags=["diagnoses"])


def _get_owned_diagnosis(db: Session, tenant_id: UUID, diagnosis_id: UUID) -> Diagnosis:
    diagnosis = db.scalar(
        select(Diagnosis).where(
            Diagnosis.id == diagnosis_id, Diagnosis.tenant_id == tenant_id
        )
    )
    if diagnosis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Diagnosis not found"
        )
    return diagnosis


@router.post(
    "/infer",
    response_model=InferenceResult,
    dependencies=[Depends(require_roles(*CLINICAL_ROLES))],
)
def infer(
    image: UploadFile,
    db: DbSession,
    _tenant_id: CurrentTenant,
    _user: CurrentUser,
) -> InferenceResult:
    """Accepts a chest X-ray image and returns a structured MOCK result.

    Validation is by file signature and size, not by the client's declared
    content_type — that header is attacker-controlled and previously was the
    only check. The bytes are not stored yet; storage lands with the real
    model integration, at which point this is the function that already holds
    them.
    """
    read_validated_image(image, get_settings().max_upload_bytes)
    return run_mock_inference(latest_model_version(db), image.filename)


@router.get(
    "",
    response_model=list[DiagnosisOut],
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def list_diagnoses(
    db: DbSession,
    tenant_id: CurrentTenant,
    _user: CurrentUser,
    patient_id: UUID | None = None,
    status_filter: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Diagnosis]:
    stmt = select(Diagnosis).where(Diagnosis.tenant_id == tenant_id)
    if patient_id is not None:
        stmt = stmt.where(Diagnosis.patient_id == patient_id)
    if status_filter is not None:
        stmt = stmt.where(Diagnosis.status == status_filter)
    stmt = stmt.order_by(Diagnosis.diagnosed_at.desc()).limit(min(limit, 500)).offset(offset)
    return list(db.scalars(stmt))


@router.post(
    "",
    response_model=DiagnosisOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*CLINICAL_ROLES))],
)
def create_diagnosis(
    body: DiagnosisCreate, db: DbSession, tenant_id: CurrentTenant, user: CurrentUser
) -> Diagnosis:
    # The referenced patient must belong to the same tenant.
    patient = db.scalar(
        select(Patient).where(
            Patient.id == body.patient_id, Patient.tenant_id == tenant_id
        )
    )
    if patient is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found"
        )

    data = body.model_dump(exclude={"findings"}, exclude_none=True)
    diagnosis = Diagnosis(
        tenant_id=tenant_id,
        created_by=user.id,
        findings=body.findings.model_dump(),
        **data,
    )
    db.add(diagnosis)
    db.commit()
    db.refresh(diagnosis)
    return diagnosis


@router.get(
    "/{diagnosis_id}",
    response_model=DiagnosisOut,
    dependencies=[Depends(require_roles(*READ_ROLES))],
)
def get_diagnosis(
    diagnosis_id: UUID, db: DbSession, tenant_id: CurrentTenant, _user: CurrentUser
) -> Diagnosis:
    return _get_owned_diagnosis(db, tenant_id, diagnosis_id)


@router.patch(
    "/{diagnosis_id}/status",
    response_model=DiagnosisOut,
    dependencies=[Depends(require_roles(*CLINICAL_ROLES))],
)
def update_status(
    diagnosis_id: UUID,
    body: DiagnosisStatusUpdate,
    db: DbSession,
    tenant_id: CurrentTenant,
    _user: CurrentUser,
) -> Diagnosis:
    """Doctor validation verdict — mirrors the Flutter ValidationScreen flow.

    The "disagreeing requires a clinical note" rule lives on
    DiagnosisStatusUpdate rather than here, so the offline sync path is held to
    it too. FastAPI turns the schema's rejection into the same 422 this handler
    used to raise by hand.
    """
    diagnosis = _get_owned_diagnosis(db, tenant_id, diagnosis_id)
    diagnosis.status = body.status
    diagnosis.doctor_note = body.doctor_note
    db.commit()
    db.refresh(diagnosis)
    return diagnosis
