"""Local MiniCPM5-2B provider (llama.cpp ``llama-server``).

ONE provider instance serves every local worker. The four worker agents
(extractor / summarizer / classifier / reviewer) differ only in system prompt,
task and reasoning policy -- they are not four loaded models.

Compatibility notes for llama.cpp:

* ``reasoning_budget`` is a ``chat_template_kwargs`` entry that current
  llama.cpp builds understand. If a build rejects unknown template kwargs we
  retry once WITHOUT them instead of failing the call (requirement: an
  unsupported reasoning parameter must never break a request).
* ``reasoning_content`` is parsed alongside ``content``; the two are never
  concatenated.
* ``usage`` may be absent -> fields stay ``None``. When the server omits
  ``completion_tokens`` but returned text, we count it locally and label the
  figure ``estimated`` so it is never mistaken for an API-reported value.
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

#: reasoning_budget (in "thinking tokens") per effort level. ``None`` -> omit.
#: ``low`` is 512 rather than a token-sipping 256 because MiniCPM5-2B reliably
#: exhausts a very small budget on thinking and then emits no content at all --
#: an answer that is slightly more expensive beats an empty one.
_EFFORT_TO_BUDGET: dict[str, int | None] = {
    "none": 0,
    "low": 512,
    "medium": 1024,
    "high": 4096,
}


@dataclass
class StreamUsageSlot:
    usage: TokenUsage | None = field(default=None)


def _extract_usage(raw: Any) -> TokenUsage:
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


def estimate_reasoning_tokens(reasoning: str | None) -> int | None:
    """Rough token count for reasoning text.

    llama.cpp does not report ``reasoning_tokens``. Rather than silently
    presenting a guess as an API value, callers MUST mark the result
    ``estimated``. The heuristic is deliberately crude (it is a label, not a
    measurement): CJK characters ~1 token each, other text ~1 token / 4 chars.
    """
    if not reasoning or not reasoning.strip():
        return None
    cjk = sum(1 for ch in reasoning if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff")
    other = len(reasoning) - cjk
    return int(cjk + other / 4)


class LlamaCppProvider(LLMProvider):
    """OpenAI-compatible client for a local ``llama-server``."""

    name = "llamacpp"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.is_local = True
        self.last_stream_usage = StreamUsageSlot()
        self._client: AsyncOpenAI | None = None
        self._client_signature: tuple[str, str, float] | None = None
        #: Set to True after the server rejects chat_template_kwargs, so we stop
        #: sending them for the rest of the process lifetime.
        self._template_kwargs_supported = True

    # -- lifecycle ---------------------------------------------------------
    @property
    def model(self) -> str:
        return self._settings.local_model_name

    @property
    def base_url(self) -> str:
        return self._settings.local_model_base_url

    def refresh(self) -> None:
        """Re-read settings that affect the HTTP client.

        ``Settings`` is mutated in place by the Settings panel, so the provider
        has to rebuild its client when the endpoint, key or timeout changes --
        otherwise a live change would keep talking to the old address.
        """
        signature = (
            self._settings.local_model_base_url,
            self._settings.local_model_api_key,
            self._settings.local_model_timeout_s,
        )
        if self._client_signature is not None and self._client_signature != signature:
            self._client = None
            self._client_signature = None
            # A previously rejected template-kwargs flag may not apply to the
            # new endpoint, so give it another chance.
            self._template_kwargs_supported = True
            log.info("local_provider_refreshed base_url=%s", self._settings.local_model_base_url)

    def _get_client(self) -> AsyncOpenAI:
        signature = (
            self._settings.local_model_base_url,
            self._settings.local_model_api_key,
            self._settings.local_model_timeout_s,
        )
        if self._client is not None and self._client_signature == signature:
            return self._client

        old = self._client
        self._client = AsyncOpenAI(
            base_url=self._settings.local_model_base_url,
            api_key=self._settings.local_model_api_key or "local",
            timeout=httpx.Timeout(self._settings.local_model_timeout_s, connect=5.0),
            max_retries=0,
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
        with_template_kwargs: bool,
    ) -> dict[str, Any]:
        settings = self._settings
        kwargs: dict[str, Any] = {
            "model": settings.local_model_name,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": options.max_tokens or settings.worker_max_tokens,
            "temperature": (
                options.temperature if options.temperature is not None else settings.worker_temperature
            ),
            "top_p": options.top_p if options.top_p is not None else settings.worker_top_p,
        }
        if options.stop:
            kwargs["stop"] = options.stop
        if stream:
            kwargs["stream_options"] = {"include_usage": True}

        if with_template_kwargs and self._template_kwargs_supported:
            budget = _EFFORT_TO_BUDGET.get(options.reasoning_effort or "none")
            if budget is not None:
                # ``chat_template_kwargs`` is a llama.cpp extension, not part of
                # the OpenAI schema, so it must travel in ``extra_body`` -- the
                # SDK validates top-level kwargs and would reject it outright.
                # enable_thinking=False fully suppresses the local model's
                # thinking phase for extraction/classification tasks.
                extras = dict(options.extras)
                extras["chat_template_kwargs"] = {
                    "enable_thinking": budget > 0,
                    "reasoning_budget": budget,
                }
                kwargs["extra_body"] = extras
        return kwargs

    async def _call_with_retry(self, kwargs_factory):
        """Retry boundedly, dropping unsupported template kwargs when needed."""
        attempts = max(0, self._settings.local_model_max_retries)
        last_error: Exception | None = None

        for attempt in range(attempts + 1):
            kwargs = kwargs_factory()
            try:
                return await self._get_client().chat.completions.create(**kwargs)
            except TypeError as exc:
                # A stricter SDK build refusing a llama.cpp-only extension.
                if "extra_body" in kwargs and "chat_template_kwargs" in str(exc):
                    log.warning("SDK rejected chat_template_kwargs; disabling reasoning controls")
                    self._template_kwargs_supported = False
                    continue
                raise ProviderError(f"Local model call failed: {exc}", provider=self.name) from exc
            except APIStatusError as exc:
                if _is_template_kwarg_rejection(exc) and "extra_body" in kwargs:
                    log.warning(
                        "llama.cpp rejected chat_template_kwargs; disabling reasoning controls",
                        extra={"status": exc.status_code},
                    )
                    self._template_kwargs_supported = False
                    continue  # identical retry minus the unsupported parameter
                if exc.status_code < 500 and exc.status_code != 429:
                    raise ProviderError(
                        f"Local model rejected the request ({exc.status_code}): {exc.message}",
                        provider=self.name,
                        status=exc.status_code,
                    ) from exc
                last_error = exc
            except APIConnectionError as exc:
                # Connection refused == llama-server not running.
                last_error = ProviderUnavailable(
                    f"Local model is offline at {self.base_url}.", provider=self.name
                )
                last_error.__cause__ = exc
            except (APITimeoutError, RateLimitError) as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001
                raise ProviderError(f"Local model call failed: {exc}", provider=self.name) from exc

            if attempt < attempts:
                await asyncio.sleep(min(2**attempt * 0.4, 3.0))

        if isinstance(last_error, ProviderUnavailable):
            raise last_error
        if isinstance(last_error, APITimeoutError):
            raise ProviderError(
                f"Local model timed out after {self._settings.local_model_timeout_s:.0f}s.",
                provider=self.name,
            ) from last_error
        raise ProviderError(f"Local model call failed: {last_error}", provider=self.name) from last_error

    def _finalize_usage(self, usage: TokenUsage, started, reasoning_text: str | None) -> TokenUsage:
        usage.model = self.model
        usage.provider = self.name
        usage.is_local = True
        # Local inference is free; cost is intentionally None (UI shows "Local").
        if usage.reasoning_tokens is None:
            estimated = estimate_reasoning_tokens(reasoning_text)
            if estimated is not None:
                usage.reasoning_tokens = estimated
                usage.reasoning_tokens_source = "estimated"
        return self._finalize(usage, started)

    # -- generate ----------------------------------------------------------
    async def generate(
        self,
        messages: list[LLMMessage],
        options: GenerateOptions | None = None,
    ) -> LLMResponse:
        options = options or GenerateOptions()
        started = utcnow()

        response = await self._call_with_retry(
            lambda: self._build_kwargs(messages, options, stream=False, with_template_kwargs=True)
        )

        choice = response.choices[0] if getattr(response, "choices", None) else None
        message = getattr(choice, "message", None)
        content = (_extract_text(message, "content") or "").strip()
        reasoning = _extract_text(message, "reasoning_content", "reasoning")

        usage = self._finalize_usage(_extract_usage(getattr(response, "usage", None)), started, reasoning)

        # llama.cpp sometimes omits completion_tokens; count locally and LABEL it.
        if usage.completion_tokens is None and content:
            usage.completion_tokens = _estimate_tokens(content)
            usage.completion_tokens_source = "estimated"
            usage.finalize()

        return LLMResponse(
            content=content,
            reasoning=reasoning,
            model=self.model,
            provider=self.name,
            is_local=True,
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
        started = utcnow()
        self.last_stream_usage = StreamUsageSlot()

        stream = await self._call_with_retry(
            lambda: self._build_kwargs(messages, options, stream=True, with_template_kwargs=True)
        )

        final_usage: TokenUsage | None = None
        reasoning_buffer: list[str] = []
        content_buffer: list[str] = []
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
                if reasoning_delta:
                    reasoning_buffer.append(reasoning_delta)
                if content_delta:
                    content_buffer.append(content_delta)
                if content_delta or reasoning_delta:
                    yield (content_delta or "", reasoning_delta)
        finally:
            usage = final_usage or TokenUsage()
            if usage.completion_tokens is None and content_buffer:
                usage.completion_tokens = _estimate_tokens("".join(content_buffer))
                usage.completion_tokens_source = "estimated"
            self.last_stream_usage = StreamUsageSlot(
                usage=self._finalize_usage(usage, started, "".join(reasoning_buffer) or None)
            )

    # -- health ------------------------------------------------------------
    async def health_check(self) -> dict[str, Any]:
        started = utcnow()
        url = f"{self.base_url}/models"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                response = await client.get(url, headers={"Authorization": f"Bearer {self._settings.local_model_api_key}"})
                response.raise_for_status()
                payload = response.json()
            elapsed = (utcnow() - started).total_seconds() * 1000
            ids = [m.get("id") for m in payload.get("data", []) if isinstance(m, dict)]
            if not ids:
                return {"status": "error", "detail": "Endpoint reachable but no model is loaded", "models": []}
            return {
                "status": "online",
                "detail": f"serving {', '.join(ids)}",
                "latency_ms": round(elapsed, 2),
                "models": ids,
            }
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return {
                "status": "offline",
                "detail": f"No llama-server at {self.base_url}",
                "models": [],
            }
        except Exception as exc:  # health checks must never raise
            return {"status": "offline", "detail": _short(exc), "models": []}


def _is_template_kwarg_rejection(exc: APIStatusError) -> bool:
    text = f"{exc.message or ''} {getattr(exc, 'body', '')}".lower()
    return exc.status_code in (400, 422) and any(
        token in text for token in ("chat_template_kwargs", "reasoning_budget", "enable_thinking", "unknown")
    )


def _estimate_tokens(text: str) -> int:
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff" or "\u3040" <= ch <= "\u30ff")
    return max(1, int(cjk + (len(text) - cjk) / 4))


def _short(exc: Exception) -> str:
    text = str(exc)
    return text if len(text) <= 300 else text[:300] + "..."


def _fire_and_forget(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(coro)


__all__ = ["LlamaCppProvider", "StreamUsageSlot", "estimate_reasoning_tokens"]
