from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.models.device import DEVICE_STATUSES, REASON_MAX_LENGTH, normalise_mac

# tenant_id is deliberately absent from DeviceCreate. Which hospital a device
# belongs to is decided by the server from the caller's token, never by the
# request body — the same rule patients follow.

_STATUS_PATTERN = f"^({'|'.join(DEVICE_STATUSES)})$"

# Surrounding whitespace is trimmed before the length check, so "   " is an
# empty reason and refused, not a reason made of spaces.
Reason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=REASON_MAX_LENGTH),
]


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


class DeviceRevoke(BaseModel):
    """Cutting off a unit. The reason is required because the trail is only
    useful if it says why, and "why" is the one thing nobody remembers later.
    """

    reason: Reason = Field(
        description="Why access is being cut, e.g. unit reported lost. Describe "
        "the device's situation; never include patient data."
    )


class DeviceRebind(BaseModel):
    """Moving a registered unit onto a replacement board.

    The same canonicalisation as registration, so the new address is compared
    with the rest of the registry in one spelling.
    """

    mac_address: str = Field(
        description="WLAN MAC of the replacement board. Any common spelling is "
        "accepted and stored in one canonical form."
    )
    reason: Reason = Field(
        description="Why the hardware changed, e.g. board replaced after water "
        "damage. Describe the device's situation; never include patient data."
    )

    @field_validator("mac_address")
    @classmethod
    def _canonicalise(cls, raw: str) -> str:
        return normalise_mac(raw)
