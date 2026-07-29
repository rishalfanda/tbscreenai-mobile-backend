"""Regression tests for the four data-integrity defects found in review.

Each class pins down one way medical data could previously be corrupted or
lost. They are grouped here rather than folded into the existing files because
what they share is the failure mode, not the endpoint.
"""

import uuid

from tests.conftest import make_patient_body

FINDINGS = {
    "consolidation": 26.3,
    "cavity": 0.6,
    "effusion": 5.6,
    "fibrotic": 0.0,
    "calcification": 0.0,
}


def _push(client, headers, items, device_id="tablet-uji"):
    return client.post(
        "/api/v1/sync/push",
        headers=headers,
        json={"device_id": device_id, "items": items},
    )


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
    return client.post("/api/v1/diagnoses", headers=headers, json=body).json()


def _status_op(entity_id: str, payload: dict, **extra):
    return {
        "client_op_id": str(uuid.uuid4()),
        "entity_type": "diagnosis",
        "operation": "update",
        "entity_id": entity_id,
        "payload": payload,
        **extra,
    }


class TestSyncEnforcesClinicalRules:
    """C-1 — the sync path used to skip Pydantic entirely for diagnoses, so a
    tablet could write anything the REST endpoint would have rejected."""

    def test_arbitrary_status_is_rejected(self, client, headers_a):
        patient = _patient(client, headers_a)
        diagnosis = _diagnosis(client, headers_a, patient["id"])

        response = _push(
            client, headers_a, [_status_op(diagnosis["id"], {"status": "sembuh_total"})]
        )

        assert response.json()["results"][0]["status"] == "conflict"
        current = client.get(
            f"/api/v1/diagnoses/{diagnosis['id']}", headers=headers_a
        ).json()
        assert current["status"] == "pending"

    def test_disagreeing_without_a_note_is_rejected(self, client, headers_a):
        patient = _patient(client, headers_a)
        diagnosis = _diagnosis(client, headers_a, patient["id"])

        response = _push(
            client, headers_a, [_status_op(diagnosis["id"], {"status": "disagreed"})]
        )

        # Same rule the REST endpoint enforces with a 422 — overruling the AI
        # without stating why leaves no clinical audit trail.
        assert response.json()["results"][0]["status"] == "conflict"
        current = client.get(
            f"/api/v1/diagnoses/{diagnosis['id']}", headers=headers_a
        ).json()
        assert current["status"] == "pending"
        assert current["doctor_note"] is None

    def test_blank_note_counts_as_no_note(self, client, headers_a):
        patient = _patient(client, headers_a)
        diagnosis = _diagnosis(client, headers_a, patient["id"])

        response = _push(
            client,
            headers_a,
            [_status_op(diagnosis["id"], {"status": "disagreed", "doctor_note": "   "})],
        )

        assert response.json()["results"][0]["status"] == "conflict"

    def test_a_properly_formed_verdict_still_applies(self, client, headers_a):
        """The guard must not block legitimate offline work."""
        patient = _patient(client, headers_a)
        diagnosis = _diagnosis(client, headers_a, patient["id"])

        response = _push(
            client,
            headers_a,
            [
                _status_op(
                    diagnosis["id"],
                    {"status": "disagreed", "doctor_note": "Jaringan parut TB lama"},
                )
            ],
        )

        assert response.json()["results"][0]["status"] == "applied"
        current = client.get(
            f"/api/v1/diagnoses/{diagnosis['id']}", headers=headers_a
        ).json()
        assert current["status"] == "disagreed"
        assert current["doctor_note"] == "Jaringan parut TB lama"

    def test_payload_cannot_reach_fields_other_than_the_verdict(
        self, client, headers_a
    ):
        """Only the doctor's verdict is client-mutable. A payload that also
        carries e.g. a confidence score must not rewrite the AI's output."""
        patient = _patient(client, headers_a)
        diagnosis = _diagnosis(client, headers_a, patient["id"])

        _push(
            client,
            headers_a,
            [
                _status_op(
                    diagnosis["id"],
                    {"status": "agreed", "confidence": 1, "is_positive": False},
                )
            ],
        )

        current = client.get(
            f"/api/v1/diagnoses/{diagnosis['id']}", headers=headers_a
        ).json()
        assert current["status"] == "agreed"
        assert current["confidence"] == 88
        assert current["is_positive"] is True


class TestVersionConflictDetection:
    """C-2 — timestamp comparison is only second-accurate because the tablet
    caches DateTime as unix seconds. Two edits inside one second were
    indistinguishable, and the later one silently overwrote the earlier."""

    def test_stale_version_is_a_conflict_even_within_the_same_second(
        self, client, headers_a
    ):
        created = _patient(client, headers_a, "TB009000")
        base_version = created["version"]

        # Someone else writes first. Both writes land in the same second, so
        # the old base_updated_at check could not have told them apart.
        client.put(
            f"/api/v1/patients/{created['id']}",
            headers=headers_a,
            json={"name": "Ditulis Dokter B"},
        )

        response = _push(
            client,
            headers_a,
            [
                {
                    "client_op_id": str(uuid.uuid4()),
                    "entity_type": "patient",
                    "operation": "update",
                    "entity_id": created["id"],
                    "base_version": base_version,
                    "payload": {"name": "Ditulis Dokter A"},
                }
            ],
        )

        assert response.json()["results"][0]["status"] == "conflict"
        current = client.get(
            f"/api/v1/patients/{created['id']}", headers=headers_a
        ).json()
        assert current["name"] == "Ditulis Dokter B"  # NOT overwritten

    def test_current_version_applies(self, client, headers_a):
        created = _patient(client, headers_a, "TB009001")

        response = _push(
            client,
            headers_a,
            [
                {
                    "client_op_id": str(uuid.uuid4()),
                    "entity_type": "patient",
                    "operation": "update",
                    "entity_id": created["id"],
                    "base_version": created["version"],
                    "payload": {"name": "Nama Terbaru"},
                }
            ],
        )

        assert response.json()["results"][0]["status"] == "applied"

    def test_version_increments_on_every_write(self, client, headers_a):
        created = _patient(client, headers_a, "TB009002")
        assert created["version"] == 1

        first = client.put(
            f"/api/v1/patients/{created['id']}",
            headers=headers_a,
            json={"name": "Sekali"},
        ).json()
        second = client.put(
            f"/api/v1/patients/{created['id']}",
            headers=headers_a,
            json={"name": "Dua kali"},
        ).json()

        assert first["version"] == 2
        assert second["version"] == 3

    def test_timestamp_fallback_still_works_for_older_clients(
        self, client, headers_a
    ):
        """Clients that predate base_version must keep syncing."""
        created = _patient(client, headers_a, "TB009003")

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
                    "payload": {"name": "Klien Lama"},
                }
            ],
        )

        assert response.json()["results"][0]["status"] == "applied"


class TestPatientSoftDelete:
    # Deletion is an admin_rs verb, so these use headers_admin_a. What is under
    # test here is soft-delete behaviour, not who may invoke it.
    """C-3 — a hard DELETE of a patient holding diagnoses violated the foreign
    key and surfaced as a 500, and destroyed records that must be retained."""

    def test_deleting_a_screened_patient_succeeds(self, client, headers_a, headers_admin_a):
        patient = _patient(client, headers_a, "TB009100")
        _diagnosis(client, headers_a, patient["id"])

        response = client.delete(
            f"/api/v1/patients/{patient['id']}", headers=headers_admin_a
        )

        assert response.status_code == 204

    def test_the_diagnosis_survives_the_deletion(self, client, headers_a, headers_admin_a):
        patient = _patient(client, headers_a, "TB009101")
        diagnosis = _diagnosis(client, headers_a, patient["id"])

        client.delete(f"/api/v1/patients/{patient['id']}", headers=headers_admin_a)

        # The clinical record is retained even though the patient is hidden.
        kept = client.get(f"/api/v1/diagnoses/{diagnosis['id']}", headers=headers_a)
        assert kept.status_code == 200
        assert kept.json()["patient_id"] == patient["id"]

    def test_a_deleted_patient_is_invisible(self, client, headers_a, headers_admin_a):
        patient = _patient(client, headers_a, "TB009102")
        client.delete(f"/api/v1/patients/{patient['id']}", headers=headers_admin_a)

        assert (
            client.get(
                f"/api/v1/patients/{patient['id']}", headers=headers_a
            ).status_code
            == 404
        )
        listed = client.get("/api/v1/patients", headers=headers_a).json()
        assert all(row["id"] != patient["id"] for row in listed)

    def test_the_code_becomes_reusable(self, client, headers_a, headers_admin_a):
        first = _patient(client, headers_a, "TB009103")
        client.delete(f"/api/v1/patients/{first['id']}", headers=headers_admin_a)

        again = client.post(
            "/api/v1/patients", headers=headers_a, json=make_patient_body("TB009103")
        )

        assert again.status_code == 201
        assert again.json()["id"] != first["id"]

    def test_a_deleted_patient_cannot_be_edited_through_sync(
        self, client, headers_a, headers_admin_a
    ):
        patient = _patient(client, headers_a, "TB009104")
        client.delete(f"/api/v1/patients/{patient['id']}", headers=headers_admin_a)

        response = _push(
            client,
            headers_a,
            [
                {
                    "client_op_id": str(uuid.uuid4()),
                    "entity_type": "patient",
                    "operation": "update",
                    "entity_id": patient["id"],
                    "payload": {"name": "Bangkit Lagi"},
                }
            ],
        )

        assert response.json()["results"][0]["status"] == "conflict"

    def test_delta_pull_carries_a_tombstone(self, client, headers_a, headers_admin_a):
        """A full snapshot omits deleted rows, but a delta must report them —
        otherwise the tablet keeps a patient it should have dropped."""
        patient = _patient(client, headers_a, "TB009105")
        client.delete(f"/api/v1/patients/{patient['id']}", headers=headers_admin_a)

        # `since` is deliberately far in the past rather than the row's own
        # created_at: SQLite's CURRENT_TIMESTAMP is second-granular, so create
        # and delete share a timestamp and the strict `>` boundary would drop
        # the row for reasons that have nothing to do with tombstones.
        delta = client.get(
            "/api/v1/sync/pull",
            headers=headers_a,
            params={"since": "2000-01-01T00:00:00+00:00"},
        ).json()
        snapshot = client.get("/api/v1/sync/pull", headers=headers_a).json()

        tombstone = [p for p in delta["patients"] if p["id"] == patient["id"]]
        assert len(tombstone) == 1
        assert tombstone[0]["deleted_at"] is not None
        assert all(p["id"] != patient["id"] for p in snapshot["patients"])


class TestBatchIsolation:
    """C-4 — only ValidationError and ValueError were caught, so an
    IntegrityError escaped as a 500 and the client lost the verdicts for ops
    the server had already committed."""

    def test_a_duplicate_code_does_not_sink_the_batch(self, client, headers_a):
        _patient(client, headers_a, "TB009200")

        good_before = {
            "client_op_id": str(uuid.uuid4()),
            "entity_type": "patient",
            "operation": "create",
            "entity_id": str(uuid.uuid4()),
            "payload": make_patient_body("TB009201"),
        }
        duplicate = {
            "client_op_id": str(uuid.uuid4()),
            "entity_type": "patient",
            "operation": "create",
            "entity_id": str(uuid.uuid4()),
            "payload": make_patient_body("TB009200"),  # already taken
        }
        good_after = {
            "client_op_id": str(uuid.uuid4()),
            "entity_type": "patient",
            "operation": "create",
            "entity_id": str(uuid.uuid4()),
            "payload": make_patient_body("TB009202"),
        }

        response = _push(client, headers_a, [good_before, duplicate, good_after])

        assert response.status_code == 200
        results = {r["client_op_id"]: r["status"] for r in response.json()["results"]}
        assert results[good_before["client_op_id"]] == "applied"
        assert results[duplicate["client_op_id"]] == "conflict"
        # The item AFTER the failure is the one that used to be lost.
        assert results[good_after["client_op_id"]] == "applied"

    def test_every_item_gets_a_verdict(self, client, headers_a):
        items = [
            {
                "client_op_id": str(uuid.uuid4()),
                "entity_type": "patient",
                "operation": "update",
                "entity_id": str(uuid.uuid4()),  # nonexistent
                "payload": {"name": "Hantu"},
            },
            {
                "client_op_id": str(uuid.uuid4()),
                "entity_type": "patient",
                "operation": "create",
                "entity_id": str(uuid.uuid4()),
                "payload": {"code": "TB009300"},  # missing required fields
            },
            {
                "client_op_id": str(uuid.uuid4()),
                "entity_type": "patient",
                "operation": "create",
                "entity_id": str(uuid.uuid4()),
                "payload": make_patient_body("TB009301"),
            },
        ]

        response = _push(client, headers_a, items)

        assert response.status_code == 200
        assert len(response.json()["results"]) == len(items)
        assert response.json()["results"][-1]["status"] == "applied"

    def test_a_reused_client_assigned_id_is_a_conflict_not_a_crash(
        self, client, headers_a
    ):
        shared_id = str(uuid.uuid4())
        first = {
            "client_op_id": str(uuid.uuid4()),
            "entity_type": "patient",
            "operation": "create",
            "entity_id": shared_id,
            "payload": make_patient_body("TB009400"),
        }
        collision = {
            "client_op_id": str(uuid.uuid4()),
            "entity_type": "patient",
            "operation": "create",
            "entity_id": shared_id,  # same row id, different op
            "payload": make_patient_body("TB009401"),
        }

        response = _push(client, headers_a, [first, collision])

        assert response.status_code == 200
        results = [r["status"] for r in response.json()["results"]]
        assert results[0] == "applied"
        assert results[1] == "conflict"
