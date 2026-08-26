"""Sync semantics: idempotency and conflict handling.

Offline-first means the tablet WILL retry. Retrying must never duplicate
medical records, and a stale edit must never silently overwrite newer data.
"""

import uuid

import pytest

from tests.conftest import make_patient_body


def _push(client, headers, items, device_id="tablet-uji"):
    return client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={"device_id": device_id, "items": items},
    )


def _create_op(code: str, entity_id: str | None = None, op_id: str | None = None):
    return {
        "client_op_id": op_id or str(uuid.uuid4()),
        "entity_type": "patient",
        "operation": "create",
        "entity_id": entity_id or str(uuid.uuid4()),
        "payload": make_patient_body(code),
    }


class TestIdempotency:
    def test_same_op_id_twice_is_applied_once(self, client, headers_a):
        op = _create_op("TB000100")

        first = _push(client, headers_a, [op])
        second = _push(client, headers_a, [op])

        assert first.json()["results"][0]["status"] == "applied"
        assert second.json()["results"][0]["status"] == "skipped"

        listed = client.get("/api/v1/patients", headers=headers_a).json()
        assert len([p for p in listed if p["code"] == "TB000100"]) == 1

    def test_repeated_retries_never_duplicate(self, client, headers_a):
        op = _create_op("TB000101")

        for _ in range(5):
            _push(client, headers_a, [op])

        listed = client.get("/api/v1/patients", headers=headers_a).json()
        assert len([p for p in listed if p["code"] == "TB000101"]) == 1

    def test_op_ids_are_scoped_per_tenant(self, client, headers_a, headers_b):
        """The same op id from a different hospital is a different operation —
        the unique constraint is (tenant_id, client_op_id)."""
        shared_op_id = str(uuid.uuid4())

        first = _push(
            client, headers_a, [_create_op("TB000200", op_id=shared_op_id)]
        )
        second = _push(
            client, headers_b, [_create_op("TB000200", op_id=shared_op_id)]
        )

        assert first.json()["results"][0]["status"] == "applied"
        assert second.json()["results"][0]["status"] == "applied"

    def test_batch_mixes_new_and_retried_ops(self, client, headers_a):
        old = _create_op("TB000300")
        _push(client, headers_a, [old])

        new = _create_op("TB000301")
        response = _push(client, headers_a, [old, new])

        statuses = {r["client_op_id"]: r["status"] for r in response.json()["results"]}
        assert statuses[old["client_op_id"]] == "skipped"
        assert statuses[new["client_op_id"]] == "applied"


class TestConflict:
    def test_stale_update_is_flagged_not_applied(self, client, headers_a):
        created = client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TB000400")
        ).json()

        # Client edited against a version older than what the server holds.
        response = _push(
            client,
            headers_a,
            [
                {
                    "client_op_id": str(uuid.uuid4()),
                    "entity_type": "patient",
                    "operation": "update",
                    "entity_id": created["id"],
                    "base_updated_at": "2020-01-01T00:00:00+00:00",
                    "payload": {"name": "Nama Basi"},
                }
            ],
        )

        assert response.json()["results"][0]["status"] == "conflict"
        # Medical data untouched — a doctor resolves this, not the server.
        current = client.get(f"/api/v1/patients/{created['id']}", headers=headers_a)
        assert current.json()["name"] == "Pasien Uji"

    def test_update_of_unknown_entity_is_conflict_not_crash(self, client, headers_a):
        response = _push(
            client,
            headers_a,
            [
                {
                    "client_op_id": str(uuid.uuid4()),
                    "entity_type": "patient",
                    "operation": "update",
                    "entity_id": str(uuid.uuid4()),
                    "payload": {"name": "Hantu"},
                }
            ],
        )

        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "conflict"

    def test_current_base_version_updates_successfully(self, client, headers_a):
        created = client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TB000500")
        ).json()

        response = _push(
            client,
            headers_a,
            [
                {
                    "client_op_id": str(uuid.uuid4()),
                    "entity_type": "patient",
                    "operation": "update",
                    "entity_id": created["id"],
                    "base_updated_at": created["updated_at"],
                    "payload": {"name": "Nama Terbaru"},
                }
            ],
        )

        assert response.json()["results"][0]["status"] == "applied"
        current = client.get(f"/api/v1/patients/{created['id']}", headers=headers_a)
        assert current.json()["name"] == "Nama Terbaru"


class TestPull:
    @pytest.mark.empty_catalog
    def test_pull_returns_server_time_and_latest_model(self, client, headers_a):
        response = client.get("/api/v1/sync/pull", headers=headers_a)

        assert response.status_code == 200
        body = response.json()
        assert "server_time" in body
        assert "patients" in body and "diagnoses" in body
        # No model seeded in the test DB → null, and that must not crash.
        assert body["latest_model"] is None

    def test_pull_since_filters_out_older_rows(self, client, headers_a):
        client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TB000600")
        )

        future = "2099-01-01T00:00:00+00:00"
        response = client.get(
            "/api/v1/sync/pull", headers=headers_a, params={"since": future}
        )

        assert response.json()["patients"] == []
