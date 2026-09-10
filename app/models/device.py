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

# The four spellings a MAC is actually printed in. Matching these explicitly,
# rather than deleting whatever is not a hex digit, is what keeps
# "!!aa:bb:cc:dd:ee:ff??" from being read as a valid address: stripping
# punctuation accepts any string that happens to contain twelve hex digits,
# which is a much larger set than the formats we mean to support.
_MAC_FORMATS = (
    re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$"),   # aa:bb:cc:dd:ee:ff
    re.compile(r"^[0-9a-f]{2}(?:-[0-9a-f]{2}){5}$"),   # aa-bb-cc-dd-ee-ff
    re.compile(r"^[0-9a-f]{4}(?:\.[0-9a-f]{4}){2}$"),  # aabb.ccdd.eeff
    re.compile(r"^[0-9a-f]{12}$"),                     # aabbccddeeff
)
_SEPARATORS = str.maketrans("", "", ":-.")


def normalise_mac(raw: str) -> str:
    """Return one canonical spelling of a MAC: lowercase, colon-separated.

    One interface prints itself as AA:BB:CC:DD:EE:FF, another as
    aa-bb-cc-dd-ee-ff, a switch console as aabb.ccddeeff. Stored as typed,
    those are three rows for one device and the unique constraint stops meaning
    anything. Normalising on the way in is what makes the constraint true.

    Only those spellings are accepted, and separators may not be mixed within
    one address. Surrounding whitespace is forgiven because it is a paste
    artefact rather than a format. Anything else raises ValueError, so a typo
    fails at registration rather than becoming a device nobody can match.
    """
    candidate = raw.strip().lower()
    if not any(pattern.match(candidate) for pattern in _MAC_FORMATS):
        raise ValueError(f"{raw!r} is not a MAC address")
    digits = candidate.translate(_SEPARATORS)
    return ":".join(digits[index : index + 2] for index in range(0, 12, 2))


class Device(Base, UuidPkMixin, TimestampMixin, TenantMixin):
    """One unit in the field, bound to exactly one hospital.

    MAC and credential are deliberately separate columns. A MAC is visible to
    anyone on the same network and can be changed from the operating system, so
    it answers "which device is this" and not "is this really that device".
    The credential answers the second question; it is issued once at
    registration and only its hash is kept, the same way user passwords are.

    The canonical address is the WLAN interface's, not Ethernet's. Units upload
    only when they reach WiFi, so the wireless interface is the one guaranteed
    to be up whenever the server hears from a device at all.

    It has to be that interface's permanent hardware address, not whatever it
    happens to be using. WiFi stacks can present a different, randomised
    address per network, which would register one physical unit as several.
    """

    __tablename__ = "devices"

    mac_address: Mapped[str] = mapped_column(
        String(MAC_LENGTH), unique=True, nullable=False
    )
    credential_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default=DEVICE_STATUS_DEFAULT, nullable=False
    )
