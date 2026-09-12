"""Agent base classes.

An agent owns identity (key, label, role) and a provider binding. It does not
own orchestration state, does not write to the database, and does not emit
events -- the orchestrator does all of that. This keeps agents trivially
testable and keeps telemetry out of the agent layer by construction.

Prompt rule (enforced in :mod:`core.prompt_builder`): every prompt is
``system -> TASK -> CONTEXT -> INTERNAL AGENT STATE``.
"""

from __future__ import annotations

import abc
import json
import logging
from datetime import datetime

from core.config import Settings
from core.prompt_builder import TelemetryLeakError, build_agent_prompt, build_worker_prompt
from models.schemas import AgentResult, AgentTask, AgentVisibleState
from models.token_usage import TokenUsage, utcnow
from orchestration.prompt_loader import PromptRepository
from providers.base import (
    GenerateOptions,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ProviderError,
    ProviderUnavailable,
)

log = logging.getLogger(__name__)


class AgentError(RuntimeError):
    """Agent-level failure already normalized for the orchestrator."""

    def __init__(self, message: str, *, agent: str = "", recoverable: bool = True) -> None:
        super().__init__(message)
        self.agent = agent
        self.recoverable = recoverable


class BaseAgent(abc.ABC):
    """Common identity + provider binding."""

    #: stable key used in decisions, DB rows and the UI
    key: str = "agent"
    #: human label for the UI
    label: str = "Agent"
    #: one-line role shown in the Agent panel (user-visible, never prompted)
    role_description: str = ""

    def __init__(self, settings: Settings, provider: LLMProvider, prompts: PromptRepository) -> None:
        self._settings = settings
        self._provider = provider
        self._prompts = prompts

    # -- accessors ---------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def provider(self) -> LLMProvider:
        return self._provider

    @property
    def prompts(self) -> PromptRepository:
        return self._prompts

    @property
    def model(self) -> str:
        return self._provider.model

    @property
    def is_local(self) -> bool:
        return self._provider.is_local

    @property
    def reasoning_effort(self) -> str:
        return self._settings.reasoning_for(self.key)

    def update_settings(self, settings: Settings) -> None:
        """Adopt new settings (used when the Settings panel changes values)."""
        self._settings = settings

    @abc.abstractmethod
    def system_prompt(self) -> str:
        """Static instructions for this agent."""

    async def run(self, task: AgentTask, state: AgentVisibleState) -> AgentResult:
        """Execute one unit of work.

        Concrete for agents whose interface is richer than a single task (the
        MainAgent exposes :meth:`~agents.main_agent.MainAgent.decide` instead),
        so the orchestrator can hold every agent behind one registry type.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement run()")

    # -- shared plumbing ---------------------------------------------------
    def _build_prompt(
        self,
        *,
        task: str,
        context: str | None,
        state: AgentVisibleState | None,
        worker: bool,
        context_limit: int = 6000,
    ) -> str:
        try:
            if worker:
                return build_worker_prompt(
                    self.system_prompt(),
                    task=task,
                    context=context,
                    agent_state=state,
                    context_limit=context_limit,
                )
            return build_agent_prompt(
                self.system_prompt(),
                task=task,
                context=context,
                agent_state=state,
            )
        except TelemetryLeakError:
            # A programming error, not a runtime condition: fail loudly so it is
            # caught in tests rather than silently leaking into a prompt.
            log.error("telemetry_leak_blocked agent=%s", self.key, exc_info=True)
            raise

    @staticmethod
    def _messages(prompt: str) -> list[LLMMessage]:
        return [LLMMessage(role="user", content=prompt)]

    def _options(self, *, max_tokens: int | None = None, stream: bool = False) -> GenerateOptions:
        settings = self._settings
        if self.is_local:
            return GenerateOptions(
                max_tokens=max_tokens or settings.worker_max_tokens,
                temperature=settings.worker_temperature,
                top_p=settings.worker_top_p,
                reasoning_effort=self.reasoning_effort,
                stream=stream,
            )
        return GenerateOptions(
            max_tokens=max_tokens or settings.main_agent_max_tokens,
            temperature=settings.main_agent_temperature,
            reasoning_effort=self.reasoning_effort,
            stream=stream,
        )

    async def _complete(
        self,
        prompt: str,
        *,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Single provider call with normalized error translation."""
        try:
            response = await self._provider.generate(self._messages(prompt), self._options(max_tokens=max_tokens))
        except ProviderUnavailable as exc:
            raise AgentError(str(exc), agent=self.key, recoverable=True) from exc
        except ProviderError as exc:
            raise AgentError(str(exc), agent=self.key, recoverable=True) from exc
        except Exception as exc:  # noqa: BLE001
            raise AgentError(f"{self.label} failed: {exc}", agent=self.key, recoverable=True) from exc
        return response


class WorkerAgent(BaseAgent):
    """Base for the MiniCPM workers.

    Workers share one HTTP endpoint; a worker subclass contributes a prompt
    file, a reasoning policy and an output contract. None of them load a model.
    """

    #: prompt file stem under ``backend/prompts/``
    prompt_name: str = ""
    #: what the worker is expected to return, used for downstream parsing
    output_kind: str = "text"

    def system_prompt(self) -> str:
        return self._prompts.worker_system_prompt(self.prompt_name or self.key)

    async def run(self, task: AgentTask, state: AgentVisibleState) -> AgentResult:
        started: datetime = utcnow()
        prompt = self._build_prompt(
            task=task.instruction,
            context=task.context or None,
            state=state,
            worker=True,
        )

        try:
            response = await self._complete(prompt)
            # A local model with a tight reasoning budget can spend the whole
            # budget thinking and emit no answer at all. That is worth exactly
            # one retry with a larger budget -- it is a real and common failure
            # mode, not a hypothetical one.
            if not response.content.strip() and response.reasoning:
                retry_budget = max(2 * (self.settings.worker_max_tokens), 2048)
                log.info(
                    "worker_retry_budget_exhausted agent=%s retry_max_tokens=%d",
                    self.key,
                    retry_budget,
                )
                retry = await self._complete(prompt, max_tokens=retry_budget)
                _accumulate(retry.usage, response.usage)
                response = retry
        except AgentError as exc:
            return AgentResult(
                task_id=task.task_id,
                agent=self.key,
                status="failed",
                error=str(exc),
                started_at=started,
                finished_at=utcnow(),
            )

        parsed = None
        if self.output_kind == "json":
            parsed = try_parse_json_object(response.content)

        output = response.content
        if not output.strip() and response.reasoning:
            # Still no content after the retry: hand the MainAgent the reasoning
            # rather than an empty string, clearly marked as not-an-answer.
            output = (
                "[The local model produced reasoning but no final answer. "
                f"Unverified reasoning follows]\n{response.reasoning.strip()}"
            )

        return AgentResult(
            task_id=task.task_id,
            agent=self.key,
            status="completed",
            output=output,
            parsed=parsed,
            reasoning=response.reasoning,
            usage=response.usage,
            started_at=started,
            finished_at=utcnow(),
        )


def _accumulate(target: TokenUsage, extra: TokenUsage) -> TokenUsage:
    """Fold ``extra`` into ``target`` so a retried call is fully accounted for.

    Token accounting must reflect every request actually made; otherwise a
    retried worker would look cheaper than it was.
    """
    for field in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "cached_tokens"):
        left = getattr(target, field)
        right = getattr(extra, field)
        if left is not None and right is not None:
            setattr(target, field, left + right)
        elif left is None and right is not None:
            setattr(target, field, right)
            setattr(target, f"{field}_source", getattr(extra, f"{field}_source"))

    if target.total_tokens is not None and extra.total_tokens is not None:
        target.total_tokens += extra.total_tokens
    elif target.total_tokens is None and extra.total_tokens is not None:
        target.total_tokens = extra.total_tokens

    if target.latency_ms is not None and extra.latency_ms is not None:
        target.latency_ms = round(target.latency_ms + extra.latency_ms, 2)
    if target.cost_usd is not None and extra.cost_usd is not None:
        target.cost_usd = round(target.cost_usd + extra.cost_usd, 8)

    target.retries += 1
    return target.finalize()


def try_parse_json_object(text: str) -> dict | None:
    """Best-effort JSON object extraction for worker output.

    Workers are asked for bare JSON but small models wrap it in prose or a code
    fence. A parse failure is not an error here: the raw text is still returned
    to the MainAgent, which can read it.
    """
    if not text or not text.strip():
        return None

    from orchestration.decision_parser import extract_balanced_object, repair_json, strip_code_fence

    candidate = strip_code_fence(text) or text
    for attempt in (candidate.strip(), extract_balanced_object(candidate), None):
        if attempt is None:
            break
        try:
            value = json.loads(attempt)
        except (json.JSONDecodeError, TypeError):
            try:
                value = json.loads(repair_json(attempt))
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(value, dict):
            return value
        if isinstance(value, list):
            return {"items": value}
    return None


def failure_result(task: AgentTask, agent: str, error: str, started: datetime) -> AgentResult:
    """Helper for building a failed result consistently."""
    return AgentResult(
        task_id=task.task_id,
        agent=agent,
        status="failed",
        error=error,
        started_at=started,
        finished_at=utcnow(),
    )


def zero_usage(agent: str, model: str, *, is_local: bool, provider: str) -> TokenUsage:
    """Usage placeholder for calls that never reached the model."""
    return TokenUsage(
        agent_name=agent,
        model=model,
        provider=provider,
        is_local=is_local,
    ).finalize()


__all__ = [
    "AgentError",
    "BaseAgent",
    "WorkerAgent",
    "failure_result",
    "try_parse_json_object",
    "zero_usage",
]
