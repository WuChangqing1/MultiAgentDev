"""Provider abstraction.

Agents never touch a vendor SDK directly; they talk to :class:`LLMProvider`.
Adding OpenAI / Gemini / Ollama later means adding one file here and one
registry entry -- no agent changes.

Every provider returns an :class:`LLMResponse` whose ``usage`` is a
:class:`~models.token_usage.TokenUsage` populated **only** from values the API
actually reported. Missing values stay ``None``.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from models.token_usage import TokenUsage, utcnow

Role = Literal["system", "user", "assistant"]


@dataclass(slots=True)
class LLMMessage:
    role: Role
    content: str


@dataclass
class LLMResponse:
    """Normalized provider response."""

    content: str
    reasoning: str | None = None
    model: str = ""
    provider: str = ""
    is_local: bool = False
    usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None


class ProviderError(RuntimeError):
    """Transport / API failure after retries were exhausted."""

    def __init__(self, message: str, *, provider: str = "", status: int | None = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status


class ProviderUnavailable(ProviderError):
    """The endpoint could not be reached at all (offline / connection refused)."""


@dataclass
class GenerateOptions:
    """Per-call generation options."""

    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    reasoning_effort: str = "none"
    stream: bool = False
    stop: list[str] | None = None
    #: Free-form provider extras; unsupported keys are dropped by the provider.
    extras: dict[str, Any] = field(default_factory=dict)


class LLMProvider(abc.ABC):
    """Uniform async interface for every model backend."""

    name: str = "provider"
    is_local: bool = False

    @property
    @abc.abstractmethod
    def model(self) -> str:
        """Currently configured model identifier."""

    @abc.abstractmethod
    async def generate(
        self,
        messages: list[LLMMessage],
        options: GenerateOptions | None = None,
    ) -> LLMResponse:
        """Single non-streaming completion."""

    @abc.abstractmethod
    def stream(
        self,
        messages: list[LLMMessage],
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[tuple[str, str | None]]:
        """Yield ``(content_delta, reasoning_delta)`` pairs.

        Implementations are async generators; the declared return type is an
        ``AsyncIterator`` so callers can ``async for`` without caring whether the
        provider truly streams (a provider may emit a single chunk).
        """

    @abc.abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """Return ``{"status": ..., "detail": ..., "models": [...]}``."""

    async def aclose(self) -> None:  # pragma: no cover - optional hook
        return None

    # -- shared helpers ----------------------------------------------------
    @staticmethod
    def _finalize(
        usage: TokenUsage,
        started: datetime,
        *,
        retries: int = 0,
    ) -> TokenUsage:
        usage.request_start_time = started
        usage.request_end_time = utcnow()
        usage.retries = retries
        return usage.finalize()


__all__ = [
    "GenerateOptions",
    "LLMMessage",
    "LLMProvider",
    "LLMResponse",
    "ProviderError",
    "ProviderUnavailable",
    "Role",
]
