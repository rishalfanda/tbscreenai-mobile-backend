"""Object storage for chest X-rays.

Images do not belong in Postgres: a single study is megabytes where a record is
kilobytes, and mixing them makes every backup and every replica carry the
imaging archive. ADR-001 chose MinIO, and the deciding reason was data
sovereignty rather than scale — images must be able to stay inside hospital
infrastructure.

Everything here speaks the S3 API and nothing above this module imports boto3.
That is the point: MinIO in development, and whatever a deployment decides in
the field, without a line of application code changing. Callers see three
domain errors instead of botocore's exception surface, so a route can map a
failure to an HTTP status without knowing what an S3 client is.
"""

import logging
from functools import lru_cache
from typing import Any

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Codes create_bucket returns when someone else won the race. Both mean the
# bucket exists now, which is all ensure_bucket promises.
_ALREADY_EXISTS = frozenset({"BucketAlreadyOwnedByYou", "BucketAlreadyExists"})

# get_object's answer for a key that is not there. MinIO also answers
# "NoSuchBucket" when the bucket itself is gone, and that is a different
# problem — a missing bucket is misconfiguration, not a missing image.
_NO_SUCH_KEY = "NoSuchKey"

DEFAULT_CONTENT_TYPE = "application/octet-stream"


class StorageError(RuntimeError):
    """Base class for every failure raised out of this module."""


class StorageUnavailableError(StorageError):
    """Storage could not be reached, or refused the credentials.

    Distinct from ObjectNotFoundError because the right response differs: this one
    means try again later, and the caller should not present it as "no image".
    """


class ObjectNotFoundError(StorageError):
    """The key does not exist in the bucket."""


def _error_code(error: ClientError) -> str:
    """Pull the S3 error code out of a ClientError, tolerating an odd shape."""
    response = getattr(error, "response", None) or {}
    return str(response.get("Error", {}).get("Code", ""))


def build_client(settings: Settings) -> Any:
    """Construct a boto3 S3 client pointed at the configured endpoint.

    Two settings here are not optional against MinIO:

    * path-style addressing — MinIO serves every bucket from one host, while
      the AWS default puts the bucket in the hostname and expects wildcard DNS
      that no deployment in this project has;
    * signature v4 — what MinIO validates against.

    The timeouts exist because botocore defaults to sixty seconds. A storage
    node that has stopped answering should surface in five, not hold a request
    thread for a minute while a doctor watches a spinner.
    """
    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint_url,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
        region_name=settings.storage_region,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            connect_timeout=5,
            read_timeout=30,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


class ObjectStorage:
    """A narrow door onto one bucket.

    Deliberately not a general S3 wrapper. The four operations below are what
    the imaging path needs, and keeping the surface small is what makes the
    "swap MinIO for something else" claim in ADR-001 checkable rather than
    aspirational.

    The client is injected rather than built internally so a test can hand in
    a stub, the same way get_db is overridden in the route tests.
    """

    def __init__(
        self,
        client: Any,
        bucket: str,
        server_side_encryption: str | None = None,
    ) -> None:
        self._client = client
        self._bucket = bucket
        # Empty string and None both mean "do not send the header at all".
        # Sending ServerSideEncryption="" makes MinIO reject the whole request.
        self._server_side_encryption = server_side_encryption or None

    @property
    def bucket(self) -> str:
        return self._bucket

    def ensure_bucket(self) -> bool:
        """Create the bucket if it is missing. True if this call created it.

        Idempotent and safe to run concurrently: two processes starting at once
        both try to create, one wins, and the loser reads its own answer as
        success.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            # Could be 404 (absent) or 403 (present but not ours to inspect).
            # Rather than branch on that, try to create and let create_bucket's
            # own error be the authoritative one — it is the more informative
            # of the two.
            pass
        except BotoCoreError as error:
            raise StorageUnavailableError(
                f"Cannot reach object storage at {self._endpoint()}"
            ) from error
        else:
            return False

        try:
            self._client.create_bucket(Bucket=self._bucket)
        except ClientError as error:
            if _error_code(error) in _ALREADY_EXISTS:
                return False
            raise StorageUnavailableError(
                f"Cannot create bucket {self._bucket!r}: {_error_code(error)}"
            ) from error
        except BotoCoreError as error:
            raise StorageUnavailableError(
                f"Cannot reach object storage at {self._endpoint()}"
            ) from error

        logger.info("Created object storage bucket %r", self._bucket)
        return True

    def put_object(
        self,
        key: str,
        data: bytes,
        content_type: str = DEFAULT_CONTENT_TYPE,
    ) -> None:
        """Store bytes under key, overwriting whatever was there."""
        extra: dict[str, str] = {}
        if self._server_side_encryption is not None:
            extra["ServerSideEncryption"] = self._server_side_encryption

        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                **extra,
            )
        except (ClientError, BotoCoreError) as error:
            raise StorageUnavailableError(
                f"Could not store object {key!r}: {error}"
            ) from error

    def get_object(self, key: str) -> bytes:
        """Return the bytes stored under key.

        Reads the whole object into memory, which is correct at this size —
        an X-ray is single-digit megabytes and the upload ceiling is 25 MB.
        Model artefacts are ~500 MB and must not use this method; Task 17
        streams those.
        """
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as error:
            if _error_code(error) == _NO_SUCH_KEY:
                raise ObjectNotFoundError(f"No object at key {key!r}") from error
            raise StorageUnavailableError(
                f"Could not read object {key!r}: {_error_code(error)}"
            ) from error
        except BotoCoreError as error:
            raise StorageUnavailableError(
                f"Could not read object {key!r}: {error}"
            ) from error

        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def delete_object(self, key: str) -> None:
        """Remove key. Deleting something that is not there is not an error.

        S3 delete is idempotent by design, and the orphan-image cleanup in
        Task 7 depends on that: a sweep that crashes halfway must be safe to
        re-run.
        """
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except (ClientError, BotoCoreError) as error:
            raise StorageUnavailableError(
                f"Could not delete object {key!r}: {error}"
            ) from error

    def _endpoint(self) -> str:
        """Best-effort endpoint for error messages; never the reason to fail."""
        return str(getattr(getattr(self._client, "meta", None), "endpoint_url", "?"))


@lru_cache
def get_object_storage() -> ObjectStorage:
    """The application's storage handle, built once.

    Cached like get_settings for the same reason — and because a boto3 client
    is thread-safe once constructed but not cheap to construct. Usable as a
    FastAPI dependency, which is what lets Task 7's route tests substitute a
    fake without reaching into this module.
    """
    settings = get_settings()
    return ObjectStorage(
        client=build_client(settings),
        bucket=settings.storage_bucket,
        server_side_encryption=settings.storage_server_side_encryption,
    )
