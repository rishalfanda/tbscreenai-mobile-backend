# Import every model here so Alembic autogenerate sees the full metadata.
from app.models.base import Base
from app.models.diagnosis import Diagnosis
from app.models.hospital import Hospital
from app.models.model_version import ModelVersion
from app.models.patient import Patient
from app.models.sync_log import SyncLog
from app.models.user import User

__all__ = [
    "Base",
    "Diagnosis",
    "Hospital",
    "ModelVersion",
    "Patient",
    "SyncLog",
    "User",
]
