"""Tests for the Sprint 2 security controls.

Covers login rate limiting, upload validation that does not trust the client's
content_type, and the production configuration guard.
"""

import io
from typing import ClassVar

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
VALID_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
VALID_DICOM = b"\x00" * 128 + b"DICM" + b"\x00" * 64


def _infer(client, headers, content: bytes, filename="xray.png", mime="image/png"):
    return client.post(
        "/api/v1/diagnoses/infer",
        headers=headers,
        files={"image": (filename, io.BytesIO(content), mime)},
    )


class TestLoginRateLimit:
    """S-1 — /auth/login was the one unauthenticated endpoint checking a
    secret, and nothing bounded how often it could be asked."""

    def test_repeated_failures_are_eventually_refused(self, client, users):
        wrong = {"email": "doctor.a@rs.co.id", "password": "salah"}

        statuses = [
            client.post("/api/v1/auth/login", json=wrong).status_code
            for _ in range(12)
        ]

        # Default budget is 10 per minute; everything past it is turned away.
        assert statuses[:10] == [401] * 10
        assert 429 in statuses[10:]

    def test_the_limit_counts_attempts_not_failures(self, client, users):
        """Otherwise a correct password would reset the attacker's budget, and
        credential-stuffing across many accounts would go unmetered."""
        good = {"email": "doctor.a@rs.co.id", "password": "secret123"}

        statuses = [
            client.post("/api/v1/auth/login", json=good).status_code
            for _ in range(12)
        ]

        assert statuses[0] == 200
        assert 429 in statuses

    def test_a_refusal_tells_the_client_when_to_retry(self, client, users):
        wrong = {"email": "doctor.a@rs.co.id", "password": "salah"}
        for _ in range(11):
            response = client.post("/api/v1/auth/login", json=wrong)

        assert response.status_code == 429
        # A client that honours this backs off without being told twice.
        assert "retry-after" in {k.lower() for k in response.headers}

    def test_other_endpoints_are_not_rate_limited(self, client, headers_a):
        """The limit protects credential guessing, not ordinary tablet use. A
        doctor working through a patient list must never trip it."""
        statuses = [
            client.get("/api/v1/patients", headers=headers_a).status_code
            for _ in range(25)
        ]

        assert statuses == [200] * 25


class TestUploadValidation:
    """S-3 — content_type is a claim the client makes about its own bytes.
    Nothing verified it, and nothing bounded the size."""

    def test_a_text_file_labelled_as_png_is_rejected(self, client, headers_a):
        """The header said image/png. The bytes did not."""
        response = _infer(client, headers_a, b"halo ini bukan gambar sama sekali")

        assert response.status_code == 415

    def test_the_rejection_does_not_echo_the_claimed_type_back(
        self, client, headers_a
    ):
        """The old message interpolated content_type straight into the response,
        reflecting attacker-supplied text."""
        response = _infer(
            client, headers_a, b"bukan gambar", mime="<script>alert(1)</script>"
        )

        assert response.status_code == 415
        assert "script" not in response.text.lower()

    def test_an_empty_upload_is_rejected(self, client, headers_a):
        response = _infer(client, headers_a, b"")

        assert response.status_code == 400

    def test_an_oversized_upload_is_refused(self, client, headers_a, monkeypatch):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "max_upload_bytes", 1024, raising=False)

        response = _infer(client, headers_a, VALID_PNG + b"\x00" * 4096)

        assert response.status_code == 413

    @pytest.mark.parametrize(
        "content,label",
        [(VALID_PNG, "PNG"), (VALID_JPEG, "JPEG"), (VALID_DICOM, "DICOM")],
    )
    def test_genuine_medical_images_are_accepted(
        self, client, headers_a, content, label
    ):
        """The guard must not reject the formats a real X-ray arrives in."""
        response = _infer(client, headers_a, content)

        assert response.status_code == 200, f"{label} rejected: {response.text}"
        assert response.json()["is_mock"] is True

    def test_the_filename_extension_is_not_what_is_trusted(self, client, headers_a):
        """A genuine JPEG named .png is still a genuine image; a .png that is
        not an image is not. Only the signature decides."""
        good = _infer(client, headers_a, VALID_JPEG, filename="scan.png")
        bad = _infer(client, headers_a, b"MZ\x90\x00", filename="scan.png")

        assert good.status_code == 200
        assert bad.status_code == 415


class TestProductionConfigGuard:
    """S-7 / S-8 — these were checklist lines, i.e. exactly as reliable as
    whoever remembered to read the checklist."""

    SAFE: ClassVar[dict[str, object]] = {
        "env": "production",
        "jwt_secret_key": "x" * 48,
        "cors_origins": ["https://tbscreen.example.id"],
        "database_url": "postgresql+psycopg://u:p@db:5432/tbscreenai",
    }

    def test_a_correctly_configured_production_env_starts(self):
        settings = Settings(**self.SAFE)

        assert settings.is_production

    def test_the_development_secret_is_refused(self):
        with pytest.raises(ValidationError, match="development default"):
            Settings(**{**self.SAFE, "jwt_secret_key": "dev-only-secret-change-me-in-production"})

    def test_a_short_secret_is_refused(self):
        with pytest.raises(ValidationError, match="at least 32"):
            Settings(**{**self.SAFE, "jwt_secret_key": "terlalu-pendek"})

    def test_wildcard_cors_is_refused(self):
        with pytest.raises(ValidationError, match="explicit origins"):
            Settings(**{**self.SAFE, "cors_origins": ["*"]})

    def test_sqlite_in_production_is_refused(self):
        with pytest.raises(ValidationError, match="SQLite"):
            Settings(**{**self.SAFE, "database_url": "sqlite:///./prod.db"})

    def test_every_problem_is_reported_at_once(self):
        """One restart should surface the whole list, not the first item and
        then another failed deploy for the next."""
        with pytest.raises(ValidationError) as excinfo:
            Settings(
                env="production",
                jwt_secret_key="pendek",
                cors_origins=["*"],
                database_url="sqlite:///./prod.db",
            )

        message = str(excinfo.value)
        assert "JWT_SECRET_KEY" in message
        assert "CORS_ORIGINS" in message
        assert "SQLite" in message

    def test_development_keeps_its_convenient_defaults(self):
        """The guard must not make local development miserable."""
        settings = Settings(env="development")

        assert not settings.is_production
        assert settings.cors_origins == ["*"]

class TestMockInferenceProductionGuard:
    """B-1 — the mock decides TB positive/negative with random.random(), and
    nothing stopped that verdict from being served as a clinical result."""

    def test_inference_is_refused_in_production(
        self, client, headers_a, monkeypatch
    ):
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "env", "production", raising=False)

        response = _infer(client, headers_a, VALID_PNG)

        assert response.status_code == 503

    def test_the_refusal_says_the_model_is_missing(
        self, client, headers_a, monkeypatch
    ):
        """A raw stack trace tells the doctor nothing about what to do next."""
        from app.core.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "env", "production", raising=False)

        response = _infer(client, headers_a, VALID_PNG)

        assert "model" in response.text.lower()

    def test_development_still_returns_a_mock_result(self, client, headers_a):
        """The guard must not break the contract the tablet is built against."""
        response = _infer(client, headers_a, VALID_PNG)

        assert response.status_code == 200
        assert response.json()["is_mock"] is True
