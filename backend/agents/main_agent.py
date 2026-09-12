"""MainAgent -- the DeepSeek brain.

Responsibilities (requirement #5.1): understand the request, plan, decompose,
decide whether to delegate, choose the worker, order execution, continue
reasoning over worker results, re-plan when needed, judge completion, and write
the final user-facing answer.

Prompt construction
-------------------
The static prefix (``_static_prefix.md`` + ``main_agent.md``) is passed as the
**system** message and is byte-identical on every call, which is what makes it
cacheable by the provider. Everything that varies -- conversation history, the
task, context and the internal agent state -- goes into the **user** message,
with the agent state always last.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from core.config import Settings
from models.schemas import AgentDecision, AgentVisibleState, Message
from models.token_usage import TokenUsage, utcnow
from orchestration.decision_parser import ParseResult, parse_decision
from orchestration.prompt_loader import PromptRepository
from providers.base import (
    GenerateOptions,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ProviderError,
    ProviderUnavailable,
)

from agents.base import AgentError, BaseAgent

log = logging.getLogger(__name__)

_MAIN_AGENT_KEY = "main"


def is_local_agent_key(key: str) -> bool:
    """True when ``key`` refers to one of the MiniCPM workers."""
    return key.startswith("local_")


@dataclass
class MainAgentTurn:
    """One MainAgent invocation: the decision plus its telemetry and audit data."""

    decision: AgentDecision
    parse: ParseResult
    usage: TokenUsage | None = None
    reasoning: str | None = None
    raw: str = ""
    prompt: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    history_messages: int = 0
    debug: dict = field(default_factory=dict)


class MainAgent(BaseAgent):
    """Planner / router / reviewer / final author."""

    key = _MAIN_AGENT_KEY
    label = "DeepSeek MainAgent"
    role_description = "任务规划 / 委派决策 / 结果校验 / 最终回答"

    # -- prompt ------------------------------------------------------------
    def system_prompt(self) -> str:
        """The cacheable static prefix (static rules + role + protocol)."""
        return self._prompts.main_agent_static_prefix()

    def build_messages(
        self,
        *,
        user_goal: str,
        history: list[Message] | None = None,
        agent_state: AgentVisibleState | None = None,
        extra_instruction: str | None = None,
    ) -> tuple[list[LLMMessage], str]:
        """Build (messages, rendered_user_content).

        Order inside the user message is fixed:
        conversation -> extra instruction -> INTERNAL AGENT STATE (always last).
        """
        sections: list[str] = []

        history = history or []
        if history:
            lines = ["CONVERSATION SO FAR (most recent last)"]
            for message in history:
                label = "User" if message.role == "user" else "Assistant"
                content = (message.content or "").strip()
                if len(content) > 1500:
                    content = content[:1500] + "\n...[truncated]"
                lines.append(f"[{label}] {content}")
            sections.append("\n".join(lines))

        sections.append(f"CURRENT USER REQUEST\n{user_goal.strip()}")

        if extra_instruction:
            sections.append(f"ADDITIONAL INSTRUCTION\n{extra_instruction.strip()}")

        if agent_state is not None:
            from core.prompt_builder import _assert_not_telemetry  # local import: guard is internal

            _assert_not_telemetry(agent_state, "agent_state")
            sections.append(agent_state.render())

        user_content = "\n\n".join(sections)
        return [
            LLMMessage(role="system", content=self.system_prompt()),
            LLMMessage(role="user", content=user_content),
        ], user_content

    # -- decisions ---------------------------------------------------------
    async def decide(
        self,
        *,
        user_goal: str,
        agent_state: AgentVisibleState,
        history: list[Message] | None = None,
        extra_instruction: str | None = None,
        max_tokens: int | None = None,
    ) -> MainAgentTurn:
        """Ask the MainAgent for exactly one decision."""
        messages, user_content = self.build_messages(
            user_goal=user_goal,
            history=history,
            agent_state=agent_state,
            extra_instruction=extra_instruction,
        )
        started = utcnow()
        debug: dict = {"system_chars": len(messages[0].content)}

        try:
            response: LLMResponse = await self._provider.generate(
                messages, self._options(max_tokens=max_tokens)
            )
        except (ProviderUnavailable, ProviderError) as exc:
            finished = utcnow()
            return MainAgentTurn(
                decision=AgentDecision(action="answer", answer=None, reason=str(exc)),
                parse=ParseResult(
                    decision=AgentDecision(action="answer"),
                    ok=False,
                    strategy="provider_error",
                    error=str(exc),
                ),
                usage=TokenUsage(
                    agent_name=self.key,
                    model=self.model,
                    provider=self.provider.name,
                    is_local=False,
                ).finalize(),
                prompt=user_content,
                started_at=started,
                finished_at=finished,
                error=str(exc),
                history_messages=len(history or []),
                debug={**debug, "error_kind": type(exc).__name__},
            )

        finished = utcnow()
        parse = parse_decision(response.content, fallback_answer=None)
        if not parse.ok:
            debug["parse_failure"] = parse.error
            debug["parse_attempts"] = [a.strategy for a in parse.attempts]

        usage = response.usage
        usage.agent_name = self.key
        usage.finalize()

        return MainAgentTurn(
            decision=parse.decision,
            parse=parse,
            usage=usage,
            reasoning=response.reasoning,
            raw=response.content,
            prompt=user_content,
            started_at=started,
            finished_at=finished,
            history_messages=len(history or []),
            debug=debug,
        )

    async def finalize(
        self,
        *,
        user_goal: str,
        agent_state: AgentVisibleState,
        history: list[Message] | None = None,
        extra_instruction: str | None = None,
    ) -> MainAgentTurn:
        """Force a final answer (used when the step budget is exhausted).

        The step budget is runtime state the agent legitimately needs, so it is
        passed through ``AgentVisibleState`` -- never as a telemetry object.
        """
        instruction = extra_instruction or (
            "You must stop delegating now. Using everything gathered above, produce the "
            'final answer for the user by responding with {"action":"answer","answer":"..."}. '
            "If something could not be completed, say so plainly inside the answer."
        )
        return await self.decide(
            user_goal=user_goal,
            agent_state=agent_state,
            history=history,
            extra_instruction=instruction,
        )

    # -- streaming final answer -------------------------------------------
    async def stream_answer(
        self,
        *,
        user_goal: str,
        agent_state: AgentVisibleState,
        history: list[Message] | None = None,
        extra_instruction: str | None = None,
    ):
        """Stream the final answer as plain text.

        Used when the MainAgent already decided ``action="answer"`` and the
        answer should appear token-by-token. Yields ``(content_delta,
        reasoning_delta)``; the caller reads :attr:`last_stream_usage` from the
        provider afterwards.
        """
        messages, user_content = self.build_messages(
            user_goal=user_goal,
            history=history,
            agent_state=agent_state,
            extra_instruction=extra_instruction,
        )
        options: GenerateOptions = self._options(stream=True)
        async for pair in self._provider.stream(messages, options):
            yield pair

    # -- helpers -----------------------------------------------------------
    def worker_prompt_preview(self) -> str:
        """Static prefix size, surfaced in the Settings panel for cache sanity."""
        return self.system_prompt()

    def estimate_static_prefix_chars(self) -> int:
        return len(self.system_prompt())


__all__ = ["MainAgent", "MainAgentTurn", "is_local_agent_key"]
