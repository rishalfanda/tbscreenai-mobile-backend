"""Registering field units, listing them, and changing them afterwards.

Fleet management is administrative, not clinical, so doctors are absent from
this router entirely — a tablet user has no reason to enumerate the estate.
Registration is narrower still: issuing a credential that lets hardware sync a
hospital's patient data is a central act, so it belongs to super_admin, who
must name the owning hospital explicitly via X-Tenant-Id.

After registration a unit can be revoked when it is lost, or rebound to a new
MAC when its board is replaced. Each change is committed together with a
`device_events` row saying who made it, why, and what changed, so a device can
never be altered without leaving that record behind.
"""

import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import (
    DEVICE_READ_ROLES,
    DEVICE_REBIND_ROLES,
    DEVICE_REGISTER_ROLES,
    DEVICE_REVOKE_ROLES,
    CurrentTenant,
    CurrentUser,
    DbSession,
    require_roles,
)
from app.core.security import hash_password
from app.models.device import IN_SERVICE_STATUSES, REVOKED_STATUS, Device, DeviceEvent
from app.models.user import User
from app.schemas.device import (
    DeviceCreate,
    DeviceOut,
    DeviceRebind,
    DeviceRegistered,
    DeviceRevoke,
)

router = APIRouter(prefix="/devices", tags=["devices"])

# 32 bytes of entropy, URL-safe so it survives config files and QR codes on the
# way to the unit. Long enough that guessing is not a threat model worth
# modelling.
_CREDENTIAL_BYTES = 32

# One wording for "this MAC belongs to another device", whether the collision
# happens at registration or at a rebind. It names no hospital on purpose.
_MAC_TAKEN = "A device with this MAC address is already registered"


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
            status_code=status.HTTP_409_CONFLICT, detail=_MAC_TAKEN
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
    limit: int = Query(100, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[Device]:
    """Devices belonging to the caller's hospital, newest first.

    Scoped by `tenant_id` from the token, never from a query parameter, so an
    admin of one hospital cannot enumerate another's estate.

    The bounds are refused at the edge rather than passed on: a negative offset
    and an unbounded limit are both questions the database should never be
    asked, and 422 says so more usefully than a slow query or a driver error.
    """
    stmt = (
        select(Device)
        .where(Device.tenant_id == tenant_id)
        .order_by(Device.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(db.scalars(stmt).all())


def _get_owned_device(db: Session, tenant_id: UUID, device_id: UUID) -> Device:
    """Load a device of the caller's hospital, locked for the change to come.

    Another hospital's device answers 404, exactly like a device that does not
    exist, so its existence is not confirmed across tenants. The row lock
    stops two admins acting on the same unit at once from both passing the
    status check and both writing an event; SQLite ignores it, Postgres does
    not.
    """
    device = db.scalars(
        select(Device)
        .where(Device.id == device_id, Device.tenant_id == tenant_id)
        .with_for_update()
    ).one_or_none()
    if device is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Device not found"
        )
    return device


def _require_in_service(device: Device) -> None:
    """Refuse to act on a unit that is already out of service."""
    if device.status not in IN_SERVICE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Device is {device.status}",
        )


def _record_event(
    db: Session,
    request: Request,
    device: Device,
    actor: User,
    *,
    event: str,
    reason: str,
    old_mac: str | None = None,
    new_mac: str | None = None,
) -> None:
    """Stage the event in the same transaction as the change it describes.

    Committed together or not at all: a revoked or rebound device without its
    record, or a record for a change that was rolled back, cannot exist.
    `request_id` is the one the audit middleware put on this request, which is
    what joins this row to its access-log entry.
    """
    db.add(
        DeviceEvent(
            tenant_id=device.tenant_id,
            device_id=device.id,
            actor_user_id=actor.id,
            actor_role=actor.role,
            event=event,
            reason=reason,
            old_mac=old_mac,
            new_mac=new_mac,
            request_id=request.state.request_id,
        )
    )


@router.post(
    "/{device_id}/revoke",
    response_model=DeviceOut,
    dependencies=[Depends(require_roles(*DEVICE_REVOKE_ROLES))],
)
def revoke_device(
    device_id: UUID,
    payload: DeviceRevoke,
    request: Request,
    db: DbSession,
    tenant_id: CurrentTenant,
    user: CurrentUser,
) -> Device:
    """Mark a lost or compromised unit as revoked.

    The registry side of SECURITY_DESIGN's "access is cut when the device
    reconnects". Nothing is enforced yet, because the sync path does not check
    device credentials yet; that check is the next part of Task 16, and it is
    where a suspended status will turn into a refused request.

    The credential hash is deliberately left alone. When the unit reconnects,
    the server has to recognise it — by that credential — to tell it that it
    has been revoked and must wipe its local data. Destroying the hash here
    would leave the server unable to tell the lost unit from any other
    stranger.
    """
    device = _get_owned_device(db, tenant_id, device_id)
    _require_in_service(device)

    device.status = REVOKED_STATUS
    _record_event(db, request, device, user, event="revoked", reason=payload.reason)
    db.commit()
    db.refresh(device)
    return device


@router.post(
    "/{device_id}/rebind-mac",
    response_model=DeviceOut,
    dependencies=[Depends(require_roles(*DEVICE_REBIND_ROLES))],
)
def rebind_device_mac(
    device_id: UUID,
    payload: DeviceRebind,
    request: Request,
    db: DbSession,
    tenant_id: CurrentTenant,
    user: CurrentUser,
) -> Device:
    """Bind a replacement board's MAC to an existing device record.

    For a board swap on a unit that is still in our hands: its history, its
    hospital, and its credential all stay, and only the address changes. The
    credential lives on the unit's storage, which moves to the new board with
    everything else. A unit that left our hands is revoked instead, which is
    why a suspended device cannot be rebound.
    """
    device = _get_owned_device(db, tenant_id, device_id)
    _require_in_service(device)
    if payload.mac_address == device.mac_address:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Device is already bound to this MAC address",
        )

    old_mac = device.mac_address
    device.mac_address = payload.mac_address
    _record_event(
        db,
        request,
        device,
        user,
        event="mac_rebound",
        reason=payload.reason,
        old_mac=old_mac,
        new_mac=payload.mac_address,
    )
    try:
        db.commit()
    except IntegrityError:
        # The new address already belongs to another device, possibly in
        # another hospital. Same answer as a duplicate registration, and for
        # the same reason it names nobody.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_MAC_TAKEN
        ) from None
    db.refresh(device)
    return device
