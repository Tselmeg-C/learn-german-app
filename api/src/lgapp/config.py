from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="LGAPP_",
        extra="ignore",
    )

    env: Literal["local", "ci", "staging", "production"] = "local"
    log_level: str = "INFO"

    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+asyncpg://lgapp:lgapp@localhost:5432/lgapp"),
    )
    # Supavisor/PgBouncer sit in front of Postgres in deployed environments, so each API
    # process keeps a small pool; the pooler owns the real connection fan-in.
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_echo: bool = False

    # Supabase project ref, e.g. "abcdefghijklmnop". JWKS and issuer are derived from it.
    supabase_project_ref: str = ""
    supabase_url: str = ""
    jwks_cache_seconds: int = 600

    cors_origins: list[str] = ["http://localhost:5173"]

    @property
    def supabase_base_url(self) -> str:
        if self.supabase_url:
            return self.supabase_url.rstrip("/")
        return f"https://{self.supabase_project_ref}.supabase.co"

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_base_url}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_base_url}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    return Settings()
