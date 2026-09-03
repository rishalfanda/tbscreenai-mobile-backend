"""Tests for the object storage service (Task 6).

Split deliberately.

The unit tests below pin the wrapper's own decisions — which header goes out,
which botocore failure turns into which domain error — against a stub. What
they do not do is assert that a put followed by a get returns the same bytes:
run against a stub, that only ever tests the stub.

That round trip is the thing Task 6 actually promises, so it is an integration
test against a real S3 implementation, and it skips when there is none. The
suite therefore still runs on a laptop with Docker stopped, and CI runs the
real thing in the `storage` job — the same arrangement as the `migrations` job,
and for the same reason.
"""

import os
from collections.abc import Generator
from typing import Any

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.services.storage import (
    ObjectNotFoundError,
    ObjectStorage,
    StorageUnavailableError,
    build_client,
    get_object_storage,
)

BUCKET = "test-bucket"


def _client_error(code: str, operation: str = "Op") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


def _unreachable() -> EndpointConnectionError:
    return EndpointConnectionError(endpoint_url="http://localhost:9000")


class _StubBody:
    def __init__(self, data: bytes) -> None:
        self._data = data
        self.closed = False

    def read(self) -> bytes:
        return self._data

    def close(self) -> None:
        self.closed = True


class _StubS3:
    """An in-memory stand-in for the four calls this module makes.

    Methods take **kwargs because boto3's parameters are PascalCase, which is
    not a shape a normal Python signature should be twisted into.
    """

    def __init__(
        self,
        buckets: tuple[str, ...] = (),
        errors: dict[str, Exception] | None = None,
    ) -> None:
        self.buckets = set(buckets)
        self.objects: dict[str, bytes] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.bodies: list[_StubBody] = []
        self._errors = errors or {}

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs))
        if name in self._errors:
            raise self._errors[name]

    def head_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self._record("head_bucket", kwargs)
        if kwargs["Bucket"] not in self.buckets:
            raise _client_error("404", "HeadBucket")
        return {}

    def create_bucket(self, **kwargs: Any) -> dict[str, Any]:
        self._record("create_bucket", kwargs)
        self.buckets.add(kwargs["Bucket"])
        return {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("put_object", kwargs)
        self.objects[kwargs["Key"]] = kwargs["Body"]
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("get_object", kwargs)
        if kwargs["Key"] not in self.objects:
            raise _client_error("NoSuchKey", "GetObject")
        body = _StubBody(self.objects[kwargs["Key"]])
        self.bodies.append(body)
        return {"Body": body}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._record("delete_object", kwargs)
        self.objects.pop(kwargs["Key"], None)
        return {}

    def last(self, name: str) -> dict[str, Any]:
        return [kwargs for called, kwargs in self.calls if called == name][-1]


def _storage(stub: _StubS3, sse: str | None = None) -> ObjectStorage:
    return ObjectStorage(client=stub, bucket=BUCKET, server_side_encryption=sse)


class TestEnsureBucket:
    def test_a_missing_bucket_is_created(self):
        stub = _StubS3()

        created = _storage(stub).ensure_bucket()

        assert created is True
        assert BUCKET in stub.buckets

    def test_an_existing_bucket_is_left_alone(self):
        stub = _StubS3(buckets=(BUCKET,))

        created = _storage(stub).ensure_bucket()

        assert created is False
        assert "create_bucket" not in [name for name, _ in stub.calls]

    def test_losing_the_creation_race_counts_as_success(self):
        """Two workers starting together both try to create. One wins, and the
        loser must read its own answer as "the bucket is there", not as an
        error worth crashing over."""
        stub = _StubS3(
            errors={"create_bucket": _client_error("BucketAlreadyOwnedByYou")}
        )

        assert _storage(stub).ensure_bucket() is False

    def test_an_unreachable_endpoint_is_reported_as_unavailable(self):
        stub = _StubS3(errors={"head_bucket": _unreachable()})

        with pytest.raises(StorageUnavailableError):
            _storage(stub).ensure_bucket()

    def test_a_refused_creation_is_reported_as_unavailable(self):
        """Credentials without CreateBucket rights, most likely — which is a
        real deployment shape, and must not be mistaken for success."""
        stub = _StubS3(errors={"create_bucket": _client_error("AccessDenied")})

        with pytest.raises(StorageUnavailableError, match="AccessDenied"):
            _storage(stub).ensure_bucket()

    def test_creation_failing_at_the_transport_is_reported_as_unavailable(self):
        stub = _StubS3(errors={"create_bucket": _unreachable()})

        with pytest.raises(StorageUnavailableError):
            _storage(stub).ensure_bucket()


class TestPutObject:
    def test_the_encryption_header_is_omitted_when_unconfigured(self):
        """MinIO rejects the whole request when the header is present but
        empty, so "not configured" has to mean "not sent"."""
        stub = _StubS3(buckets=(BUCKET,))

        _storage(stub).put_object("xray.png", b"bytes")

        assert "ServerSideEncryption" not in stub.last("put_object")

    def test_the_encryption_header_is_sent_when_configured(self):
        stub = _StubS3(buckets=(BUCKET,))

        _storage(stub, sse="AES256").put_object("xray.png", b"bytes")

        assert stub.last("put_object")["ServerSideEncryption"] == "AES256"

    def test_an_empty_encryption_setting_is_treated_as_unset(self):
        """An env file with STORAGE_SERVER_SIDE_ENCRYPTION= reads as "", which
        is what a half-filled .env produces."""
        stub = _StubS3(buckets=(BUCKET,))

        _storage(stub, sse="").put_object("xray.png", b"bytes")

        assert "ServerSideEncryption" not in stub.last("put_object")

    def test_a_content_type_is_recorded(self):
        stub = _StubS3(buckets=(BUCKET,))

        _storage(stub).put_object("xray.png", b"bytes", content_type="image/png")

        assert stub.last("put_object")["ContentType"] == "image/png"

    def test_a_failed_write_is_reported_as_unavailable(self):
        stub = _StubS3(errors={"put_object": _unreachable()})

        with pytest.raises(StorageUnavailableError):
            _storage(stub).put_object("xray.png", b"bytes")


class TestGetObject:
    def test_a_missing_key_is_distinguishable_from_an_outage(self):
        """The caller's response differs: 404 for one, 503 for the other. A
        single exception type would make that call impossible to get right."""
        stub = _StubS3(buckets=(BUCKET,))

        with pytest.raises(ObjectNotFoundError):
            _storage(stub).get_object("tidak-ada.png")

    def test_a_missing_bucket_is_not_reported_as_a_missing_image(self):
        """NoSuchBucket is misconfiguration. Reporting it as "no image" would
        send someone hunting for a lost X-ray that was never lost."""
        stub = _StubS3(errors={"get_object": _client_error("NoSuchBucket")})

        with pytest.raises(StorageUnavailableError):
            _storage(stub).get_object("xray.png")

    def test_a_transport_failure_is_reported_as_unavailable(self):
        stub = _StubS3(errors={"get_object": _unreachable()})

        with pytest.raises(StorageUnavailableError):
            _storage(stub).get_object("xray.png")

    def test_the_response_stream_is_closed(self):
        """Left open, these leak a connection back to the pool per read."""
        stub = _StubS3(buckets=(BUCKET,))
        storage = _storage(stub)
        storage.put_object("xray.png", b"bytes")

        storage.get_object("xray.png")

        assert stub.bodies[-1].closed is True


class TestDeleteObject:
    def test_deleting_a_key_that_is_not_there_is_not_an_error(self):
        """The orphan sweep in Task 7 has to be safe to re-run after a crash."""
        stub = _StubS3(buckets=(BUCKET,))

        _storage(stub).delete_object("tidak-pernah-ada.png")

    def test_a_failed_delete_is_reported_as_unavailable(self):
        stub = _StubS3(errors={"delete_object": _unreachable()})

        with pytest.raises(StorageUnavailableError):
            _storage(stub).delete_object("xray.png")


class TestClientConstruction:
    def test_the_endpoint_comes_from_configuration(self):
        """ADR-001's "swap the backend without touching code" claim rests on
        exactly this line staying true."""
        settings = get_settings()

        client = build_client(settings)

        assert client.meta.endpoint_url == settings.storage_endpoint_url

    def test_addressing_is_path_style(self):
        """The AWS default puts the bucket in the hostname, which needs
        wildcard DNS MinIO does not have."""
        client = build_client(get_settings())

        assert client.meta.config.s3["addressing_style"] == "path"

    def test_the_shared_handle_uses_the_configured_bucket(self):
        assert get_object_storage().bucket == get_settings().storage_bucket


class TestStartupBootstrap:
    """The bucket has to exist before the first upload, and the API has to
    survive it not existing."""

    def test_the_bucket_is_created_on_startup(self, monkeypatch):
        from app import main

        stub = _StubS3()
        monkeypatch.setattr(main.settings, "storage_auto_create_bucket", True)
        monkeypatch.setattr(main, "get_object_storage", lambda: _storage(stub))

        with TestClient(main.app):
            pass

        assert BUCKET in stub.buckets

    def test_the_api_still_starts_when_storage_is_down(self, monkeypatch):
        """Login, the patient list and sync never touch object storage. Taking
        the whole API down because MinIO is slow to come up would trade a
        narrow outage for a total one."""
        from app import main

        stub = _StubS3(errors={"head_bucket": _unreachable()})
        monkeypatch.setattr(main.settings, "storage_auto_create_bucket", True)
        monkeypatch.setattr(main, "get_object_storage", lambda: _storage(stub))

        with TestClient(main.app) as client:
            response = client.get("/health")

        assert response.status_code == 200

    def test_the_failure_is_written_down(self, monkeypatch, caplog):
        """A storage outage that starts silently is one nobody investigates
        until the first upload fails in a clinic."""
        from app import main

        stub = _StubS3(errors={"head_bucket": _unreachable()})
        monkeypatch.setattr(main.settings, "storage_auto_create_bucket", True)
        monkeypatch.setattr(main, "get_object_storage", lambda: _storage(stub))

        with caplog.at_level("WARNING"), TestClient(main.app):
            pass

        assert "storage" in caplog.text.lower()


# --------------------------------------------------------------------------
# Integration — needs a real S3 implementation, skipped when there is none.
# --------------------------------------------------------------------------

INTEGRATION_BUCKET = "tbscreen-storage-test"


@pytest.fixture(scope="module")
def live_storage() -> Generator[ObjectStorage, None, None]:
    """A handle on real storage, or a skip.

    Uses its own bucket so a test run never touches images in the development
    bucket.

    STORAGE_REQUIRED turns the skip into a failure. CI sets it, because a job
    whose entire purpose is to prove the round trip works must not report green
    by quietly proving nothing.
    """
    settings = get_settings()
    storage = ObjectStorage(
        client=build_client(settings),
        bucket=INTEGRATION_BUCKET,
        server_side_encryption=settings.storage_server_side_encryption,
    )
    try:
        storage.ensure_bucket()
    except StorageUnavailableError as error:
        message = f"no object storage at {settings.storage_endpoint_url}: {error}"
        if os.environ.get("STORAGE_REQUIRED", "").lower() in {"1", "true", "yes"}:
            pytest.fail(message)
        pytest.skip(message)
    yield storage


@pytest.fixture
def live_key(live_storage: ObjectStorage) -> Generator[str, None, None]:
    key = "pytest/xray.bin"
    yield key
    live_storage.delete_object(key)


@pytest.mark.integration
class TestLiveRoundTrip:
    def test_an_image_comes_back_byte_for_byte(self, live_storage, live_key):
        """Task 6's acceptance criterion. A chest X-ray that returns altered is
        worse than one that fails to return: the second is visible.

        The payload is binary and includes a NUL and a lone \\r on purpose —
        the shapes that a transport treating the body as text would mangle.
        """
        original = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 64

        live_storage.put_object(live_key, original, content_type="image/png")

        assert live_storage.get_object(live_key) == original

    def test_a_key_that_was_never_written_raises_not_found(self, live_storage):
        with pytest.raises(ObjectNotFoundError):
            live_storage.get_object("pytest/tidak-ada.bin")

    def test_a_deleted_image_is_gone(self, live_storage):
        key = "pytest/dihapus.bin"
        live_storage.put_object(key, b"data")

        live_storage.delete_object(key)

        with pytest.raises(ObjectNotFoundError):
            live_storage.get_object(key)

    def test_ensure_bucket_is_idempotent(self, live_storage):
        """It runs on every startup, including restarts."""
        assert live_storage.ensure_bucket() is False
