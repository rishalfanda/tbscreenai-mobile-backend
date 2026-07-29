"""Tenant isolation — the non-negotiable one.

This is medical data: a doctor at RS Beta must never be able to read, modify or
even confirm the existence of a patient belonging to RS Alpha.
"""

from tests.conftest import make_patient_body


def _create_patient(client, headers) -> dict:
    response = client.post("/api/v1/patients", headers=headers, json=make_patient_body())
    assert response.status_code == 201, response.text
    return response.json()


class TestPatientIsolation:
    def test_other_tenant_cannot_read_patient(self, client, headers_a, headers_b):
        patient = _create_patient(client, headers_a)

        response = client.get(f"/api/v1/patients/{patient['id']}", headers=headers_b)

        # 404, not 403: an existence-revealing error would let RS Beta probe
        # which patient ids RS Alpha holds.
        assert response.status_code == 404

    def test_other_tenant_cannot_list_patient(self, client, headers_a, headers_b):
        patient = _create_patient(client, headers_a)

        response = client.get("/api/v1/patients", headers=headers_b)

        assert response.status_code == 200
        assert all(row["id"] != patient["id"] for row in response.json())

    def test_other_tenant_cannot_update_patient(self, client, headers_a, headers_b):
        patient = _create_patient(client, headers_a)

        response = client.put(
            f"/api/v1/patients/{patient['id']}",
            headers=headers_b,
            json={"name": "Diretas"},
        )

        assert response.status_code == 404
        # ...and the record is untouched.
        still = client.get(f"/api/v1/patients/{patient['id']}", headers=headers_a)
        assert still.json()["name"] == "Pasien Uji"

    def test_other_tenant_cannot_delete_patient(self, client, headers_a, headers_b):
        patient = _create_patient(client, headers_a)

        response = client.delete(f"/api/v1/patients/{patient['id']}", headers=headers_b)

        assert response.status_code == 404
        assert (
            client.get(f"/api/v1/patients/{patient['id']}", headers=headers_a).status_code
            == 200
        )

    def test_same_patient_code_allowed_in_different_hospitals(
        self, client, headers_a, headers_b
    ):
        """Codes are unique per tenant, not globally — RS Beta must not be
        blocked (or informed) by a code RS Alpha already uses."""
        body = make_patient_body(code="TB000001")

        first = client.post("/api/v1/patients", headers=headers_a, json=body)
        second = client.post("/api/v1/patients", headers=headers_b, json=body)

        assert first.status_code == 201
        assert second.status_code == 201


class TestDiagnosisIsolation:
    def test_cannot_attach_diagnosis_to_another_tenants_patient(
        self, client, headers_a, headers_b
    ):
        patient = _create_patient(client, headers_a)

        response = client.post(
            "/api/v1/diagnoses",
            headers=headers_b,
            json={
                "patient_id": patient["id"],
                "is_positive": True,
                "confidence": 90,
                "model_version": "TBScreen v2.1.0",
                "findings": {
                    "consolidation": 20.0,
                    "cavity": 1.0,
                    "effusion": 3.0,
                    "fibrotic": 0.0,
                    "calcification": 0.0,
                },
            },
        )

        assert response.status_code == 404

    def test_other_tenant_cannot_validate_diagnosis(self, client, headers_a, headers_b):
        patient = _create_patient(client, headers_a)
        diagnosis = client.post(
            "/api/v1/diagnoses",
            headers=headers_a,
            json={
                "patient_id": patient["id"],
                "is_positive": True,
                "confidence": 90,
                "model_version": "TBScreen v2.1.0",
                "findings": {
                    "consolidation": 20.0,
                    "cavity": 1.0,
                    "effusion": 3.0,
                    "fibrotic": 0.0,
                    "calcification": 0.0,
                },
            },
        ).json()

        response = client.patch(
            f"/api/v1/diagnoses/{diagnosis['id']}/status",
            headers=headers_b,
            json={"status": "agreed"},
        )

        assert response.status_code == 404


class TestSyncIsolation:
    def test_pull_returns_only_own_tenant_data(self, client, headers_a, headers_b):
        _create_patient(client, headers_a)
        own = _create_patient(client, headers_b)

        response = client.get("/api/v1/sync/pull", headers=headers_b)

        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["patients"]}
        assert ids == {own["id"]}


class TestSuperAdminScope:
    def test_super_admin_without_tenant_header_is_rejected(self, client, headers_super):
        response = client.get("/api/v1/patients", headers=headers_super)

        assert response.status_code == 400

    def test_super_admin_with_tenant_header_sees_that_tenant(
        self, client, headers_a, headers_super, hospitals
    ):
        patient = _create_patient(client, headers_a)

        response = client.get(
            "/api/v1/patients",
            headers={**headers_super, "X-Tenant-Id": str(hospitals["A"].id)},
        )

        assert response.status_code == 200
        assert any(row["id"] == patient["id"] for row in response.json())

    def test_tenant_bound_user_cannot_switch_tenant_via_header(
        self, client, headers_a, headers_b, hospitals
    ):
        """A doctor sending X-Tenant-Id for another hospital must be ignored —
        scope comes from the JWT, never from a client-supplied header."""
        patient = _create_patient(client, headers_a)

        response = client.get(
            "/api/v1/patients",
            headers={**headers_b, "X-Tenant-Id": str(hospitals["A"].id)},
        )

        assert response.status_code == 200
        assert all(row["id"] != patient["id"] for row in response.json())
