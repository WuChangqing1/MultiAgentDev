"""DeepSeek provider (OpenAI-compatible chat completions).

This is the MainAgent's brain. It is the only place in the codebase that knows
DeepSeek exists.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from core.config import Settings
from models.token_usage import TokenUsage, utcnow
from providers.base import (
    GenerateOptions,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ProviderError,
    ProviderUnavailable,
)

log = logging.getLogger(__name__)

#: "none" is our sentinel for "do not send any reasoning control at all".
_EFFORT_TO_BUDGET = {"none": None, "low": 512, "medium": 2048, "high": 8192}


@dataclass
class StreamUsageSlot:
    """Mutable holder the caller reads after draining a stream.

    Streaming responses report usage on the final chunk (or not at all); a slot
    avoids changing the generator's element type.
    """

    usage: TokenUsage | None = field(default=None)


def _extract_usage(raw: Any) -> TokenUsage:
    """Map an OpenAI-style ``usage`` object onto TokenUsage.

    Only values the server actually sent are recorded; everything else stays
    ``None`` so the UI can honestly render an em dash.
    """
    usage = TokenUsage()
    if raw is None:
        return usage

    prompt = getattr(raw, "prompt_tokens", None)
    completion = getattr(raw, "completion_tokens", None)
    total = getattr(raw, "total_tokens", None)

    if prompt is not None:
        usage.prompt_tokens = int(prompt)
        usage.prompt_tokens_source = "reported"
    if completion is not None:
        usage.completion_tokens = int(completion)
        usage.completion_tokens_source = "reported"
    if total is not None:
        usage.total_tokens = int(total)

    details = getattr(raw, "completion_tokens_details", None)
    if details is not None:
        reasoning = getattr(details, "reasoning_tokens", None)
        if reasoning is not None:
            usage.reasoning_tokens = int(reasoning)
            usage.reasoning_tokens_source = "reported"

    prompt_details = getattr(raw, "prompt_tokens_details", None)
    if prompt_details is not None:
        cached = getattr(prompt_details, "cached_tokens", None)
        if cached is not None:
            usage.cached_tokens = int(cached)
            usage.cached_tokens_source = "reported"

    return usage


def _extract_text(chunk: Any, *names: str) -> str | None:
    """Read the first non-empty string attribute among ``names``."""
    for name in names:
        value = getattr(chunk, name, None)
        if isinstance(value, str) and value:
            return value
    extra = getattr(chunk, "model_extra", None)
    if isinstance(extra, dict):
        for name in names:
            value = extra.get(name)
            if isinstance(value, str) and value:
                return value
    return None


class DeepSeekProvider(LLMProvider):
    """Main-agent provider."""

    name = "deepseek"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.is_local = False
        self._model = settings.deepseek_model
        self._client: AsyncOpenAI | None = None
        self._client_signature: tuple[str, str, float] | None = None
        self.last_stream_usage = StreamUsageSlot()

    # -- lifecycle ---------------------------------------------------------
    @property
    def model(self) -> str:
        return self._model

    def set_model(self, model: str) -> None:
        if model and model != self._model:
            self._model = model

    @property
    def configured(self) -> bool:
        return self._settings.deepseek_configured

    def refresh(self) -> None:
        """Rebuild the client if endpoint/credentials/timeout changed.

        ``Settings`` is mutated in place by the Settings panel; without this the
        provider would keep using a client built for the old configuration.
        """
        signature = (
            self._settings.deepseek_base_url,
            self._settings.deepseek_api_key,
            self._settings.deepseek_timeout_s,
        )
        if self._client_signature is not None and self._client_signature != signature:
            self._client = None
            self._client_signature = None

    def _get_client(self) -> AsyncOpenAI:
        """Reuse a client keyed on (base_url, api_key, timeout).

        A Settings change therefore takes effect immediately without leaking
        connections; the superseded client is closed asynchronously.
        """
        signature = (self._settings.deepseek_base_url, self._settings.deepseek_api_key, self._settings.deepseek_timeout_s)
        if self._client is not None and self._client_signature == signature:
            return self._client

        old = self._client
        self._client = AsyncOpenAI(
            base_url=self._settings.deepseek_base_url,
            api_key=self._settings.deepseek_api_key or "missing",
            timeout=httpx.Timeout(self._settings.deepseek_timeout_s, connect=15.0),
            max_retries=0,  # bounded retry is implemented here
        )
        self._client_signature = signature
        if old is not None:
            _fire_and_forget(old.close())
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None
            self._client_signature = None

    # -- option translation ------------------------------------------------
    def _build_kwargs(
        self,
        messages: list[LLMMessage],
        options: GenerateOptions,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        settings = self._settings
        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": options.max_tokens or settings.main_agent_max_tokens,
            "temperature": (
                options.temperature if options.temperature is not None else settings.main_agent_temperature
            ),
        }
        if options.top_p is not None:
            kwargs["top_p"] = options.top_p
        if options.stop:
            kwargs["stop"] = options.stop
        if stream:
            # Ask for usage on the final chunk. Servers that do not support
            # this key simply omit usage; nothing breaks.
            kwargs["stream_options"] = {"include_usage": True}

        # Reasoning control is opt-in and only sent to reasoner-style models,
        # so an unsupported parameter can never fail a normal request.
        budget = _EFFORT_TO_BUDGET.get(options.reasoning_effort or "none")
        if budget and "reasoner" in self._model.lower():
            kwargs["reasoning_effort"] = _budget_to_effort(budget)
        return kwargs

    async def _call_with_retry(self, coro_factory):
        """Bounded exponential-backoff retry; never loops forever."""
        attempts = max(0, self._settings.deepseek_max_retries)
        last_error: Exception | None = None

        for attempt in range(attempts + 1):
            try:
                return await coro_factory()
            except APIStatusError as exc:
                if exc.status_code < 500 and exc.status_code != 429:
                    raise ProviderError(
                        f"DeepSeek rejected the request ({exc.status_code}): {exc.message}",
                        provider=self.name,
                        status=exc.status_code,
                    ) from exc
                last_error = exc
            except (RateLimitError, APITimeoutError, APIConnectionError) as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - normalize anything else
                raise ProviderError(f"DeepSeek call failed: {exc}", provider=self.name) from exc

            if attempt < attempts:
                await asyncio.sleep(min(2**attempt * 0.6, 6.0))

        if isinstance(last_error, (APIConnectionError, APITimeoutError)):
            raise ProviderUnavailable(
                "Could not reach DeepSeek. Check the network and DEEPSEEK_BASE_URL in .env.",
                provider=self.name,
            ) from last_error
        raise ProviderError(f"DeepSeek call failed: {last_error}", provider=self.name) from last_error

    def _finalize_usage(self, usage: TokenUsage, started) -> TokenUsage:
        usage.model = self._model
        usage.provider = self.name
        usage.is_local = False
        usage.cost_usd = _estimate_cost(self._settings, usage)
        return self._finalize(usage, started)

    # -- generate ----------------------------------------------------------
    async def generate(
        self,
        messages: list[LLMMessage],
        options: GenerateOptions | None = None,
    ) -> LLMResponse:
        options = options or GenerateOptions()
        if not self.configured:
            raise ProviderError(
                "DeepSeek API key is not configured. Set DEEPSEEK_API_KEY in .env.",
                provider=self.name,
                status=401,
            )

        started = utcnow()
        client = self._get_client()
        kwargs = self._build_kwargs(messages, options, stream=False)
        response = await self._call_with_retry(lambda: client.chat.completions.create(**kwargs))

        choice = response.choices[0] if getattr(response, "choices", None) else None
        message = getattr(choice, "message", None)

        usage = self._finalize_usage(_extract_usage(getattr(response, "usage", None)), started)

        return LLMResponse(
            content=(_extract_text(message, "content") or "").strip(),
            reasoning=_extract_text(message, "reasoning_content", "reasoning"),
            model=self._model,
            provider=self.name,
            is_local=False,
            usage=usage,
            finish_reason=getattr(choice, "finish_reason", None),
        )

    # -- stream ------------------------------------------------------------
    async def stream(
        self,
        messages: list[LLMMessage],
        options: GenerateOptions | None = None,
    ) -> AsyncIterator[tuple[str, str | None]]:
        options = options or GenerateOptions()
        if not self.configured:
            raise ProviderError(
                "DeepSeek API key is not configured. Set DEEPSEEK_API_KEY in .env.",
                provider=self.name,
                status=401,
            )

        started = utcnow()
        self.last_stream_usage = StreamUsageSlot()
        client = self._get_client()
        kwargs = self._build_kwargs(messages, options, stream=True)
        stream = await self._call_with_retry(lambda: client.chat.completions.create(**kwargs))

        final_usage: TokenUsage | None = None
        try:
            async for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    final_usage = _extract_usage(chunk_usage)
                choices = getattr(chunk, "choices", None)
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue
                reasoning_delta = _extract_text(delta, "reasoning_content", "reasoning")
                content_delta = _extract_text(delta, "content")
                if content_delta or reasoning_delta:
                    yield (content_delta or "", reasoning_delta)
        finally:
            self.last_stream_usage = StreamUsageSlot(
                usage=self._finalize_usage(final_usage or TokenUsage(), started)
            )

    # -- health ------------------------------------------------------------
    async def health_check(self) -> dict[str, Any]:
        if not self.configured:
            return {"status": "unknown", "detail": "DEEPSEEK_API_KEY is not set in .env", "models": []}
        started = utcnow()
        try:
            client = self._get_client()
            models = await client.models.list()
            elapsed = (utcnow() - started).total_seconds() * 1000
            ids = [m.id for m in getattr(models, "data", [])]
            return {
                "status": "online",
                "detail": f"{len(ids)} model(s) reachable",
                "latency_ms": round(elapsed, 2),
                "models": ids,
            }
        except Exception as exc:  # health checks must never raise
            return {"status": "error", "detail": _short(exc), "models": []}


def _budget_to_effort(budget: int) -> str:
    return "low" if budget <= 512 else "medium" if budget <= 2048 else "high"


def _estimate_cost(settings: Settings, usage: TokenUsage) -> float | None:
    """Computed only when pricing is explicitly configured, else ``None``.

    Prices are never hardcoded: a stale constant would silently mislead.
    """
    if usage.prompt_tokens is None and usage.completion_tokens is None:
        return None
    in_price = settings.deepseek_price_input
    out_price = settings.deepseek_price_output
    if in_price is None and out_price is None:
        return None
    cost = 0.0
    if usage.prompt_tokens and in_price is not None:
        cost += usage.prompt_tokens / 1_000_000 * in_price
    if usage.completion_tokens and out_price is not None:
        cost += usage.completion_tokens / 1_000_000 * out_price
    return round(cost, 8)


def _short(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) <= 300 else text[:300] + "..."


def _fire_and_forget(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(coro)


__all__ = ["DeepSeekProvider", "StreamUsageSlot"]
