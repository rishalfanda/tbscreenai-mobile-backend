"""Task 7 — keeping the X-ray a diagnosis was made on, and giving it back.

Three promises, each pinned below. The image comes back exactly as it was
kept, bit for bit, because image quality is not negotiable. A hospital can
never reach another hospital's image, however the request is shaped. And a
DICOM file is de-identified before anything is kept, so what storage holds
never carries the patient's identity.
"""

import uuid
from datetime import UTC, datetime
from io import BytesIO

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient
from pydicom import dcmread
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit import AccessLog
from app.models.diagnosis import Diagnosis
from app.models.hospital import Hospital
from app.services.images import find_orphans, image_key
from app.services.storage import ObjectStorage, StorageUnavailableError
from tests.conftest import InMemoryS3, make_patient_body
from tests.test_deidentification import IDENTIFYING, PIXELS, _dirty_xray

PNG = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 40
JPEG = b"\xff\xd8\xff\xe0" + bytes(reversed(range(256))) * 40


def _infer(client: TestClient, headers: dict, content: bytes, name: str = "xray.bin"):
    return client.post(
        "/api/v1/diagnoses/infer",
        headers=headers,
        files={"image": (name, content, "application/octet-stream")},
    )


def _patient(client: TestClient, headers: dict, code: str) -> str:
    response = client.post("/api/v1/patients", headers=headers, json=make_patient_body(code))
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _save(client: TestClient, headers: dict, patient_id: str, inferred: dict, **extra):
    body = {
        "patient_id": patient_id,
        "is_positive": inferred["is_positive"],
        "confidence": inferred["confidence"],
        "model_version": inferred["model_version"],
        "findings": inferred["findings"],
        "image_id": inferred.get("image_id"),
        **extra,
    }
    return client.post("/api/v1/diagnoses", headers=headers, json=body)


def _screen(client: TestClient, headers: dict, content: bytes, code: str) -> dict:
    """Infer, then save the diagnosis with the image it returned."""
    inferred = _infer(client, headers, content)
    assert inferred.status_code == 200, inferred.text
    saved = _save(client, headers, _patient(client, headers, code), inferred.json())
    assert saved.status_code == 201, saved.text
    return saved.json()


class TestTheImageComesBackExactly:
    @pytest.mark.parametrize(
        ("content", "media_type"), [(PNG, "image/png"), (JPEG, "image/jpeg")]
    )
    def test_infer_save_fetch_returns_identical_bytes(
        self, client: TestClient, headers_a: dict, content: bytes, media_type: str
    ) -> None:
        # Task 7's verification item. Not re-encoded, not resized, not
        # recompressed: the same bytes that were uploaded.
        diagnosis = _screen(client, headers_a, content, "TB000701")

        fetched = client.get(f"/api/v1/diagnoses/{diagnosis['id']}/image", headers=headers_a)

        assert fetched.status_code == 200
        assert fetched.content == content
        assert fetched.headers["content-type"] == media_type
        assert fetched.headers["cache-control"] == "no-store"
        assert fetched.headers["x-content-type-options"] == "nosniff"

    def test_a_dicom_comes_back_de_identified_with_its_pixels_untouched(
        self, client: TestClient, headers_a: dict
    ) -> None:
        diagnosis = _screen(client, headers_a, _dirty_xray(), "TB000702")

        fetched = client.get(f"/api/v1/diagnoses/{diagnosis['id']}/image", headers=headers_a)

        assert fetched.status_code == 200
        assert fetched.headers["content-type"] == "application/dicom"
        for value in IDENTIFYING:
            assert value.encode() not in fetched.content
        kept = dcmread(BytesIO(fetched.content))
        assert kept.PixelData == PIXELS
        assert kept.PatientIdentityRemoved == "YES"

    def test_the_image_is_kept_under_its_hospital(
        self,
        client: TestClient,
        headers_a: dict,
        hospitals: dict[str, Hospital],
        image_store: InMemoryS3,
    ) -> None:
        inferred = _infer(client, headers_a, PNG).json()

        key = image_key(hospitals["A"].id, uuid.UUID(inferred["image_id"]))
        assert list(image_store.objects) == [key]
        assert image_store.objects[key][0] == PNG


class TestOnlyTheOwningHospitalReachesIt:
    def test_another_hospital_gets_404_for_the_image(
        self, client: TestClient, headers_a: dict, headers_b: dict
    ) -> None:
        diagnosis = _screen(client, headers_a, PNG, "TB000710")

        response = client.get(f"/api/v1/diagnoses/{diagnosis['id']}/image", headers=headers_b)

        assert response.status_code == 404
        assert PNG not in response.content

    def test_another_hospitals_image_id_cannot_be_claimed(
        self, client: TestClient, headers_a: dict, headers_b: dict
    ) -> None:
        # B learns A's image id somehow and tries to attach it to its own
        # diagnosis. The key is built from B's hospital, so it finds nothing,
        # and the refusal is the same as for an id that was never issued.
        stolen = _infer(client, headers_a, PNG).json()

        response = _save(client, headers_b, _patient(client, headers_b, "TB000711"), stolen)

        assert response.status_code == 422
        never_issued = {**stolen, "image_id": str(uuid.uuid4())}
        refusal = _save(client, headers_b, _patient(client, headers_b, "TB000712"), never_issued)
        assert refusal.json() == response.json()

    def test_a_path_sent_by_the_client_is_ignored(
        self, client: TestClient, headers_a: dict, hospitals: dict[str, Hospital]
    ) -> None:
        # The old field. Accepting it would let a client point its diagnosis
        # at any object in the bucket, another hospital's included.
        foreign = image_key(hospitals["B"].id, uuid.uuid4())
        inferred = _infer(client, headers_a, PNG).json()
        inferred.pop("image_id")

        saved = _save(
            client, headers_a, _patient(client, headers_a, "TB000713"), inferred,
            image_path=foreign,
        )

        assert saved.status_code == 201
        assert saved.json()["image_path"] is None

    def test_sync_does_not_take_a_path_from_the_tablet_either(
        self, client: TestClient, headers_a: dict, hospitals: dict[str, Hospital]
    ) -> None:
        patient_id = _patient(client, headers_a, "TB000714")
        inferred = _infer(client, headers_a, PNG).json()
        payload = {
            "patient_id": patient_id,
            "is_positive": inferred["is_positive"],
            "confidence": inferred["confidence"],
            "model_version": inferred["model_version"],
            "findings": inferred["findings"],
            "image_path": image_key(hospitals["B"].id, uuid.uuid4()),
            "image_id": inferred["image_id"],
        }
        response = client.post(
            "/api/v1/sync/push",
            headers=headers_a,
            json={"device_id": "tablet-uji", "items": [{
                "client_op_id": str(uuid.uuid4()),
                "entity_type": "diagnosis",
                "operation": "create",
                "entity_id": str(uuid.uuid4()),
                "payload": payload,
            }]},
        )

        assert response.json()["results"][0]["status"] == "applied"
        diagnosis_id = response.json()["results"][0]["entity_id"]
        stored = client.get(f"/api/v1/diagnoses/{diagnosis_id}", headers=headers_a)
        assert stored.json()["image_path"] is None

    def test_an_old_row_pointing_elsewhere_is_never_served(
        self,
        client: TestClient,
        db_session: Session,
        headers_a: dict,
        headers_b: dict,
        hospitals: dict[str, Hospital],
    ) -> None:
        # Rows written before image_id existed took image_path from the
        # client. Even if one names a real object of another hospital, the
        # endpoint refuses to read outside the caller's own prefix.
        victim = _screen(client, headers_b, PNG, "TB000715")
        victim_path = victim["image_path"]
        mine = _screen(client, headers_a, JPEG, "TB000716")
        row = db_session.get(Diagnosis, uuid.UUID(mine["id"]))
        assert row is not None
        row.image_path = victim_path
        db_session.commit()

        response = client.get(f"/api/v1/diagnoses/{mine['id']}/image", headers=headers_a)

        assert response.status_code == 404
        assert PNG not in response.content

    def test_every_look_at_an_image_is_in_the_access_trail(
        self, client: TestClient, db_session: Session, headers_a: dict, users
    ) -> None:
        # The security design's "who opened which image": the read is logged
        # against the diagnosis, with the doctor who made it.
        diagnosis = _screen(client, headers_a, PNG, "TB000717")
        client.get(f"/api/v1/diagnoses/{diagnosis['id']}/image", headers=headers_a)

        row = db_session.scalars(
            select(AccessLog).where(AccessLog.path.endswith("/image"))
        ).one()
        assert str(row.resource_id) == diagnosis["id"]
        assert row.actor_user_id == users["doctor_a"].id
        assert row.action == "read"


class TestWhatIsRefused:
    def test_declared_burned_in_text_is_refused_and_nothing_kept(
        self, client: TestClient, headers_a: dict, image_store: InMemoryS3
    ) -> None:
        response = _infer(client, headers_a, _dirty_xray(burned_in="YES"))

        assert response.status_code == 422
        assert "without annotations" in response.json()["detail"]
        assert image_store.objects == {}

    def test_a_file_that_only_looks_like_dicom_is_refused(
        self, client: TestClient, headers_a: dict, image_store: InMemoryS3
    ) -> None:
        # The signature check alone used to accept this. It carries the
        # marker and nothing else, so there is nothing to de-identify and
        # nothing worth keeping.
        response = _infer(client, headers_a, b"\x00" * 128 + b"DICM" + b"\x00" * 64)

        assert response.status_code == 422
        assert image_store.objects == {}

    def test_an_unknown_image_id_is_refused(self, client: TestClient, headers_a: dict) -> None:
        inferred = {**_infer(client, headers_a, PNG).json(), "image_id": str(uuid.uuid4())}

        response = _save(client, headers_a, _patient(client, headers_a, "TB000720"), inferred)

        assert response.status_code == 422

    def test_a_diagnosis_without_an_image_has_nothing_to_fetch(
        self, client: TestClient, headers_a: dict
    ) -> None:
        inferred = _infer(client, headers_a, PNG).json()
        inferred.pop("image_id")
        saved = _save(client, headers_a, _patient(client, headers_a, "TB000721"), inferred)

        response = client.get(f"/api/v1/diagnoses/{saved.json()['id']}/image", headers=headers_a)

        assert response.status_code == 404

    def test_an_image_gone_from_storage_is_404_not_500(
        self, client: TestClient, headers_a: dict, image_store: InMemoryS3
    ) -> None:
        diagnosis = _screen(client, headers_a, PNG, "TB000722")
        image_store.objects.clear()

        response = client.get(f"/api/v1/diagnoses/{diagnosis['id']}/image", headers=headers_a)

        assert response.status_code == 404


class TestWhenStorageIsDown:
    """503, and a message to try again. Never a result without its image."""

    def test_infer_refuses_rather_than_losing_the_image(
        self, client: TestClient, headers_a: dict, image_store: InMemoryS3
    ) -> None:
        image_store.errors["put_object"] = EndpointConnectionError(endpoint_url="http://s3")

        response = _infer(client, headers_a, PNG)

        assert response.status_code == 503
        assert "is_positive" not in response.text

    def test_saving_with_an_image_waits_for_storage(
        self, client: TestClient, headers_a: dict, image_store: InMemoryS3
    ) -> None:
        inferred = _infer(client, headers_a, PNG).json()
        image_store.errors["head_object"] = EndpointConnectionError(endpoint_url="http://s3")

        response = _save(client, headers_a, _patient(client, headers_a, "TB000730"), inferred)

        assert response.status_code == 503

    def test_fetching_waits_for_storage(
        self, client: TestClient, headers_a: dict, image_store: InMemoryS3
    ) -> None:
        diagnosis = _screen(client, headers_a, PNG, "TB000731")
        image_store.errors["get_object"] = EndpointConnectionError(endpoint_url="http://s3")

        response = client.get(f"/api/v1/diagnoses/{diagnosis['id']}/image", headers=headers_a)

        assert response.status_code == 503


class TestOrphans:
    """Images inferred but never claimed by a diagnosis, and only those."""

    def test_only_old_unclaimed_images_are_orphans(
        self,
        client: TestClient,
        db_session: Session,
        headers_a: dict,
        headers_b: dict,
        image_store: InMemoryS3,
    ) -> None:
        # Two old unclaimed images from two hospitals, one fresh unclaimed
        # image, and one old image a diagnosis claims. Only the first two may
        # go, and they come from different hospitals and different pages of
        # the listing.
        claimed = _screen(client, headers_a, PNG, "TB000740")["image_path"]
        before = set(image_store.objects)
        _infer(client, headers_a, JPEG)
        _infer(client, headers_b, JPEG)
        old = sorted(set(image_store.objects) - before)
        _infer(client, headers_b, PNG)
        [fresh] = set(image_store.objects) - before - set(old)
        for key in [*old, claimed]:
            image_store.age(key, days=30)

        storage = ObjectStorage(client=image_store, bucket="test-images")
        orphans = find_orphans(db_session, storage, now=datetime.now(UTC))

        assert sorted(orphans) == old
        assert claimed not in orphans
        assert fresh not in orphans

    def test_listing_that_cannot_reach_storage_says_so(self, image_store: InMemoryS3) -> None:
        image_store.errors["list_objects_v2"] = EndpointConnectionError(endpoint_url="http://s3")
        storage = ObjectStorage(client=image_store, bucket="test-images")

        with pytest.raises(StorageUnavailableError):
            list(storage.list_objects("tenants/"))


class TestExistenceCheck:
    def test_a_refusal_other_than_not_found_is_an_outage(self, image_store: InMemoryS3) -> None:
        # Access denied is not "no such image": treating it as absent would
        # turn a storage misconfiguration into 422s blamed on the client.
        image_store.errors["head_object"] = ClientError(
            {"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject"
        )
        storage = ObjectStorage(client=image_store, bucket="test-images")

        with pytest.raises(StorageUnavailableError):
            storage.exists("tenants/x/images/y")
