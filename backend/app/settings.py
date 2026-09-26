"""Process settings, read once from backend/.env (or the environment).

Nothing else in the app reads the environment directly.
"""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # OpenAI: used ONLY for the Realtime voice session (call creation + sideband).
    openai_api_key: SecretStr | None = None
    openai_realtime_model: str = "gpt-realtime-2.1"

    # OpenRouter: everything else (text agent, decider fallback model, one-shot tasks).
    openrouter_api_key: SecretStr | None = None

    # Storage
    database_url: str = "sqlite:///./onboarding.db"
    credentials_key: SecretStr | None = None  # Fernet key for Integration.credentials

    # Google OAuth (openid/email/profile only; see docs 10)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = None

    # Public base URL, used to build the Gmail link the agent sends
    app_base_url: str = "http://localhost:8000"

    # Optional
    jev_api_key: SecretStr | None = None
    logfire_token: SecretStr | None = None
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
