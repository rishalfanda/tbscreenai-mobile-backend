"""Every cell of the access matrix in app/api/deps.py, asserted.

`require_roles` existed for weeks without a single call site — written,
exported, never wired to a route, and never tested. Coverage showed those
lines at 0%. A guard nobody exercises is indistinguishable from no guard, so
this file walks the whole matrix rather than spot-checking a route or two.

Authorisation is asserted as "not 403" rather than "200": whether a request
then succeeds, 404s or 422s depends on fixtures and is another test's job.
What matters here is only whether the role gate opened.
"""

import io
import uuid

import pytest

from tests.conftest import make_patient_body

FINDINGS = {
    "consolidation": 26.3,
    "cavity": 0.6,
    "effusion": 5.6,
    "fibrotic": 0.0,
    "calcification": 0.0,
}

DOCTOR = "doctor"
ADMIN_RS = "admin_rs"
SUPER_ADMIN = "super_admin"
ALL_ROLES = (DOCTOR, ADMIN_RS, SUPER_ADMIN)

# (method, path template, roles permitted) — mirrors the table in deps.py.
ACCESS_MATRIX = [
    ("GET", "/api/v1/patients", set(ALL_ROLES)),
    ("POST", "/api/v1/patients", {DOCTOR, ADMIN_RS}),
    ("GET", "/api/v1/patients/{patient_id}", set(ALL_ROLES)),
    ("PUT", "/api/v1/patients/{patient_id}", {DOCTOR, ADMIN_RS}),
    ("DELETE", "/api/v1/patients/{patient_id}", {ADMIN_RS, SUPER_ADMIN}),
    ("POST", "/api/v1/diagnoses/infer", {DOCTOR}),
    ("GET", "/api/v1/diagnoses", set(ALL_ROLES)),
    ("POST", "/api/v1/diagnoses", {DOCTOR}),
    ("GET", "/api/v1/diagnoses/{diagnosis_id}", set(ALL_ROLES)),
    ("PATCH", "/api/v1/diagnoses/{diagnosis_id}/status", {DOCTOR}),
    ("POST", "/api/v1/sync/push", {DOCTOR}),
    ("GET", "/api/v1/sync/pull", {DOCTOR}),
    ("GET", "/api/v1/sync/model-version", set(ALL_ROLES)),
]


@pytest.fixture
def scenario(client, hospitals, headers_a, headers_admin_a, headers_super):
    """One patient, one diagnosis, and a header set per role.

    super_admin is not tenant-bound, so its headers carry X-Tenant-Id — without
    it the request fails at tenant resolution and would never reach the role
    gate this file is about.
    """
    patient = client.post(
        "/api/v1/patients", headers=headers_a, json=make_patient_body("TBROLE01")
    ).json()
    diagnosis = client.post(
        "/api/v1/diagnoses",
        headers=headers_a,
        json={
            "patient_id": patient["id"],
            "is_positive": True,
            "confidence": 88,
            "model_version": "TBScreen v2.1.0",
            "findings": FINDINGS,
        },
    ).json()
    return {
        "patient_id": patient["id"],
        "diagnosis_id": diagnosis["id"],
        "headers": {
            DOCTOR: headers_a,
            ADMIN_RS: headers_admin_a,
            SUPER_ADMIN: {**headers_super, "X-Tenant-Id": str(hospitals["A"].id)},
        },
    }


def _send(client, method, path, headers):
    """Issue the request with a body valid enough to get past the role gate."""
    if method == "POST" and path.endswith("/infer"):
        return client.post(
            path,
            headers=headers,
            files={"image": ("scan.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")},
        )
    if method == "POST" and path.endswith("/patients"):
        return client.post(path, headers=headers, json=make_patient_body())
    if method == "POST" and path.endswith("/diagnoses"):
        return client.post(
            path,
            headers=headers,
            json={
                "patient_id": str(uuid.uuid4()),
                "is_positive": False,
                "confidence": 10,
                "model_version": "v1",
                "findings": FINDINGS,
            },
        )
    if method == "POST" and path.endswith("/sync/push"):
        return client.post(path, headers=headers, json={"device_id": "t", "items": []})
    if method == "PUT":
        return client.put(path, headers=headers, json={"name": "Diubah"})
    if method == "PATCH":
        return client.patch(path, headers=headers, json={"status": "agreed"})
    if method == "DELETE":
        return client.delete(path, headers=headers)
    return client.get(path, headers=headers)


@pytest.mark.parametrize("method,path_template,allowed", ACCESS_MATRIX)
@pytest.mark.parametrize("role", ALL_ROLES)
def test_access_matrix(client, scenario, role, method, path_template, allowed):
    path = path_template.format(
        patient_id=scenario["patient_id"], diagnosis_id=scenario["diagnosis_id"]
    )

    response = _send(client, method, path, scenario["headers"][role])

    if role in allowed:
        assert response.status_code != 403, (
            f"{role} should be allowed {method} {path_template}, got 403"
        )
    else:
        assert response.status_code == 403, (
            f"{role} should be denied {method} {path_template}, "
            f"got {response.status_code}"
        )


class TestGuardOrdering:
    """Which guard fires first is a disclosure question, not a detail."""

    def test_a_wrong_role_is_refused_before_the_row_is_looked_up(
        self, client, headers_a
    ):
        """A doctor asking to delete a patient that does not exist must get 403,
        not 404. Otherwise the error code reveals whether an id is real to
        someone who was never allowed the operation."""
        response = client.delete(
            f"/api/v1/patients/{uuid.uuid4()}", headers=headers_a
        )

        assert response.status_code == 403

    def test_a_permitted_role_still_cannot_see_across_tenants(
        self, client, headers_a, headers_admin_b
    ):
        """And with the role satisfied, isolation takes over: 404, never 403,
        so another hospital's row is not confirmed to exist."""
        patient = client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TBROLE02")
        ).json()

        response = client.delete(
            f"/api/v1/patients/{patient['id']}", headers=headers_admin_b
        )

        assert response.status_code == 404


class TestClinicalActsAreDoctorOnly:
    """The matrix split is by function. These are the cells that carry the
    clinical weight, so they are asserted on their own terms rather than only
    as rows in the table above."""

    def test_an_admin_cannot_record_a_diagnosis(
        self, client, headers_a, headers_admin_a
    ):
        patient = client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TBROLE03")
        ).json()

        response = client.post(
            "/api/v1/diagnoses",
            headers=headers_admin_a,
            json={
                "patient_id": patient["id"],
                "is_positive": True,
                "confidence": 91,
                "model_version": "TBScreen v2.1.0",
                "findings": FINDINGS,
            },
        )

        assert response.status_code == 403

    def test_an_admin_cannot_sign_off_a_reading(
        self, client, headers_a, headers_admin_a
    ):
        """Agreeing with the AI is a clinical judgement. Holding more
        administrative privilege does not qualify anyone to make it."""
        patient = client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TBROLE04")
        ).json()
        diagnosis = client.post(
            "/api/v1/diagnoses",
            headers=headers_a,
            json={
                "patient_id": patient["id"],
                "is_positive": True,
                "confidence": 88,
                "model_version": "TBScreen v2.1.0",
                "findings": FINDINGS,
            },
        ).json()

        response = client.patch(
            f"/api/v1/diagnoses/{diagnosis['id']}/status",
            headers=headers_admin_a,
            json={"status": "agreed"},
        )

        assert response.status_code == 403
        after = client.get(
            f"/api/v1/diagnoses/{diagnosis['id']}", headers=headers_a
        ).json()
        assert after["status"] == "pending"

    def test_an_admin_cannot_run_inference(self, client, headers_admin_a):
        response = client.post(
            "/api/v1/diagnoses/infer",
            headers=headers_admin_a,
            files={"image": ("x.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")},
        )

        assert response.status_code == 403

    def test_sync_belongs_to_the_tablet(self, client, headers_admin_a, headers_super):
        """Sync is the offline tablet's path, and only doctors carry tablets."""
        push = client.post(
            "/api/v1/sync/push",
            headers=headers_admin_a,
            json={"device_id": "t", "items": []},
        )
        pull = client.get("/api/v1/sync/pull", headers=headers_admin_a)

        assert push.status_code == 403
        assert pull.status_code == 403


class TestAuthenticationStillPrecedesAuthorization:
    def test_no_token_is_401_not_403(self, client):
        """An anonymous caller is unauthenticated, not unauthorised — the
        distinction matters to clients deciding whether to refresh."""
        assert client.get("/api/v1/patients").status_code == 401

    def test_a_disabled_account_is_refused_whatever_its_role(self, client, users):
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "disabled@rs.co.id", "password": "secret123"},
        )
        assert login.status_code == 403
