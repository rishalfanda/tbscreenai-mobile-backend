from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Short enough to brute-force offline in minutes. An HS256 signature is only
# worth what its key is worth: whoever recovers it mints valid tokens for any
# user in any hospital.
MIN_PRODUCTION_SECRET_LENGTH = 32

_DEV_SECRET_DEFAULT = "dev-only-secret-change-me-in-production"
_DEV_STORAGE_SECRET_DEFAULT = "tbscreen_dev_storage"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "TBScreenAI API"
    api_v1_prefix: str = "/api/v1"

    # Drives the production safety checks below. Set ENV=production on any
    # deployment real patient data touches.
    env: Literal["development", "staging", "production"] = "development"

    database_url: str = (
        "postgresql+psycopg://tbscreen:tbscreen_dev@localhost:5432/tbscreenai"
    )

    jwt_secret_key: str = _DEV_SECRET_DEFAULT
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Dev default "*" because Flutter web dev servers pick random ports. Auth
    # travels in the Authorization header rather than a cookie, so this is not
    # a CSRF hole — but production refuses it outright below.
    cors_origins: list[str] = ["*"]

    # Chest X-rays are large, DICOM especially; 25 MB covers a full-resolution
    # study while bounding what a single request can make the server hold.
    max_upload_bytes: int = 25 * 1024 * 1024

    # Login attempts per client IP per window. Loose enough that a doctor
    # mistyping on a tablet keyboard never notices, tight enough that guessing
    # at scale stops being viable.
    login_rate_limit: str = "10/minute"

    # Object storage. S3-compatible, so the endpoint is a deployment decision
    # rather than a dependency baked into the code.
    storage_endpoint_url: str = "http://localhost:9000"
    storage_access_key: str = "tbscreen"
    storage_secret_key: str = _DEV_STORAGE_SECRET_DEFAULT
    storage_bucket: str = "tbscreen-images"

    # Sent as the ServerSideEncryption header when set. Empty in development
    # because MinIO rejects it until a key service is configured; production
    # deployments are expected to have one.
    storage_server_side_encryption: str | None = None

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @model_validator(mode="after")
    def _refuse_unsafe_production_config(self) -> "Settings":
        """Fail at startup rather than serve patient data on dev defaults.

        Each of these was previously a line in a pre-launch checklist, which is
        to say: exactly as reliable as whoever remembered to read it.
        """
        if not self.is_production:
            return self

        problems: list[str] = []

        if self.jwt_secret_key == _DEV_SECRET_DEFAULT:
            problems.append("JWT_SECRET_KEY is still the development default")
        elif len(self.jwt_secret_key) < MIN_PRODUCTION_SECRET_LENGTH:
            problems.append(
                f"JWT_SECRET_KEY must be at least {MIN_PRODUCTION_SECRET_LENGTH} "
                f"characters (got {len(self.jwt_secret_key)})"
            )

        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must list explicit origins, not '*'")

        if self.storage_secret_key == _DEV_STORAGE_SECRET_DEFAULT:
            problems.append("STORAGE_SECRET_KEY is still the development default")

        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL points at SQLite")

        if problems:
            raise ValueError(
                "Refusing to start in production:\n  - " + "\n  - ".join(problems)
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
