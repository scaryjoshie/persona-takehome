"""Model construction from settings. Keys never come from the process environment."""

from __future__ import annotations

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from app.settings import Settings


def agent_model(settings: Settings) -> Model:
    """The shared agent's model. `agent_model` in settings is 'openai:<name>'."""
    provider, _, name = settings.agent_model.partition(":")
    if provider != "openai":
        raise ValueError(f"unsupported agent model provider: {provider!r}")
    if settings.openai_api_key is None:
        raise ValueError("OPENAI_API_KEY is not set in backend/.env")
    return OpenAIResponsesModel(
        name, provider=OpenAIProvider(api_key=settings.openai_api_key.get_secret_value())
    )
