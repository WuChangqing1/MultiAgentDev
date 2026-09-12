"""Agent-visible state management.

Owns the *flow* state of one execution and is the single producer of
:class:`AgentVisibleState`. Because that is the only state type the prompt
builder accepts, "what reaches the model" has exactly one author.

Deliberately absent from this module: tokens, latency, cost, timestamps,
execution ids. Those live in :mod:`services.runtime_state` (UI) and
:mod:`services.token_tracker` (telemetry).
"""

from __future__ import annotations

import logging

from models.schemas import AgentResult, AgentVisibleState

log = logging.getLogger(__name__)

#: Cap on how much of any single worker result is echoed into agent state.
#: Keeps the prompt tail bounded even when a worker rambles.
MAX_RESULT_CHARS = 1800
MAX_COMPLETED_STEPS = 14
MAX_ERRORS = 5
#: Distinct worker results kept in agent state. Without a cap a long delegation
#: chain would grow the prompt tail on every turn.
MAX_RESULTS = 6


class ExecutionStateManager:
    """Accumulates one execution's flow state and renders agent-visible views."""

    def __init__(
        self,
        execution_id: str,
        user_goal: str,
        *,
        max_steps: int,
        available_agents: list[str],
        history_digest: str = "",
    ) -> None:
        self.execution_id = execution_id
        self.user_goal = user_goal
        self.max_steps = max_steps
        #: Hard budget for *planning* steps. ``step_index`` starts equal to it and
        #: is only exceeded by the finalisation ladder, which is allowed to run
        #: past the budget so a run always ends with an answer.
        self.planning_budget = max_steps
        self.available_agents = list(available_agents)
        self.current_stage = "planning"
        self.step_index = 0
        self._completed: list[str] = []
        self._results: dict[str, str] = {}
        self._errors: list[str] = []
        self._next_hint: str | None = None
        self._local_available = True
        self._history_digest = history_digest.strip()

    # -- updates -----------------------------------------------------------
    def set_stage(self, stage: str) -> None:
        self.current_stage = stage

    def set_local_available(self, available: bool) -> None:
        self._local_available = available

    def set_hint(self, hint: str | None) -> None:
        self._next_hint = (hint or "").strip() or None

    def reserve_index(self) -> int:
        """Claim the next timeline index.

        Called exactly once per timeline entry (MainAgent turn or worker turn).
        Using a single reservation point keeps step numbering correct and makes
        the ``max_steps`` budget exact rather than approximate. The finalisation
        ladder may reserve indices beyond the planning budget on purpose; it
        cannot delegate, so it always terminates.
        """
        self.step_index += 1
        return self.step_index

    @property
    def planning_exhausted(self) -> bool:
        return self.step_index >= self.planning_budget

    def record_result(self, result: AgentResult, *, label: str) -> None:
        """Fold a worker/main result into the agent-visible state.

        Bounded on purpose: a long run must not grow the prompt without limit,
        so the store is capped and the oldest entries collapse into a counter.
        """
        label = label or result.agent
        line = f"{label}: {_one_line(result.output)}"
        if result.status != "completed":
            self.record_error(f"{label} failed: {result.error or 'unknown error'}")
        self._completed.append(line)
        if len(self._completed) > MAX_COMPLETED_STEPS:
            self._completed = self._completed[-MAX_COMPLETED_STEPS:]

        if result.status == "completed" and result.output:
            self._results[label] = _truncate(result.output)
        elif result.error:
            self._results[label] = f"(failed) {_one_line(result.error)}"

        self._enforce_result_budget()

    def _enforce_result_budget(self) -> None:
        if len(self._results) <= MAX_RESULTS:
            return
        keep = list(self._results)[-MAX_RESULTS:]
        dropped = len(self._results) - len(keep)
        trimmed = {k: self._results[k] for k in keep}
        trimmed["earlier_results"] = (
            f"({dropped} older worker result(s) omitted to keep the prompt small)"
        )
        self._results = trimmed

    def record_error(self, error: str) -> None:
        text = _one_line(error)
        if not text:
            return
        self._errors.append(text)
        if len(self._errors) > MAX_ERRORS:
            self._errors = self._errors[-MAX_ERRORS:]

    def note(self, step_label: str) -> None:
        """Record a step that produced no worker result (e.g. the agent itself)."""
        self._completed.append(step_label)
        if len(self._completed) > MAX_COMPLETED_STEPS:
            self._completed = self._completed[-MAX_COMPLETED_STEPS:]

    # -- rendering ---------------------------------------------------------
    def visible_state(self) -> AgentVisibleState:
        """Build the object that may be injected at the prompt tail."""
        important = dict(self._results)
        if self._history_digest:
            important.setdefault("conversation_digest", self._history_digest)

        return AgentVisibleState(
            user_goal=self.user_goal,
            current_stage=self.current_stage,
            completed_steps=list(self._completed),
            available_agents=list(self.available_agents),
            important_results=important,
            errors=list(self._errors),
            next_action_hint=self._next_hint,
            steps_remaining=max(0, self.planning_budget - self.step_index),
            step_index=self.step_index,
            local_workers_available=self._local_available,
        )

    # -- reads (orchestrator bookkeeping, not prompt input) ----------------
    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    @property
    def results(self) -> dict[str, str]:
        return dict(self._results)

    def latest_result(self) -> tuple[str, str] | None:
        if not self._results:
            return None
        key = next(reversed(self._results))
        return key, self._results[key]


def _truncate(text: str) -> str:
    text = (text or "").strip()
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[:MAX_RESULT_CHARS].rstrip() + "\n...[truncated]"


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())[:220]


__all__ = ["ExecutionStateManager", "MAX_RESULT_CHARS"]
