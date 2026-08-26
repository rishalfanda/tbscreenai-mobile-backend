"""CRUD, validation rules, and the mock inference endpoint."""

import io
import uuid

from tests.conftest import make_patient_body

FINDINGS = {
    "consolidation": 26.3,
    "cavity": 0.6,
    "effusion": 5.6,
    "fibrotic": 0.0,
    "calcification": 0.0,
}


def _patient(client, headers, code=None):
    return client.post(
        "/api/v1/patients", headers=headers, json=make_patient_body(code)
    ).json()


def _diagnosis(client, headers, patient_id, **overrides):
    body = {
        "patient_id": patient_id,
        "is_positive": True,
        "confidence": 88,
        "model_version": "TBScreen v2.1.0",
        "processing_time_ms": 2800,
        "findings": FINDINGS,
        **overrides,
    }
    return client.post("/api/v1/diagnoses", headers=headers, json=body)


class TestPatientCrud:
    def test_create_read_update_delete(self, client, headers_a, headers_admin_a):
        created = _patient(client, headers_a, "TB001000")
        assert created["code"] == "TB001000"

        read = client.get(f"/api/v1/patients/{created['id']}", headers=headers_a)
        assert read.status_code == 200

        updated = client.put(
            f"/api/v1/patients/{created['id']}",
            headers=headers_a,
            json={"status": "Positive", "confidence": 95},
        )
        assert updated.json()["status"] == "Positive"
        # Untouched fields survive a partial update.
        assert updated.json()["name"] == "Pasien Uji"

        # Deletion is an admin_rs verb — see the access matrix in app/api/deps.py.
        deleted = client.delete(
            f"/api/v1/patients/{created['id']}", headers=headers_admin_a
        )
        assert deleted.status_code == 204
        assert (
            client.get(f"/api/v1/patients/{created['id']}", headers=headers_a).status_code
            == 404
        )

    def test_duplicate_code_within_hospital_is_rejected(self, client, headers_a):
        _patient(client, headers_a, "TB001100")

        again = client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TB001100")
        )

        assert again.status_code == 409

    def test_search_matches_name_and_code(self, client, headers_a):
        _patient(client, headers_a, "TB001200")

        by_code = client.get(
            "/api/v1/patients", headers=headers_a, params={"q": "TB0012"}
        )
        by_name = client.get(
            "/api/v1/patients", headers=headers_a, params={"q": "pasien"}
        )

        assert len(by_code.json()) == 1
        assert len(by_name.json()) == 1

    def test_invalid_field_values_are_rejected(self, client, headers_a):
        bad_gender = client.post(
            "/api/v1/patients",
            headers=headers_a,
            json={**make_patient_body(), "gender": "Alien"},
        )
        bad_age = client.post(
            "/api/v1/patients",
            headers=headers_a,
            json={**make_patient_body(), "age": 999},
        )

        assert bad_gender.status_code == 422
        assert bad_age.status_code == 422

    def test_client_supplied_tenant_id_in_body_is_ignored(
        self, client, headers_a, hospitals
    ):
        """Even if a client tries, tenant always comes from the token."""
        response = client.post(
            "/api/v1/patients",
            headers=headers_a,
            json={**make_patient_body(), "tenant_id": str(hospitals["B"].id)},
        )

        assert response.status_code == 201
        assert response.json()["tenant_id"] == str(hospitals["A"].id)


class TestDiagnosis:
    def test_create_and_read(self, client, headers_a):
        patient = _patient(client, headers_a)

        created = _diagnosis(client, headers_a, patient["id"])

        assert created.status_code == 201
        assert created.json()["status"] == "pending"
        assert created.json()["findings"]["consolidation"] == 26.3

    def test_disagree_requires_a_clinical_note(self, client, headers_a):
        patient = _patient(client, headers_a)
        diagnosis = _diagnosis(client, headers_a, patient["id"]).json()

        without_note = client.patch(
            f"/api/v1/diagnoses/{diagnosis['id']}/status",
            headers=headers_a,
            json={"status": "disagreed"},
        )
        blank_note = client.patch(
            f"/api/v1/diagnoses/{diagnosis['id']}/status",
            headers=headers_a,
            json={"status": "disagreed", "doctor_note": "   "},
        )

        assert without_note.status_code == 422
        assert blank_note.status_code == 422

    def test_agree_and_disagree_with_note_succeed(self, client, headers_a):
        patient = _patient(client, headers_a)
        diagnosis = _diagnosis(client, headers_a, patient["id"]).json()

        agreed = client.patch(
            f"/api/v1/diagnoses/{diagnosis['id']}/status",
            headers=headers_a,
            json={"status": "agreed", "doctor_note": "Pola sesuai TB."},
        )
        assert agreed.json()["status"] == "agreed"

        disagreed = client.patch(
            f"/api/v1/diagnoses/{diagnosis['id']}/status",
            headers=headers_a,
            json={"status": "disagreed", "doctor_note": "Jaringan parut lama."},
        )
        assert disagreed.json()["status"] == "disagreed"

    def test_invalid_status_value_is_rejected(self, client, headers_a):
        patient = _patient(client, headers_a)
        diagnosis = _diagnosis(client, headers_a, patient["id"]).json()

        response = client.patch(
            f"/api/v1/diagnoses/{diagnosis['id']}/status",
            headers=headers_a,
            json={"status": "mungkin"},
        )

        assert response.status_code == 422

    def test_filters_by_patient_and_status(self, client, headers_a):
        first = _patient(client, headers_a, "TB002000")
        second = _patient(client, headers_a, "TB002001")
        _diagnosis(client, headers_a, first["id"])
        _diagnosis(client, headers_a, second["id"])

        by_patient = client.get(
            "/api/v1/diagnoses", headers=headers_a, params={"patient_id": first["id"]}
        )
        by_status = client.get(
            "/api/v1/diagnoses", headers=headers_a, params={"status_filter": "agreed"}
        )

        assert len(by_patient.json()) == 1
        assert by_status.json() == []

    def test_unknown_patient_is_404(self, client, headers_a):
        response = _diagnosis(client, headers_a, str(uuid.uuid4()))

        assert response.status_code == 404


class TestMockInference:
    def test_returns_structured_result_flagged_as_mock(self, client, headers_a):
        image = io.BytesIO(b"\x89PNG\r\n\x1a\npalsu")

        response = client.post(
            "/api/v1/diagnoses/infer",
            headers=headers_a,
            files={"image": ("xray.png", image, "image/png")},
        )

        assert response.status_code == 200
        body = response.json()
        # is_mock must stay true until a real model is wired in — the tablet
        # relies on it to label the result.
        assert body["is_mock"] is True
        assert 0 <= body["confidence"] <= 100
        assert set(body["findings"]) == set(FINDINGS)

    def test_the_version_matches_the_sync_catalog(self, client, headers_a):
        """Result screen and Sync Center must name the same model. Two naming
        schemes meant a stored result could not be traced to a catalogued
        version, which is the whole point of recording one."""
        image = io.BytesIO(b"\x89PNG\r\n\x1a\npalsu")

        inferred = client.post(
            "/api/v1/diagnoses/infer",
            headers=headers_a,
            files={"image": ("xray.png", image, "image/png")},
        )
        catalogued = client.get("/api/v1/sync/model-version", headers=headers_a)

        assert inferred.status_code == 200
        assert catalogued.status_code == 200
        assert inferred.json()["model_version"] == catalogued.json()["version"]

    def test_rejects_unsupported_content_type(self, client, headers_a):
        response = client.post(
            "/api/v1/diagnoses/infer",
            headers=headers_a,
            files={"image": ("catatan.txt", io.BytesIO(b"halo"), "text/plain")},
        )

        assert response.status_code == 415

    def test_requires_authentication(self, client, users):
        response = client.post(
            "/api/v1/diagnoses/infer",
            files={"image": ("xray.png", io.BytesIO(b"x"), "image/png")},
        )

        assert response.status_code == 401
