"""Settings loaded from .env (keys frozen in .env.example). Owner: Bindu."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ClickHouse
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123
    clickhouse_secure: bool = False
    clickhouse_database: str = "tripwire"
    clickhouse_user: str = "default"
    clickhouse_password: str = "tripwire"
    clickhouse_ro_user: str = "tripwire_ro"
    clickhouse_ro_password: str = "tripwire_ro_pw"

    # Models
    akashml_api_key: str = ""
    akashml_base_url: str = "https://api.akashml.com/v1"
    akashml_model_small: str = ""
    akashml_model_large: str = ""
    openai_api_key: str = ""
    openai_model: str = ""

    # Checkpoint
    checkpoint_url: str = "http://localhost:8000"
    tripwire_token: str = "change-me"
    public: bool = False
    hold_check: str = "stub"  # "stub" | "real"
    tripwire_honeytokens: str = ""

    # Guild
    guild_trigger_url: str = ""
    guild_trigger_key: str = ""

    @property
    def honeytokens(self) -> list[str]:
        return [t.strip() for t in self.tripwire_honeytokens.split(",") if t.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
