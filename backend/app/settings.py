"""Process settings, read once from backend/.env (or the environment).

Nothing else in the app reads the environment directly.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI: the shared agent's model (text channel AND the GPT-Live delegation backend,
    # so voice is an extension of the text agent; docs 15) plus the Live voice model.
    openai_api_key: SecretStr | None = None
    agent_model: str = "openai:gpt-6-sol"  # placeholder id; confirm current best fast model
    voice_layer: Literal["live", "realtime"] = "live"
    openai_live_model: str = "gpt-live-1"
    openai_live_backend_model: str | None = None  # None = the agent's own model (recommended)

    @field_validator("openai_live_backend_model", mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        return None if v == "" else v

    openai_realtime_model: str = "gpt-realtime-2.1"  # fallback voice layer

    # OpenRouter: the Jev decider (routing judgment calls) and auxiliary tasks.
    openrouter_api_key: SecretStr | None = None
    jev_model: str = "typesafe/jev-1.13"

    # Storage
    database_url: str = "sqlite+aiosqlite:///./onboarding.db"
    credentials_key: SecretStr | None = None  # Fernet key for Integration.credentials

    # Google OAuth (openid/email/profile only; see docs 10)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = None

    # Public base URL, used to build the Gmail link the agent sends
    app_base_url: str = "http://localhost:8000"

    # Optional
    logfire_token: SecretStr | None = None
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
