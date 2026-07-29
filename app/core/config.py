from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "TBScreenAI API"
    api_v1_prefix: str = "/api/v1"

    database_url: str = (
        "postgresql+psycopg://tbscreen:tbscreen_dev@localhost:5432/tbscreenai"
    )

    jwt_secret_key: str = "dev-only-secret-change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Dev default "*" because Flutter web dev servers use random ports.
    # Auth uses the Authorization header (no cookies), so this is safe for
    # dev — but PIN explicit origins before any real deployment.
    cors_origins: list[str] = ["*"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
