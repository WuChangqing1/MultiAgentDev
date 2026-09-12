"""Token accounting primitives.

This module is pure telemetry. Nothing defined here may ever be injected into
an agent prompt -- see :mod:`orchestration.prompt_builder` for the one and only
sanctioned prompt assembly path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

# How a token figure was obtained.
#   reported  -> the model API returned it verbatim (trustworthy)
#   estimated -> we derived it locally; MUST be surfaced as "estimated" in the UI
#   unavailable -> not obtainable
TokenSource = Literal["reported", "estimated", "unavailable"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TokenUsage(BaseModel):
    """One model call's token + latency accounting.

    Every numeric field is optional on purpose: when an API does not report a
    value we store ``None`` rather than inventing a number.
    """

    agent_name: str | None = None
    model: str | None = None
    provider: str | None = None
    is_local: bool = False

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None

    # Provenance for each figure so the UI can render "estimated" badges.
    prompt_tokens_source: TokenSource = "unavailable"
    completion_tokens_source: TokenSource = "unavailable"
    reasoning_tokens_source: TokenSource = "unavailable"
    cached_tokens_source: TokenSource = "unavailable"

    request_start_time: datetime | None = None
    request_end_time: datetime | None = None
    latency_ms: float | None = None
    tokens_per_second: float | None = None

    # DeepSeek only; ``None`` for local models and when pricing is unconfigured.
    cost_usd: float | None = None

    retries: int = 0

    # -- derivation --------------------------------------------------------
    def finalize(self) -> TokenUsage:
        """Fill in derived fields that can be computed without guessing.

        ``total_tokens`` is only derived when both halves are known; we never
        fabricate a total from a partial observation.
        """
        if self.total_tokens is None and self.prompt_tokens is not None and self.completion_tokens is not None:
            self.total_tokens = self.prompt_tokens + self.completion_tokens

        if self.request_start_time and self.request_end_time:
            delta = (self.request_end_time - self.request_start_time).total_seconds()
            if self.latency_ms is None:
                self.latency_ms = round(delta * 1000, 2)

        if (
            self.tokens_per_second is None
            and self.completion_tokens is not None
            and self.latency_ms
            and self.latency_ms > 0
        ):
            self.tokens_per_second = round(self.completion_tokens / (self.latency_ms / 1000.0), 2)

        return self

    @property
    def reasoning_is_estimated(self) -> bool:
        return self.reasoning_tokens_source == "estimated"


class TokenUsageAggregate(BaseModel):
    """Sum over many :class:`TokenUsage` records.

    ``calls`` counts real model invocations; ``None`` fields stay ``None`` when
    no constituent call reported the value.
    """

    calls: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    reasoning_tokens: int | None = None
    cached_tokens: int | None = None
    latency_ms: float | None = None
    tokens_per_second: float | None = None
    cost_usd: float | None = None
    any_estimated: bool = False

    @classmethod
    def from_usages(cls, usages: list[TokenUsage]) -> TokenUsageAggregate:
        if not usages:
            return cls()

        def _sum(field: str) -> int | None:
            vals = [getattr(u, field) for u in usages if getattr(u, field) is not None]
            return sum(vals) if vals else None

        latencies = [u.latency_ms for u in usages if u.latency_ms is not None]
        costs = [u.cost_usd for u in usages if u.cost_usd is not None]
        total_completion = _sum("completion_tokens")

        return cls(
            calls=len(usages),
            prompt_tokens=_sum("prompt_tokens"),
            completion_tokens=total_completion,
            total_tokens=_sum("total_tokens"),
            reasoning_tokens=_sum("reasoning_tokens"),
            cached_tokens=_sum("cached_tokens"),
            latency_ms=round(sum(latencies), 2) if latencies else None,
            tokens_per_second=(
                round(total_completion / (sum(latencies) / 1000.0), 2)
                if total_completion is not None and latencies and sum(latencies) > 0
                else None
            ),
            cost_usd=round(sum(costs), 8) if costs else None,
            any_estimated=any(
                u.reasoning_tokens_source == "estimated" or u.prompt_tokens_source == "estimated" for u in usages
            ),
        )


class ModelCallRecord(BaseModel):
    """Telemetry row persisted for every provider invocation."""

    id: int | None = None
    execution_id: str | None = None
    agent_step_id: int | None = None
    agent_name: str
    model: str
    provider: str
    is_local: bool = False
    usage: TokenUsage = Field(default_factory=TokenUsage)


__all__ = [
    "ModelCallRecord",
    "TokenSource",
    "TokenUsage",
    "TokenUsageAggregate",
    "utcnow",
]
