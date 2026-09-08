"""Device registry — identity normalisation and the constraints behind it.

Task 16 rests on two promises the rest of the feature will assume: one physical
unit is one row no matter how its MAC was typed, and a device always belongs to
exactly one hospital. Both are cheap to state and easy to lose, so they are
pinned here before any endpoint is built on top of them.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.device import (
    DEVICE_STATUS_DEFAULT,
    Device,
    normalise_mac,
)
from app.models.hospital import Hospital

CANONICAL = "aa:bb:cc:dd:ee:ff"


def _device(hospital: Hospital, mac: str = CANONICAL, **overrides) -> Device:
    return Device(
        tenant_id=hospital.id,
        mac_address=mac,
        credential_hash="not-a-real-hash",
        **overrides,
    )


class TestMacNormalisation:
    """One unit must be one row, however the address was written down."""

    @pytest.mark.parametrize(
        "written",
        [
            "aa:bb:cc:dd:ee:ff",
            "AA:BB:CC:DD:EE:FF",
            "aa-bb-cc-dd-ee-ff",
            "AA-BB-CC-DD-EE-FF",
            "aabb.ccdd.eeff",
            "aabbccddeeff",
            "  AA:bb:CC:dd:EE:ff  ",
        ],
    )
    def test_every_spelling_collapses_to_one(self, written: str) -> None:
        # Whichever tool printed the address — ip link, a switch console, a
        # label on the unit — the registry sees the same string.
        assert normalise_mac(written) == CANONICAL

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            "aa:bb:cc:dd:ee",  # ten digits, one octet short
            "aa:bb:cc:dd:ee:ff:00",  # fourteen digits
            "zz:bb:cc:dd:ee:ff",  # not hex
            "not a mac at all",
        ],
    )
    def test_rejects_anything_that_is_not_twelve_hex_digits(
        self, invalid: str
    ) -> None:
        # Failing at registration is the point. A typo that slips through
        # becomes a device nobody can ever match a sync request to.
        with pytest.raises(ValueError):
            normalise_mac(invalid)


class TestDeviceConstraints:
    """The rules that survive a careless endpoint, because the schema holds them."""

    def test_a_device_cannot_exist_without_a_hospital(
        self, db_session: Session, hospitals: dict[str, Hospital]
    ) -> None:
        # Pak Wahyono's rule as a NOT NULL, not a convention: a future endpoint
        # that forgets to set the owner fails loudly instead of creating a
        # device no hospital is accountable for.
        db_session.add(
            Device(mac_address=CANONICAL, credential_hash="not-a-real-hash")
        )
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_the_same_mac_cannot_be_registered_twice(
        self, db_session: Session, hospitals: dict[str, Hospital]
    ) -> None:
        # Including across hospitals. A physical unit exists once, so a second
        # registration is either a mistake or an attempt to move a device
        # between tenants — and both should stop here.
        db_session.add(_device(hospitals["A"]))
        db_session.commit()

        db_session.add(_device(hospitals["B"]))
        with pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_a_new_device_starts_pending(
        self, db_session: Session, hospitals: dict[str, Hospital]
    ) -> None:
        # Registered is not the same as trusted. A unit becomes active only
        # after it has checked in, so a row created and then forgotten cannot
        # sync.
        device = _device(hospitals["A"])
        db_session.add(device)
        db_session.commit()

        assert device.status == DEVICE_STATUS_DEFAULT == "pending"
