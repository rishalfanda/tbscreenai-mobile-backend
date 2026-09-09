"""Device registry — identity normalisation and the constraints behind it.

Task 16 rests on two promises the rest of the feature will assume: one physical
unit is one row no matter how its MAC was typed, and a device always belongs to
exactly one hospital. Both are cheap to state and easy to lose, so they are
pinned here before any endpoint is built on top of them.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import verify_password
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

class TestRegistration:
    """Issuing an identity to hardware, and who is allowed to do it."""

    def test_registering_returns_the_credential_exactly_once(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_super: dict
    ) -> None:
        # The plaintext is readable here and nowhere else afterwards. Storing
        # only the hash is what makes that true rather than a promise.
        headers = {**headers_super, "X-Tenant-Id": str(hospitals["A"].id)}
        created = client.post(
            "/api/v1/devices", headers=headers, json={"mac_address": "AA-BB-CC-DD-EE-FF"}
        )
        assert created.status_code == 201
        body = created.json()
        assert body["credential"]
        assert body["mac_address"] == CANONICAL
        assert body["status"] == "pending"
        assert body["tenant_id"] == str(hospitals["A"].id)

    def test_only_the_hash_reaches_the_database(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
    ) -> None:
        # Reading the row back is the check that matters: a wrapper that
        # forgot to hash would still return a plausible-looking response.
        headers = {**headers_super, "X-Tenant-Id": str(hospitals["A"].id)}
        credential = client.post(
            "/api/v1/devices", headers=headers, json={"mac_address": CANONICAL}
        ).json()["credential"]

        stored = db_session.scalars(select(Device)).one()
        assert stored.credential_hash != credential
        assert verify_password(credential, stored.credential_hash)

    def test_the_credential_never_appears_in_a_listing(
        self,
        client: TestClient,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_a: dict,
    ) -> None:
        # Separated by type, not by discipline: DeviceOut has no field to leak.
        client.post(
            "/api/v1/devices",
            headers={**headers_super, "X-Tenant-Id": str(hospitals["A"].id)},
            json={"mac_address": CANONICAL},
        )
        listing = client.get("/api/v1/devices", headers=headers_admin_a)
        assert listing.status_code == 200
        assert "credential" not in listing.text
        assert "credential_hash" not in listing.text

    def test_the_same_mac_is_refused_even_for_a_different_hospital(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_super: dict
    ) -> None:
        # 409 rather than 500, and the message names no hospital — saying who
        # already holds the unit would leak one tenant's estate to another.
        for key in ("A", "B"):
            response = client.post(
                "/api/v1/devices",
                headers={**headers_super, "X-Tenant-Id": str(hospitals[key].id)},
                json={"mac_address": CANONICAL},
            )
        assert response.status_code == 409
        assert "RS Alpha" not in response.text

    def test_a_typo_is_refused_at_the_edge(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_super: dict
    ) -> None:
        response = client.post(
            "/api/v1/devices",
            headers={**headers_super, "X-Tenant-Id": str(hospitals["A"].id)},
            json={"mac_address": "not-a-mac"},
        )
        assert response.status_code == 422

    def test_super_admin_must_name_the_owning_hospital(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_super: dict
    ) -> None:
        # No X-Tenant-Id means no default. Which hospital a device may sync for
        # is too consequential to be inferred from whoever happened to log in.
        response = client.post(
            "/api/v1/devices", headers=headers_super, json={"mac_address": CANONICAL}
        )
        assert response.status_code == 400


class TestDeviceAccessControl:
    """Fleet management is administrative; clinical roles are absent from it."""

    def test_a_doctor_may_neither_register_nor_list(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_a: dict
    ) -> None:
        assert (
            client.post(
                "/api/v1/devices", headers=headers_a, json={"mac_address": CANONICAL}
            ).status_code
            == 403
        )
        assert client.get("/api/v1/devices", headers=headers_a).status_code == 403

    def test_a_hospital_admin_may_list_but_not_register(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_admin_a: dict
    ) -> None:
        # Handing hardware the ability to sync a hospital's patient data is a
        # central act, so it stays with super_admin even though the hospital
        # admin outranks a doctor everywhere else.
        assert client.get("/api/v1/devices", headers=headers_admin_a).status_code == 200
        assert (
            client.post(
                "/api/v1/devices",
                headers=headers_admin_a,
                json={"mac_address": CANONICAL},
            ).status_code
            == 403
        )

    def test_one_hospital_cannot_enumerate_another_estate(
        self,
        client: TestClient,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_a: dict,
        headers_admin_b: dict,
    ) -> None:
        # Scope comes from the token, so there is no parameter to tamper with.
        client.post(
            "/api/v1/devices",
            headers={**headers_super, "X-Tenant-Id": str(hospitals["A"].id)},
            json={"mac_address": CANONICAL},
        )
        assert len(client.get("/api/v1/devices", headers=headers_admin_a).json()) == 1
        assert client.get("/api/v1/devices", headers=headers_admin_b).json() == []
