"""The models we call, by name, and which one each part of the app runs on.

Model choice is code, and only code: a model is a constant here, each part of the app gets a
tier (a model, with fallbacks behind it), and the evidence for each choice sits beside it. A
swap is one line. Keys come from settings when a model is built, never from the environment;
a missing one fails at startup, naming it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.realtime.openai_live import OpenAILiveModel, OpenAILiveModelSettings

from app.settings import Settings

Effort = Literal["minimal", "low", "medium", "high"]


@dataclass(frozen=True)
class Spec:
    """One model, and how much it reasons (None: the provider's default)."""

    provider: Literal["openai", "openrouter"]
    name: str
    reasoning: Effort | None = None

    def build(self, settings: Settings) -> Model:
        if self.provider == "openai":
            settings_ = OpenAIResponsesModelSettings()
            if self.reasoning:
                settings_["openai_reasoning_effort"] = self.reasoning
            return OpenAIResponsesModel(self.name, provider=_openai(settings), settings=settings_)
        router = OpenRouterModelSettings()
        if self.reasoning:
            router["openrouter_reasoning"] = {"effort": self.reasoning}
        return OpenRouterModel(self.name, provider=_openrouter(settings), settings=router)


@dataclass(frozen=True)
class Tier:
    """A model and the ones behind it, tried in order when a call fails (an outage, a limit)."""

    chain: tuple[Spec, ...]

    def build(self, settings: Settings) -> Model:
        models = [spec.build(settings) for spec in self.chain]
        return models[0] if len(models) == 1 else FallbackModel(*models)


GPT_6_SOL = Spec("openai", "gpt-6-sol", "low")  # low: ~25% faster than default, same replies

# Texts: quality over speed.
TEXT = Tier((GPT_6_SOL,))
# The call agent records what was said on a call and sends what was promised, while the voice
# keeps talking; every second it takes, the voice talks on out of date. Speed first.
CALL_AGENT = Tier((GPT_6_SOL,))
# Background tasks: OpenAI's own web search tool, so an OpenAI model.
JOB = Tier((GPT_6_SOL,))


@dataclass(frozen=True)
class Models:
    """What each part of the app runs on."""

    text: Model  # texting, and the rolling summary
    call_agent: Model
    job: Model

    @classmethod
    def same(cls, model: Model) -> Models:
        """One model for everything (tests)."""
        return cls(text=model, call_agent=model, job=model)

    @classmethod
    def from_settings(cls, settings: Settings) -> Models:
        return cls(
            text=TEXT.build(settings),
            call_agent=CALL_AGENT.build(settings),
            job=JOB.build(settings),
        )


def live_model(settings: Settings) -> OpenAILiveModel:
    """The voice on calls."""
    live = OpenAILiveModelSettings(openai_live_turn_silence_ms=settings.openai_live_turn_silence_ms)
    if settings.openai_live_backend_model:
        live["openai_live_delegation"] = {"model": settings.openai_live_backend_model}
    return OpenAILiveModel(settings.openai_live_model, provider=_openai(settings), settings=live)


def _openai(settings: Settings) -> OpenAIProvider:
    if settings.openai_api_key is None:
        raise ValueError("OPENAI_API_KEY is not set in backend/.env")
    return OpenAIProvider(api_key=settings.openai_api_key.get_secret_value())


def _openrouter(settings: Settings) -> OpenRouterProvider:
    if settings.openrouter_api_key is None:
        raise ValueError("OPENROUTER_API_KEY is not set in backend/.env")
    return OpenRouterProvider(api_key=settings.openrouter_api_key.get_secret_value())
