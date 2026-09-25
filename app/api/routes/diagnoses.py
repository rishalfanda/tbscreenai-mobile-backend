from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, status
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
from app.services.deidentify import BurnedInTextError, DeidentificationError
from app.services.image_validation import read_validated_image
from app.services.images import image_key, media_type, store_screening_image, tenant_prefix
from app.services.inference import latest_model_version, run_mock_inference
from app.services.storage import (
    ObjectNotFoundError,
    ObjectStorage,
    StorageError,
    get_object_storage,
)

router = APIRouter(prefix="/diagnoses", tags=["diagnoses"])

Storage = Annotated[ObjectStorage, Depends(get_object_storage)]

_STORAGE_DOWN = "Image storage is unavailable; try again shortly"


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
    tenant_id: CurrentTenant,
    _user: CurrentUser,
    storage: Storage,
) -> InferenceResult:
    """Accepts a chest X-ray image, keeps it, and returns a MOCK result.

    Validation is by file signature and size, not by the client's declared
    content_type — that header is attacker-controlled and previously was the
    only check.

    The image is stored before the result is returned, so a diagnosis saved
    afterwards can point at it, and so the image can be looked at again when
    the model changes. A DICOM file is de-identified first; one that cannot
    be made safe is refused and nothing is kept. When storage is down the
    request fails rather than answering without the image, because a result
    whose X-ray was silently lost cannot be audited later.
    """
    data = read_validated_image(image, get_settings().max_upload_bytes)
    try:
        image_id = store_screening_image(storage, tenant_id, data)
    except BurnedInTextError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="DICOM file declares text burned into the image. Export it "
            "again from the X-ray console without annotations.",
        ) from None
    except DeidentificationError as error:
        # The messages are fixed text written in deidentify.py, never the
        # file's own contents, so they are safe to hand back.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(error)
        ) from None
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_STORAGE_DOWN
        ) from None
    result = run_mock_inference(latest_model_version(db), image.filename)
    return result.model_copy(update={"image_id": image_id})


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
    body: DiagnosisCreate,
    db: DbSession,
    tenant_id: CurrentTenant,
    user: CurrentUser,
    storage: Storage,
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

    data = body.model_dump(exclude={"findings", "image_id"}, exclude_none=True)
    diagnosis = Diagnosis(
        tenant_id=tenant_id,
        created_by=user.id,
        findings=body.findings.model_dump(),
        image_path=_claim_image(storage, tenant_id, body.image_id),
        **data,
    )
    db.add(diagnosis)
    db.commit()
    db.refresh(diagnosis)
    return diagnosis


def _claim_image(
    storage: ObjectStorage, tenant_id: UUID, image_id: UUID | None
) -> str | None:
    """The stored path for an image id, checked to exist for this hospital.

    The key is built from the caller's own hospital, so an id that belongs to
    another hospital finds nothing here and is refused like an id that was
    never issued, without saying which of the two it was.
    """
    if image_id is None:
        return None
    key = image_key(tenant_id, image_id)
    try:
        stored = storage.exists(key)
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_STORAGE_DOWN
        ) from None
    if not stored:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="image_id does not refer to an image stored for this hospital",
        )
    return key


@router.get(
    "/{diagnosis_id}/image",
    response_class=Response,
    dependencies=[Depends(require_roles(*READ_ROLES))],
    responses={
        200: {
            "description": "The stored image, exactly as kept",
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "application/dicom": {},
            },
        },
        404: {"description": "No such diagnosis for this hospital, or it has no image"},
    },
)
def get_diagnosis_image(
    diagnosis_id: UUID,
    db: DbSession,
    tenant_id: CurrentTenant,
    _user: CurrentUser,
    storage: Storage,
) -> Response:
    """The X-ray a diagnosis was made on, byte for byte as it was stored.

    Another hospital's diagnosis answers 404 like any unknown id. The path
    is also checked against the caller's own prefix before anything is read:
    rows written before image_id existed took their image_path from the
    client, and one of those must never be able to point this endpoint at
    another hospital's image. Every read lands in the access trail as a read
    of this diagnosis, which is the "who opened which image" record the
    security design asks for.
    """
    diagnosis = _get_owned_diagnosis(db, tenant_id, diagnosis_id)
    path = diagnosis.image_path
    if path is None or not path.startswith(tenant_prefix(tenant_id)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Diagnosis has no stored image"
        )
    try:
        data = storage.get_object(path)
    except ObjectNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Diagnosis has no stored image"
        ) from None
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_STORAGE_DOWN
        ) from None
    return Response(
        content=data,
        media_type=media_type(data),
        # A patient's X-ray is not something a browser or proxy should keep a
        # copy of, and its type is the one stated here, never a guess.
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


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
