"""The OpenAI models, built once from settings. Keys never come from the process environment."""

from __future__ import annotations

from typing import Literal, cast

from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.realtime.openai_live import OpenAILiveModel, OpenAILiveModelSettings

from app.settings import Settings

ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]


def _provider(settings: Settings) -> OpenAIProvider:
    if settings.openai_api_key is None:
        raise ValueError("OPENAI_API_KEY is not set in backend/.env")
    return OpenAIProvider(api_key=settings.openai_api_key.get_secret_value())


def text_model(settings: Settings) -> Model:
    effort = cast(ReasoningEffort, settings.agent_reasoning_effort)
    return OpenAIResponsesModel(
        settings.agent_model.removeprefix("openai:"),
        provider=_provider(settings),
        settings=OpenAIResponsesModelSettings(openai_reasoning_effort=effort),
    )


def live_model(settings: Settings) -> OpenAILiveModel:
    live = OpenAILiveModelSettings()
    if settings.openai_live_backend_model:
        live["openai_live_delegation"] = {"model": settings.openai_live_backend_model}
    return OpenAILiveModel(settings.openai_live_model, provider=_provider(settings), settings=live)
