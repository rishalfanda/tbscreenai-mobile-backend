"""Devices in the field, and the identity the server knows them by.

`sync_logs.device_id` was a free-text column pointing at nothing, so the server
could not answer the three questions that decide field operations: which units
still run an old model, which have not synced in days, and which must be rolled
back when a release goes wrong. This table is where those answers come from.

Pak Wahyono's rule — one hospital may run several devices, one device never
serves two hospitals — is enforced as a non-nullable foreign key rather than a
convention, so it cannot be broken by a future endpoint that forgets it.
"""

import re

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantMixin, TimestampMixin, UuidPkMixin

# Lifecycle states. A device is never deleted: sync history points at it, and
# that history has to stay readable after the unit leaves the field.
#   pending        - registered, credential issued, has not checked in yet
#   active         - may sync
#   suspended      - lost or under investigation; refused, and reversible
#   decommissioned - retired for good
DEVICE_STATUSES = ("pending", "active", "suspended", "decommissioned")
DEVICE_STATUS_DEFAULT = "pending"

# "aa:bb:cc:dd:ee:ff" - twelve hex digits and five separators.
MAC_LENGTH = 17

_NON_HEX = re.compile(r"[^0-9a-fA-F]")


def normalise_mac(raw: str) -> str:
    """Return one canonical spelling of a MAC: lowercase, colon-separated.

    One interface prints itself as AA:BB:CC:DD:EE:FF, another as
    aa-bb-cc-dd-ee-ff, a switch console as aabb.ccddeeff. Stored as typed,
    those are three rows for one device and the unique constraint stops meaning
    anything. Normalising on the way in is what makes the constraint true.

    Raises ValueError on anything that is not twelve hex digits, so a typo
    fails at registration rather than becoming a device nobody can match.
    """
    digits = _NON_HEX.sub("", raw).lower()
    if len(digits) != 12:
        raise ValueError(f"{raw!r} is not a MAC address")
    return ":".join(digits[index : index + 2] for index in range(0, 12, 2))


class Device(Base, UuidPkMixin, TimestampMixin, TenantMixin):
    """One unit in the field, bound to exactly one hospital.

    MAC and credential are deliberately separate columns. A MAC is visible to
    anyone on the same network and can be changed from the operating system, so
    it answers "which device is this" and not "is this really that device".
    The credential answers the second question; it is issued once at
    registration and only its hash is kept, the same way user passwords are.

    A Raspberry Pi 5 has two interfaces with different MACs. Ethernet is the
    canonical one — its address is burned into the chip, so a unit that syncs
    over cable one day and wireless the next stays a single row.
    """

    __tablename__ = "devices"

    mac_address: Mapped[str] = mapped_column(
        String(MAC_LENGTH), unique=True, nullable=False
    )
    credential_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=DEVICE_STATUS_DEFAULT, nullable=False
    )
