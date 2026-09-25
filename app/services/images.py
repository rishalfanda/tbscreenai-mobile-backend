"""Where screening images live, and the rules for putting them there.

Every image is stored under its hospital's prefix, and the key is built here
from the caller's own tenant, never taken from a request. A client holds only
an opaque image id; the hospital half of the key comes from its token. So an id
belonging to another hospital does not resolve to that hospital's image, it
resolves to nothing, and isolation needs no check that could be forgotten.

Image quality is kept by doing nothing to the picture. PNG and JPEG are stored
as the exact bytes that arrived. A DICOM file is stored as its de-identified
copy, whose pixel data is byte-for-byte the pixel data that arrived. Nothing
here resizes, converts, re-encodes, or recompresses.
"""

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.diagnosis import Diagnosis
from app.services.deidentify import deidentify_dicom, is_dicom
from app.services.image_validation import detect_format
from app.services.storage import ObjectStorage

_ROOT = "tenants"

_MEDIA_TYPES = {"PNG": "image/png", "JPEG": "image/jpeg", "DICOM": "application/dicom"}

# How long an image may wait for a diagnosis to claim it before the sweep may
# remove it. A doctor saves within minutes of inferring; a week leaves room for
# an interrupted session, a weekend, and a slow connection.
ORPHAN_GRACE = timedelta(days=7)


def tenant_prefix(tenant_id: uuid.UUID) -> str:
    return f"{_ROOT}/{tenant_id}/images/"


def image_key(tenant_id: uuid.UUID, image_id: uuid.UUID) -> str:
    return f"{tenant_prefix(tenant_id)}{image_id}"


def media_type(data: bytes) -> str:
    """The content type to serve stored bytes with, read from the bytes."""
    return _MEDIA_TYPES.get(detect_format(data) or "", "application/octet-stream")


def store_screening_image(
    storage: ObjectStorage, tenant_id: uuid.UUID, data: bytes
) -> uuid.UUID:
    """Keep an uploaded image for the caller's hospital and return its id.

    A DICOM file is de-identified first, and a DeidentificationError from that
    step means nothing was stored. Storage failures surface as StorageError.
    """
    if is_dicom(data):
        data = deidentify_dicom(data).data
    image_id = uuid.uuid4()
    storage.put_object(image_key(tenant_id, image_id), data, content_type=media_type(data))
    return image_id


def find_orphans(
    db: Session,
    storage: ObjectStorage,
    now: datetime,
    grace: timedelta = ORPHAN_GRACE,
) -> list[str]:
    """Keys of stored images no diagnosis claims, older than the grace period.

    An image is written the moment it is inferred, before anyone decides to
    save a diagnosis. When nobody does, it belongs to no patient record and
    has no clinical use, and keeping it forever would only keep a copy of
    someone's X-ray around for no reason. Images a diagnosis does reference
    are never returned here, however old: the decision of 13 September keeps
    those permanently.

    Reads every claimed path into memory, which is fine at pilot scale; at a
    few hundred thousand images this wants a set difference in the database.
    """
    claimed = set(
        db.scalars(select(Diagnosis.image_path).where(Diagnosis.image_path.is_not(None)))
    )
    cutoff = now - grace
    return [
        stored.key
        for stored in storage.list_objects(f"{_ROOT}/")
        if stored.last_modified < cutoff and stored.key not in claimed
    ]
