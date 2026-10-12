from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-secret-change-me-dev-only-secret"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://postgres@localhost:5432/jabal"
    jwt_secret: str = _DEV_SECRET
    access_ttl_seconds: int = 900          # jeton d'accès : 15 minutes
    refresh_ttl_days: int = 60             # un appareil inactif 60 jours doit se reconnecter
    login_max_failures: int = 5            # tentatives ratées avant blocage
    login_window_seconds: int = 900        # fenêtre de blocage : 15 minutes

    @model_validator(mode="after")
    def _require_real_secret_in_production(self) -> "Settings":
        if self.app_env == "production" and (
            self.jwt_secret == _DEV_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError("JWT_SECRET doit être défini (32 caractères minimum) en production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
