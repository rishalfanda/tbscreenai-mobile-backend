from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.models.device import DEVICE_STATUSES, normalise_mac

# tenant_id is deliberately absent from DeviceCreate. Which hospital a device
# belongs to is decided by the server from the caller's token, never by the
# request body — the same rule patients follow.

_STATUS_PATTERN = f"^({'|'.join(DEVICE_STATUSES)})$"


class DeviceCreate(BaseModel):
    mac_address: str = Field(
        description="WLAN MAC. Any common spelling is accepted and stored "
        "in one canonical form."
    )

    @field_validator("mac_address")
    @classmethod
    def _canonicalise(cls, raw: str) -> str:
        # normalise_mac raises ValueError on anything that is not twelve hex
        # digits; Pydantic turns that into a 422 with the field named, so a
        # typo is refused at the edge rather than becoming a device that no
        # sync request can ever be matched to.
        return normalise_mac(raw)


class DeviceOut(BaseModel):
    """What a device looks like to anyone reading the registry.

    `credential_hash` is absent by construction rather than excluded by a
    filter. A field that is never declared cannot be leaked by a future
    endpoint that forgets to exclude it.
    """

    id: UUID
    tenant_id: UUID
    mac_address: str
    status: str = Field(pattern=_STATUS_PATTERN)
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DeviceRegistered(DeviceOut):
    """The registration response, and the only time the credential is readable.

    Only the hash is stored, so this value cannot be recovered or re-sent later
    — a lost credential means registering the device again. That is the point:
    a secret the server can reissue on demand is a secret the server is holding
    in a form someone else can read too.
    """

    credential: str = Field(
        description="Shown once. Store it on the device now; it cannot be "
        "retrieved again."
    )
