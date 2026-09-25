"""Shared pytest fixtures.

Every test runs against its own SQLite database so the suite never touches the
dev Postgres data and can run without Docker. The models use portable types
(Uuid, JSONB→JSON fallback) so this stays representative for the logic under
test; anything Postgres-specific is exercised by the migration itself.
"""

import os
import uuid
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date

import bcrypt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-at-least-32-characters")

# The client fixture is per-test, and TestClient runs the app's lifespan each
# time. Left on, that is one connection attempt to MinIO per test against a
# port nothing is listening on. Storage behaviour has its own suite, which
# builds what it needs explicitly.
os.environ.setdefault("STORAGE_AUTO_CREATE_BUCKET", "false")

# bcrypt's real cost (12 rounds) is the point in production and a waste here:
# the fixtures hash five passwords per test plus a login per auth header, which
# dominated the runtime. 4 rounds is the library minimum and exercises the same
# code path. Production cost is untouched — this only rebinds the test process.
_real_gensalt = bcrypt.gensalt
bcrypt.gensalt = lambda rounds=4, prefix=b"2b": _real_gensalt(4, prefix)  # type: ignore[assignment]

from app.core.database import get_db  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Hospital, ModelVersion, User  # noqa: E402


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(type_, compiler, **kw):
    """SQLite has no JSONB — store the same payload as TEXT/JSON."""
    return "JSON"


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> None:
    """Give every test a fresh login budget.

    slowapi's in-memory counter lives for the life of the process, and almost
    every test logs in to get a header — so without this the suite exhausts the
    limit partway through and the rest fail with 429 for no reason of their own.
    Tests that are *about* the limit set their own budget explicitly.
    """
    limiter.reset()


@pytest.fixture
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    # Capitalised because sessionmaker returns a class, which is the SQLAlchemy
    # convention — pep8-naming reads it as a variable and disagrees.
    TestingSession = sessionmaker(  # noqa: N806
        bind=engine, autoflush=False, expire_on_commit=False
    )
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)

        # StaticPool holds one SQLite connection open for the whole fixture.
        # Without disposing it the connection survives until the garbage
        # collector reaches it, and Python 3.13 reports every one of those as a
        # ResourceWarning — roughly 166 per run, which buries any warning that
        # actually matters. CI runs 3.11 and never showed them.
        engine.dispose()


@pytest.fixture
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    # The audit middleware opens a session of its own, which in production is
    # the point. Here it would reach for Postgres while every other query runs
    # against this in-memory SQLite, so point it at the same session — that is
    # also what lets a test read back the rows the middleware just wrote.
    @contextmanager
    def _audit_session():
        yield db_session

    app.state.audit_session = _audit_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def hospitals(db_session: Session) -> dict[str, Hospital]:
    """Two tenants — the whole point is proving they cannot see each other."""
    rows = {
        "A": Hospital(name="RS Alpha", code="RSA"),
        "B": Hospital(name="RS Beta", code="RSB"),
    }
    for hospital in rows.values():
        db_session.add(hospital)
    db_session.commit()
    return rows

@pytest.fixture(autouse=True)
def model_catalog(request, db_session: Session) -> None:
    """/infer now resolves its version from the catalog, so the catalog has to
    exist for the endpoint to work at all. Autouse because the alternative is
    threading this through every test that touches inference.

    Mark a test with @pytest.mark.empty_catalog to opt out — an empty catalog
    is a real state the sync path still has to survive.
    """
    if request.node.get_closest_marker("empty_catalog"):
        return
    db_session.add(
        ModelVersion(
            version="v1.3.1",
            file_size_mb=47.2,
            release_date=date(2025, 6, 10),
            changelog=["test seed"],
            is_latest=True,
        )
    )
    db_session.commit()

@pytest.fixture
def users(db_session: Session, hospitals: dict[str, Hospital]) -> dict[str, User]:
    rows = {
        "doctor_a": User(
            email="doctor.a@rs.co.id",
            full_name="Dr. A",
            role="doctor",
            tenant_id=hospitals["A"].id,
            hashed_password=hash_password("secret123"),
        ),
        "doctor_b": User(
            email="doctor.b@rs.co.id",
            full_name="Dr. B",
            role="doctor",
            tenant_id=hospitals["B"].id,
            hashed_password=hash_password("secret123"),
        ),
        "admin_a": User(
            email="admin.a@rs.co.id",
            full_name="Admin A",
            role="admin_rs",
            tenant_id=hospitals["A"].id,
            hashed_password=hash_password("secret123"),
        ),
        # A second hospital admin exists so tenant isolation can be tested with
        # a role that IS allowed the verb. Probing a cross-tenant delete as a
        # doctor only ever proves the role guard fired, never the tenant one.
        "admin_b": User(
            email="admin.b@rs.co.id",
            full_name="Admin B",
            role="admin_rs",
            tenant_id=hospitals["B"].id,
            hashed_password=hash_password("secret123"),
        ),
        "super": User(
            email="super@tbscreen.co.id",
            full_name="Super Admin",
            role="super_admin",
            tenant_id=None,
            hashed_password=hash_password("secret123"),
        ),
        "disabled": User(
            email="disabled@rs.co.id",
            full_name="Nonaktif",
            role="doctor",
            tenant_id=hospitals["A"].id,
            hashed_password=hash_password("secret123"),
            is_active=False,
        ),
    }
    for user in rows.values():
        db_session.add(user)
    db_session.commit()
    return rows


def auth_headers(client: TestClient, email: str, password: str = "secret123") -> dict:
    response = client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def headers_a(client: TestClient, users) -> dict:
    return auth_headers(client, "doctor.a@rs.co.id")


@pytest.fixture
def headers_b(client: TestClient, users) -> dict:
    return auth_headers(client, "doctor.b@rs.co.id")


@pytest.fixture
def headers_admin_a(client: TestClient, users) -> dict:
    """Hospital A admin — the role permitted to delete patient records."""
    return auth_headers(client, "admin.a@rs.co.id")


@pytest.fixture
def headers_admin_b(client: TestClient, users) -> dict:
    return auth_headers(client, "admin.b@rs.co.id")


@pytest.fixture
def headers_super(client: TestClient, users) -> dict:
    return auth_headers(client, "super@tbscreen.co.id")


def make_patient_body(code: str | None = None) -> dict:
    return {
        "code": code or f"TB{uuid.uuid4().hex[:6].upper()}",
        "name": "Pasien Uji",
        "age": 40,
        "gender": "Male",
        "status": "Suspected",
        "history": ["entri uji"],
    }


# --- Object storage --------------------------------------------------------
# Every test runs against the real ObjectStorage wrapper wired to an in-memory
# S3, so the wrapper's own error handling is exercised rather than skipped,
# and no test ever reaches a MinIO that may not be running. The storage job
# in CI is where the wrapper meets a real server.

from datetime import UTC, datetime, timedelta  # noqa: E402
from io import BytesIO  # noqa: E402
from typing import Any  # noqa: E402

from botocore.exceptions import ClientError  # noqa: E402

from app.services.storage import ObjectStorage, get_object_storage  # noqa: E402


class InMemoryS3:
    """Enough of the S3 API for the image path, held in a dict.

    `errors` maps an operation name to the exception it should raise, which
    is how a test makes storage unreachable for one call and not another.
    """

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, datetime]] = {}
        self.errors: dict[str, Exception] = {}

    def _fail_if_asked(self, operation: str) -> None:
        if operation in self.errors:
            raise self.errors[operation]

    @staticmethod
    def _missing(operation: str, code: str) -> ClientError:
        return ClientError({"Error": {"Code": code, "Message": code}}, operation)

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if_asked("put_object")
        self.objects[kwargs["Key"]] = (kwargs["Body"], datetime.now(UTC))
        return {}

    def get_object(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if_asked("get_object")
        if kwargs["Key"] not in self.objects:
            raise self._missing("GetObject", "NoSuchKey")
        return {"Body": BytesIO(self.objects[kwargs["Key"]][0])}

    def head_object(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if_asked("head_object")
        if kwargs["Key"] not in self.objects:
            raise self._missing("HeadObject", "404")
        return {}

    def delete_object(self, **kwargs: Any) -> dict[str, Any]:
        self._fail_if_asked("delete_object")
        self.objects.pop(kwargs["Key"], None)
        return {}

    def get_paginator(self, name: str) -> "InMemoryS3":
        assert name == "list_objects_v2"
        return self

    def paginate(self, **kwargs: Any) -> Generator[dict[str, Any], None, None]:
        # Two objects per page, so a caller that stops at the first page is
        # caught by any test with more than two images.
        self._fail_if_asked("list_objects_v2")
        matching = sorted(k for k in self.objects if k.startswith(kwargs["Prefix"]))
        for start in range(0, len(matching), 2):
            yield {
                "Contents": [
                    {"Key": key, "LastModified": self.objects[key][1]}
                    for key in matching[start : start + 2]
                ]
            }

    def age(self, key: str, days: int) -> None:
        """Pretend an object was written `days` ago."""
        data, written = self.objects[key]
        self.objects[key] = (data, written - timedelta(days=days))


@pytest.fixture(autouse=True)
def image_store() -> Generator[InMemoryS3, None, None]:
    s3 = InMemoryS3()
    storage = ObjectStorage(client=s3, bucket="test-images")
    app.dependency_overrides[get_object_storage] = lambda: storage
    yield s3
    app.dependency_overrides.pop(get_object_storage, None)
