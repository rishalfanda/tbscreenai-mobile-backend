"""MOCK AI inference.

Real model integration is explicitly out of scope for the MVP. This module
produces a structured result with the same shape and value ranges as the
Flutter MockDiagnosisRepository, so the end-to-end contract can be exercised
before the real model exists.
"""

import random

from app.schemas.diagnosis import Findings, InferenceResult

MOCK_MODEL_VERSION = "TBScreen v2.1.0"


def run_mock_inference(image_filename: str | None = None) -> InferenceResult:
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
        model_version=MOCK_MODEL_VERSION,
        findings=findings,
    )
