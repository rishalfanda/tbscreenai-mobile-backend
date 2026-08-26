"""MOCK AI inference.

Real model integration is explicitly out of scope for the MVP. This module
produces a structured result with the same shape and value ranges as the
Flutter MockDiagnosisRepository, so the end-to-end contract can be exercised
before the real model exists.
"""

import random

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.model_version import ModelVersion
from app.schemas.diagnosis import Findings, InferenceResult


def latest_model_version(db: Session) -> str:
    """The one name a result may be attributed to.

    Read from the catalog, not a constant. A constant drifts from
    /sync/model-version the moment either side moves, and a result the tablet
    labels one way while Sync Center reports another cannot be traced back to
    the model that produced it. An empty catalog is a misconfiguration rather
    than a default: serving a verdict nothing can be attributed to defeats the
    point of recording a version at all.
    """
    version = db.scalar(
        select(ModelVersion.version).where(ModelVersion.is_latest.is_(True))
    )
    if version is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No model version is marked current. Screening is unavailable "
                "until the model catalog is populated."
            ),
        )
    return version

def run_mock_inference(
    model_version: str, image_filename: str | None = None
) -> InferenceResult:
    """Return a structured MOCK result, or refuse outright in production.

    The verdict below is a coin flip. A coin flip presented as a clinical
    screening result is the worst failure this system can have, and until now
    nothing stopped it from reaching a real deployment. 503 rather than 500:
    nothing is broken, the real model simply is not installed yet.
    """
    if get_settings().is_production:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Real AI model is not installed. Screening is unavailable "
                "until a trained model replaces this placeholder."
            ),
        )

    is_positive = random.random() < 0.5
    confidence = 75 + random.randint(0, 23)
    processing_ms = 2600 + random.randint(0, 499)

    findings = Findings(
        consolidation=(20 + random.random() * 15) if is_positive else (1 + random.random() * 4),
        cavity=(random.random() * 5) if is_positive else 0.0,
        effusion=(3 + random.random() * 8) if is_positive else (random.random() * 2),
        fibrotic=random.random() * 2,
        calcification=random.random() * 3,
    )

    return InferenceResult(
        is_positive=is_positive,
        confidence=confidence,
        processing_time_ms=processing_ms,
        model_version=model_version,
        findings=findings,
    )
