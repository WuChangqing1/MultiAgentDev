"""``LocalAgent`` -- the single shared call layer for every MiniCPM worker.

Requirement #6: Extractor, Summarizer, Classifier and Reviewer are **not** four
model instances. They are four prompt/configurations over one ``llama-server``
endpoint. This module is that shared layer; the four concrete workers are thin
subclasses that declare a prompt file and an output contract.

Reasoning policy per worker (requirement #34), configurable in ``.env``:

============  ===================
worker        default reasoning
============  ===================
extractor     none
classifier    none
summarizer    low
reviewer      medium
============  ===================
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from models.schemas import AgentTask, AgentVisibleState
from providers.base import LLMMessage

from agents.base import AgentError, WorkerAgent


class LocalAgent(WorkerAgent):
    """Shared MiniCPM worker implementation.

    Everything a worker needs (prompt file, output kind, reasoning policy) is
    declared as a class attribute by the subclass; the call path lives here so
    the four workers cannot drift apart.
    """

    async def stream(
        self,
        task: AgentTask,
        state: AgentVisibleState,
    ) -> AsyncIterator[tuple[str, str | None]]:
        """Streaming variant, used by the orchestrator for live worker output.

        Reasoning deltas are yielded separately from content deltas so the UI
        can keep thinking text out of the final answer.
        """
        prompt = self._build_prompt(
            task=task.instruction,
            context=task.context or None,
            state=state,
            worker=True,
        )
        try:
            async for content_delta, reasoning_delta in self._provider.stream(
                self._messages(prompt), self._options(stream=True)
            ):
                yield content_delta, reasoning_delta
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise AgentError(f"{self.label} stream failed: {exc}", agent=self.key) from exc


__all__ = ["LocalAgent"]
