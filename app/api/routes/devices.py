"""Registering field units and listing them.

Fleet management is administrative, not clinical, so doctors are absent from
this router entirely — a tablet user has no reason to enumerate the estate.
Registration is narrower still: issuing a credential that lets hardware sync a
hospital's patient data is a central act, so it belongs to super_admin, who
must name the owning hospital explicitly via X-Tenant-Id.
"""

import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import (
    DEVICE_READ_ROLES,
    DEVICE_REGISTER_ROLES,
    CurrentTenant,
    CurrentUser,
    DbSession,
    require_roles,
)
from app.core.security import hash_password
from app.models.device import Device
from app.schemas.device import DeviceCreate, DeviceOut, DeviceRegistered

router = APIRouter(prefix="/devices", tags=["devices"])

# 32 bytes of entropy, URL-safe so it survives config files and QR codes on the
# way to the unit. Long enough that guessing is not a threat model worth
# modelling.
_CREDENTIAL_BYTES = 32


@router.post(
    "",
    response_model=DeviceRegistered,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(*DEVICE_REGISTER_ROLES))],
)
def register_device(
    payload: DeviceCreate,
    db: DbSession,
    tenant_id: CurrentTenant,
    _user: CurrentUser,
) -> DeviceRegistered:
    """Register a unit and issue its credential, which is shown exactly once.

    The MAC arrives already normalised by the schema, so the unique constraint
    compares like with like.
    """
    credential = secrets.token_urlsafe(_CREDENTIAL_BYTES)
    device = Device(
        tenant_id=tenant_id,
        mac_address=payload.mac_address,
        credential_hash=hash_password(credential),
    )
    db.add(device)
    try:
        db.commit()
    except IntegrityError:
        # The unique constraint fired: this MAC is already registered, possibly
        # to another hospital. Rolling back and answering 409 keeps that a
        # refusal rather than a 500, and deliberately does not say which
        # hospital holds it — that would leak the estate across tenants.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A device with this MAC address is already registered",
        ) from None
    db.refresh(device)

    # Built by hand rather than from_attributes: the credential exists only in
    # this function's scope and was never on the model, which is the property
    # that keeps it out of every other response.
    return DeviceRegistered(
        id=device.id,
        tenant_id=device.tenant_id,
        mac_address=device.mac_address,
        status=device.status,
        created_at=device.created_at,
        updated_at=device.updated_at,
        credential=credential,
    )


@router.get(
    "",
    response_model=list[DeviceOut],
    dependencies=[Depends(require_roles(*DEVICE_READ_ROLES))],
)
def list_devices(
    db: DbSession,
    tenant_id: CurrentTenant,
    _user: CurrentUser,
    limit: int = 100,
    offset: int = 0,
) -> list[Device]:
    """Devices belonging to the caller's hospital, newest first.

    Scoped by `tenant_id` from the token, never from a query parameter, so an
    admin of one hospital cannot enumerate another's estate.
    """
    stmt = (
        select(Device)
        .where(Device.tenant_id == tenant_id)
        .order_by(Device.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt).all())
