"""Device registry — identity normalisation and the constraints behind it.

Task 16 rests on two promises the rest of the feature will assume: one physical
unit is one row no matter how its MAC was typed, and a device always belongs to
exactly one hospital. Both are cheap to state and easy to lose, so they are
pinned here before any endpoint is built on top of them.
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.audit import AccessLog
from app.models.device import (
    DEVICE_STATUS_DEFAULT,
    REASON_MAX_LENGTH,
    Device,
    DeviceEvent,
    normalise_mac,
)
from app.models.hospital import Hospital

CANONICAL = "aa:bb:cc:dd:ee:ff"


REPLACEMENT = "11:22:33:44:55:66"


def _device(hospital: Hospital, mac: str = CANONICAL, **overrides) -> Device:
    return Device(
        tenant_id=hospital.id,
        mac_address=mac,
        credential_hash="not-a-real-hash",
        **overrides,
    )


def _as_super(headers_super: dict, hospital: Hospital) -> dict:
    return {**headers_super, "X-Tenant-Id": str(hospital.id)}


def _register(
    client: TestClient, headers_super: dict, hospital: Hospital, mac: str = CANONICAL
) -> dict:
    response = client.post(
        "/api/v1/devices",
        headers=_as_super(headers_super, hospital),
        json={"mac_address": mac},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _events(db: Session) -> list[DeviceEvent]:
    return list(db.scalars(select(DeviceEvent)).all())


def _stored(db: Session, device_id: str) -> Device:
    """The row as the database holds it, not as the response described it."""
    device = db.get(Device, uuid.UUID(device_id))
    assert device is not None
    return device


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

class TestInputBounds:
    """Refusing at the edge what the database should never be asked."""

    @pytest.mark.parametrize(
        "written",
        [
            "!!aa:bb:cc:dd:ee:ff??",  # twelve hex digits buried in punctuation
            "aa:bb-cc:dd-ee:ff",  # separators mixed within one address
            "aa bb cc dd ee ff",  # spaces are not a MAC separator
        ],
    )
    def test_a_string_that_merely_contains_hex_digits_is_not_a_mac(
        self, written: str
    ) -> None:
        # Deleting everything that is not a hex digit would accept all three.
        # Matching the four spellings a MAC is actually printed in does not.
        with pytest.raises(ValueError):
            normalise_mac(written)

    @pytest.mark.parametrize(
        "query", ["?limit=0", "?limit=101", "?offset=-1"]
    )
    def test_pagination_outside_its_bounds_is_refused(
        self, client: TestClient, hospitals: dict[str, Hospital],
        headers_admin_a: dict, query: str,
    ) -> None:
        assert client.get(
            f"/api/v1/devices{query}", headers=headers_admin_a
        ).status_code == 422

    def test_the_bounds_themselves_are_accepted(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_admin_a: dict
    ) -> None:
        assert client.get(
            "/api/v1/devices?limit=1&offset=0", headers=headers_admin_a
        ).status_code == 200
        assert client.get(
            "/api/v1/devices?limit=100", headers=headers_admin_a
        ).status_code == 200


class TestRevocation:
    """Cutting off a unit that is lost, and the record that it happened."""

    def test_the_owning_hospital_can_revoke_its_own_unit(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_a: dict,
    ) -> None:
        # The hospital that lost the unit acts at once. Waiting for the centre
        # to answer is time the unit spends with a working credential.
        device = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{device['id']}/revoke",
            headers=headers_admin_a,
            json={"reason": "Dilaporkan hilang di bangsal"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "suspended"
        assert _stored(db_session, device["id"]).status == "suspended"

    def test_the_revocation_records_who_did_it_and_why(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_a: dict,
        users,
    ) -> None:
        device = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{device['id']}/revoke",
            headers=headers_admin_a,
            json={"reason": "  Dilaporkan hilang di bangsal  "},
        )

        [event] = _events(db_session)
        assert event.event == "revoked"
        assert str(event.device_id) == device["id"]
        assert event.tenant_id == hospitals["A"].id
        assert event.actor_user_id == users["admin_a"].id
        assert event.actor_role == "admin_rs"
        # Trimmed, so the trail holds what was meant rather than a paste.
        assert event.reason == "Dilaporkan hilang di bangsal"
        assert event.old_mac is None and event.new_mac is None
        # The same request id the caller got back, and the one on the access
        # log row, which is what lets the two tables be read as one story.
        assert event.request_id == response.headers["X-Request-Id"]
        logged = db_session.scalars(
            select(AccessLog).where(AccessLog.request_id == event.request_id)
        ).one()
        assert str(logged.resource_id) == device["id"]

    def test_super_admin_may_revoke_naming_the_hospital(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_super: dict
    ) -> None:
        device = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{device['id']}/revoke",
            headers=_as_super(headers_super, hospitals["A"]),
            json={"reason": "Unit tidak kembali setelah kunjungan lapangan"},
        )
        assert response.status_code == 200, response.text

    def test_another_hospital_cannot_revoke_it_or_learn_it_exists(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_b: dict,
    ) -> None:
        # 404, not 403: a 403 would confirm the id belongs to somebody.
        device = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{device['id']}/revoke",
            headers=headers_admin_b,
            json={"reason": "percobaan"},
        )
        assert response.status_code == 404
        assert _stored(db_session, device["id"]).status == "pending"
        assert _events(db_session) == []

    def test_a_doctor_cannot_revoke(
        self,
        client: TestClient,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_a: dict,
    ) -> None:
        device = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{device['id']}/revoke",
            headers=headers_a,
            json={"reason": "hilang"},
        )
        assert response.status_code == 403

    def test_an_unknown_device_is_not_found(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_admin_a: dict
    ) -> None:
        response = client.post(
            "/api/v1/devices/00000000-0000-0000-0000-000000000000/revoke",
            headers=headers_admin_a,
            json={"reason": "hilang"},
        )
        assert response.status_code == 404

    @pytest.mark.parametrize("state", ["suspended", "decommissioned"])
    def test_a_unit_out_of_service_cannot_be_revoked_again(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_admin_a: dict,
        users,
        state: str,
    ) -> None:
        # A second revocation would add a second "why" to a unit that is
        # already cut off, and the trail would stop saying when it happened.
        device = _device(hospitals["A"], status=state)
        db_session.add(device)
        db_session.commit()

        response = client.post(
            f"/api/v1/devices/{device.id}/revoke",
            headers=headers_admin_a,
            json={"reason": "hilang lagi"},
        )
        assert response.status_code == 409
        assert _events(db_session) == []

    @pytest.mark.parametrize(
        "body",
        [{}, {"reason": ""}, {"reason": "   "}, {"reason": "x" * (REASON_MAX_LENGTH + 1)}],
    )
    def test_a_reason_is_required_and_bounded(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_a: dict,
        body: dict,
    ) -> None:
        device = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{device['id']}/revoke", headers=headers_admin_a, json=body
        )
        assert response.status_code == 422
        assert _stored(db_session, device["id"]).status == "pending"

    def test_the_credential_still_verifies_after_revocation(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_a: dict,
    ) -> None:
        # Deliberate, and pinned so nobody "fixes" it. When the lost unit
        # reconnects, the server must still recognise it by its credential in
        # order to tell it that it is revoked and must wipe its data. The
        # status is what refuses it, not a destroyed hash.
        registered = _register(client, headers_super, hospitals["A"])
        client.post(
            f"/api/v1/devices/{registered['id']}/revoke",
            headers=headers_admin_a,
            json={"reason": "hilang"},
        )
        stored = _stored(db_session, registered["id"])
        assert verify_password(registered["credential"], stored.credential_hash)


class TestMacRebinding:
    """Moving a unit onto a replacement board without losing who it is."""

    def test_the_same_record_takes_the_new_address(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
    ) -> None:
        registered = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{registered['id']}/rebind-mac",
            headers=_as_super(headers_super, hospitals["A"]),
            # Any spelling, as at registration.
            json={"mac_address": "11-22-33-44-55-66", "reason": "Papan diganti"},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["id"] == registered["id"]
        assert body["mac_address"] == REPLACEMENT
        assert body["status"] == registered["status"]

        # The credential moved with the unit's storage, so it still works.
        stored = _stored(db_session, registered["id"])
        assert verify_password(registered["credential"], stored.credential_hash)

    def test_the_rebind_records_the_old_and_the_new_address(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        users,
    ) -> None:
        # Task 16's verification item, word for word: the audit carries both
        # values, so "which board was this unit before March" has an answer.
        registered = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{registered['id']}/rebind-mac",
            headers=_as_super(headers_super, hospitals["A"]),
            json={"mac_address": REPLACEMENT, "reason": "Papan rusak terkena air"},
        )

        [event] = _events(db_session)
        assert event.event == "mac_rebound"
        assert event.old_mac == CANONICAL
        assert event.new_mac == REPLACEMENT
        assert event.reason == "Papan rusak terkena air"
        assert event.actor_user_id == users["super"].id
        assert event.actor_role == "super_admin"
        # The hospital the device belongs to, not the actor's: a super_admin
        # has none, and an audit of RS Alpha has to find this row.
        assert event.tenant_id == hospitals["A"].id
        assert event.request_id == response.headers["X-Request-Id"]

    def test_an_address_held_by_another_device_is_refused(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
    ) -> None:
        # Including a device of another hospital, and without naming it.
        mine = _register(client, headers_super, hospitals["A"])
        _register(client, headers_super, hospitals["B"], mac=REPLACEMENT)

        response = client.post(
            f"/api/v1/devices/{mine['id']}/rebind-mac",
            headers=_as_super(headers_super, hospitals["A"]),
            json={"mac_address": REPLACEMENT, "reason": "Papan diganti"},
        )
        assert response.status_code == 409
        assert "RS Beta" not in response.text
        assert _stored(db_session, mine["id"]).mac_address == CANONICAL
        assert _events(db_session) == []

    def test_a_taken_address_answers_409_with_production_sessions(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        per_request_sessions: None,
    ) -> None:
        # The collision is caught by rolling the transaction back. With a
        # session per request, as in production, that rollback used to crash
        # the audit middleware and turn this 409 into a 500. Pinned with real
        # per-request sessions because the shared test session cannot show it.
        mine = _register(client, headers_super, hospitals["A"])
        _register(client, headers_super, hospitals["B"], mac=REPLACEMENT)

        response = client.post(
            f"/api/v1/devices/{mine['id']}/rebind-mac",
            headers=_as_super(headers_super, hospitals["A"]),
            json={"mac_address": REPLACEMENT, "reason": "Papan diganti"},
        )
        assert response.status_code == 409
        assert _events(db_session) == []

    def test_rebinding_to_the_current_address_is_refused(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
    ) -> None:
        # Compared after normalisation, so a different spelling of the same
        # address is still no change, and no event claims one happened.
        registered = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{registered['id']}/rebind-mac",
            headers=_as_super(headers_super, hospitals["A"]),
            json={"mac_address": "AA-BB-CC-DD-EE-FF", "reason": "Papan diganti"},
        )
        assert response.status_code == 409
        assert _events(db_session) == []

    def test_only_super_admin_may_rebind(
        self,
        client: TestClient,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_a: dict,
        headers_a: dict,
    ) -> None:
        # Binding new hardware to an identity grants access, so it stays with
        # the role that grants it at registration — even for the hospital's
        # own admin, who may revoke the same unit.
        registered = _register(client, headers_super, hospitals["A"])
        for headers in (headers_admin_a, headers_a):
            response = client.post(
                f"/api/v1/devices/{registered['id']}/rebind-mac",
                headers=headers,
                json={"mac_address": REPLACEMENT, "reason": "Papan diganti"},
            )
            assert response.status_code == 403

    def test_super_admin_is_scoped_to_the_hospital_they_name(
        self, client: TestClient, hospitals: dict[str, Hospital], headers_super: dict
    ) -> None:
        # Naming the wrong hospital finds nothing, and naming none is refused,
        # the same rules as registration.
        registered = _register(client, headers_super, hospitals["A"])
        url = f"/api/v1/devices/{registered['id']}/rebind-mac"
        body = {"mac_address": REPLACEMENT, "reason": "Papan diganti"}

        wrong = client.post(url, headers=_as_super(headers_super, hospitals["B"]), json=body)
        assert wrong.status_code == 404
        unnamed = client.post(url, headers=headers_super, json=body)
        assert unnamed.status_code == 400

    def test_a_revoked_unit_cannot_be_rebound(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        headers_admin_a: dict,
    ) -> None:
        # A unit that left our hands is not having its board swapped by us.
        # Letting its record take a new address would hand the lost unit's
        # identity to whatever hardware asked for it.
        registered = _register(client, headers_super, hospitals["A"])
        client.post(
            f"/api/v1/devices/{registered['id']}/revoke",
            headers=headers_admin_a,
            json={"reason": "hilang"},
        )
        response = client.post(
            f"/api/v1/devices/{registered['id']}/rebind-mac",
            headers=_as_super(headers_super, hospitals["A"]),
            json={"mac_address": REPLACEMENT, "reason": "Papan diganti"},
        )
        assert response.status_code == 409
        assert _stored(db_session, registered["id"]).mac_address == CANONICAL

    @pytest.mark.parametrize(
        "body",
        [
            {"mac_address": "not-a-mac", "reason": "Papan diganti"},
            {"mac_address": REPLACEMENT},
            {"mac_address": REPLACEMENT, "reason": "   "},
        ],
    )
    def test_the_address_and_the_reason_are_checked_at_the_edge(
        self,
        client: TestClient,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        body: dict,
    ) -> None:
        registered = _register(client, headers_super, hospitals["A"])
        response = client.post(
            f"/api/v1/devices/{registered['id']}/rebind-mac",
            headers=_as_super(headers_super, hospitals["A"]),
            json=body,
        )
        assert response.status_code == 422
