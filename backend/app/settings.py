"""Process settings, read once from backend/.env. Nothing else reads the environment."""

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: SecretStr | None = None
    agent_model: str = "gpt-6-sol"  # texts, and the back office on calls
    agent_reasoning_effort: str = "low"  # measured: ~25% faster than default, same replies
    openai_live_model: str = "gpt-live-1"  # the voice on calls
    openai_live_backend_model: str | None = None  # None = Live's default backend
    transcribe_model: str = "gpt-transcribe"  # voice messages

    openrouter_api_key: SecretStr | None = None  # enables Jev; without it, fixed fallbacks
    jev_model: str = "typesafe/jev-1.13"

    database_url: str = "sqlite+aiosqlite:///./onboarding.db"
    app_base_url: str = "http://localhost:8000"  # public URL, for links the agent texts

    @field_validator("openai_live_backend_model", mode="before")
    @classmethod
    def _empty_is_none(cls, v: object) -> object:
        return None if v == "" else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
