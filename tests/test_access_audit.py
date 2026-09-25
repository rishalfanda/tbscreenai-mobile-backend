"""The access trail: what lands in it, and what deliberately does not."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AccessLog
from app.models.hospital import Hospital
from app.models.user import User
from tests.conftest import make_patient_body


def _entries(db: Session) -> list[AccessLog]:
    return list(db.scalars(select(AccessLog).order_by(AccessLog.occurred_at)).all())


class TestWhatIsRecorded:
    def test_reading_a_patient_names_the_actor_and_the_row(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_a: dict,
        users,
    ) -> None:
        created = client.post(
            "/api/v1/patients",
            headers=headers_a,
            json={"code": "P-AUD-1", "name": "Audit Subject", "age": 40,
                  "gender": "Male", "status": "Normal"},
        )
        patient_id = created.json()["id"]
        client.get(f"/api/v1/patients/{patient_id}", headers=headers_a)

        read = [e for e in _entries(db_session) if e.action == "read"][-1]
        assert read.resource_type == "patient"
        assert str(read.resource_id) == patient_id
        assert read.actor_user_id is not None
        assert read.actor_role == "doctor"
        assert read.status_code == 200

    def test_the_role_is_a_snapshot_not_a_join(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_admin_a: dict, users,
    ) -> None:
        # Roles change. The trail has to say what the actor was allowed to do
        # at the time, not what they are allowed to do today.
        client.get("/api/v1/patients", headers=headers_admin_a)
        assert _entries(db_session)[-1].actor_role == "admin_rs"

    def test_a_listing_records_the_kind_but_no_single_id(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_a: dict, users,
    ) -> None:
        # The honest answer: the request touched every row the caller may see,
        # not one of them.
        client.get("/api/v1/patients", headers=headers_a)
        latest = _entries(db_session)[-1]
        assert latest.resource_type == "patient"
        assert latest.resource_id is None


class TestRefusalsAreRecordedToo:
    def test_a_rejected_token_still_leaves_a_row(
        self, client: TestClient, db_session: Session, hospitals: dict[str, Hospital]
    ) -> None:
        # The event an audit trail exists for. No actor to name, but the
        # attempt itself is the thing worth keeping.
        client.get("/api/v1/patients", headers={"Authorization": "Bearer nonsense"})
        latest = _entries(db_session)[-1]
        assert latest.status_code == 401
        assert latest.actor_user_id is None
        assert latest.actor_role is None

    def test_reaching_across_hospitals_is_recorded_as_refused(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_a: dict, headers_b: dict, users,
    ) -> None:
        created = client.post(
            "/api/v1/patients",
            headers=headers_a,
            json={"code": "P-AUD-2", "name": "Other Tenant", "age": 33,
                  "gender": "Female", "status": "Normal"},
        )
        stolen = created.json()["id"]

        client.get(f"/api/v1/patients/{stolen}", headers=headers_b)
        latest = _entries(db_session)[-1]
        assert latest.status_code == 404
        assert str(latest.resource_id) == stolen


class TestWhatIsDeliberatelyAbsent:
    def test_the_query_string_is_never_copied(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_a: dict, users,
    ) -> None:
        # A search parameter can carry a patient name. Copying it here would
        # make the audit log a second store of the data it audits.
        client.get("/api/v1/patients?limit=5", headers=headers_a)
        latest = _entries(db_session)[-1]
        assert "?" not in latest.path
        assert "limit" not in latest.path

    def test_the_health_check_is_not_audited(
        self, client: TestClient, db_session: Session, hospitals: dict[str, Hospital]
    ) -> None:
        # Runs every few seconds from the container runtime; auditing it would
        # bury the clinical events under machine noise.
        before = len(_entries(db_session))
        client.get("/health")
        assert len(_entries(db_session)) == before


class TestCorrelation:
    def test_every_response_carries_a_request_id_that_matches_the_row(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_a: dict, users,
    ) -> None:
        response = client.get("/api/v1/patients", headers=headers_a)
        assert response.headers["X-Request-Id"]
        assert _entries(db_session)[-1].request_id == response.headers["X-Request-Id"]

    def test_a_supplied_request_id_is_kept(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_a: dict, users,
    ) -> None:
        # So a trail can be followed across the tablet, the proxy and here.
        client.get(
            "/api/v1/patients",
            headers={**headers_a, "X-Request-Id": "aaaaaaaa-0000-4000-8000-000000000001"},
        )
        assert _entries(db_session)[-1].request_id == (
            "aaaaaaaa-0000-4000-8000-000000000001"
        )


class TestFailureDoesNotBlockTheRequest:
    def test_an_unwritable_trail_is_logged_but_does_not_refuse_the_caller(
        self, client: TestClient, hospitals: dict[str, Hospital],
        headers_a: dict, users, caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Policy call, flagged for review: refusing a clinician access to a
        # patient record because the audit table hiccupped trades a
        # bookkeeping failure for a clinical one.
        from app.main import app

        def _broken():
            raise RuntimeError("audit database unavailable")

        original = app.state.audit_session
        app.state.audit_session = _broken
        try:
            assert client.get("/api/v1/patients", headers=headers_a).status_code == 200
        finally:
            app.state.audit_session = original
        assert "Access audit write failed" in caplog.text

class TestTheCorrelationIdCannotBeAbused:
    @pytest.mark.parametrize(
        "supplied",
        [
            "x" * 200,  # longer than the column; the insert would fail
            "not-a-uuid",
            "../../etc/passwd",
            "",
        ],
    )
    def test_a_malformed_id_is_replaced_not_stored(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_a: dict, users, supplied: str,
    ) -> None:
        # Because an audit failure is logged rather than raised, a value long
        # enough to break the insert would let a client switch off its own
        # trail. Replacing it keeps the row, and keeps the length bounded.
        response = client.get(
            "/api/v1/patients", headers={**headers_a, "X-Request-Id": supplied}
        )
        latest = _entries(db_session)[-1]
        assert latest.request_id != supplied
        assert len(latest.request_id) == 36
        assert latest.request_id == response.headers["X-Request-Id"]

    def test_the_request_still_succeeds(
        self, client: TestClient, hospitals: dict[str, Hospital],
        headers_a: dict, users,
    ) -> None:
        # Refusing a clinical request over a correlation header would trade a
        # cosmetic problem for an operational one.
        assert client.get(
            "/api/v1/patients", headers={**headers_a, "X-Request-Id": "rubbish"}
        ).status_code == 200


class TestSuperAdminActionsAreAttributedToTheHospitalTouched:
    def test_the_resolved_tenant_is_recorded_not_the_actor_own(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_super: dict, users,
    ) -> None:
        # A super_admin has no hospital of their own. Recording that would
        # leave NULL on every row, and an audit of one hospital would miss
        # every action a central administrator took against it.
        target = hospitals["B"]
        client.get(
            "/api/v1/patients",
            headers={**headers_super, "X-Tenant-Id": str(target.id)},
        )
        latest = _entries(db_session)[-1]
        assert latest.actor_role == "super_admin"
        assert latest.tenant_id == target.id

    def test_a_tenant_bound_user_still_records_their_own(
        self, client: TestClient, db_session: Session,
        hospitals: dict[str, Hospital], headers_a: dict, users,
    ) -> None:
        client.get("/api/v1/patients", headers=headers_a)
        assert _entries(db_session)[-1].tenant_id == hospitals["A"].id


class TestARolledBackRequestIsStillRecorded:
    """A rollback expires every object in the session, the actor included.

    The middleware must not depend on that object still being readable after
    the request's session is gone: a refused duplicate or a conflicting sync
    item is precisely the kind of request the trail exists for.
    """

    def test_a_duplicate_registration_answers_409_and_is_recorded(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_super: dict,
        users: dict[str, User],
        per_request_sessions: None,
    ) -> None:
        headers = {**headers_super, "X-Tenant-Id": str(hospitals["A"].id)}
        body = {"mac_address": "aa:bb:cc:dd:ee:ff"}
        assert client.post("/api/v1/devices", headers=headers, json=body).status_code == 201

        duplicate = client.post("/api/v1/devices", headers=headers, json=body)

        assert duplicate.status_code == 409
        latest = _entries(db_session)[-1]
        assert latest.status_code == 409
        assert latest.actor_user_id == users["super"].id
        assert latest.actor_role == "super_admin"
        assert latest.tenant_id == hospitals["A"].id

    def test_one_conflicting_sync_item_does_not_sink_the_batch(
        self,
        client: TestClient,
        db_session: Session,
        hospitals: dict[str, Hospital],
        headers_a: dict,
        users: dict[str, User],
        per_request_sessions: None,
    ) -> None:
        # Two tablets offline both used TB000900. That create cannot apply,
        # and the push rolls the item back. The batch must still answer 200
        # with a verdict per item: a 500 here loses the verdicts for work the
        # server already committed, and the tablet can no longer tell which
        # of its offline records landed.
        #
        # The conflict comes last on purpose. An item after it would reload
        # the expired actor on its way through and hide the crash, so a batch
        # that ends on a conflict, including a batch of one, is the case that
        # used to fail.
        client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TB000900")
        )
        taken = {
            "client_op_id": str(uuid.uuid4()),
            "entity_type": "patient",
            "operation": "create",
            "entity_id": str(uuid.uuid4()),
            "payload": make_patient_body("TB000900"),
        }
        fresh = {**taken, "client_op_id": str(uuid.uuid4()),
                 "entity_id": str(uuid.uuid4()), "payload": make_patient_body("TB000901")}

        response = client.post(
            "/api/v1/sync/push",
            headers=headers_a,
            json={"device_id": "tablet-uji", "items": [fresh, taken]},
        )

        assert response.status_code == 200
        verdicts = [result["status"] for result in response.json()["results"]]
        assert verdicts == ["applied", "conflict"]
        latest = _entries(db_session)[-1]
        assert latest.path == "/api/v1/sync/push"
        assert latest.actor_user_id == users["doctor_a"].id
