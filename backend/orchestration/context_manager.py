"""Context management.

Requirement #31: never copy the whole chat history into a worker call. Two
responsibilities live here:

* **History windows** -- the MainAgent sees a bounded number of recent messages,
  optionally preceded by a compact digest of everything older.
* **Worker payloads** -- a worker receives only a task and the minimal context
  the MainAgent supplied, never conversation history.

Digests are produced by the local summarizer when it is available and by a
deterministic truncation otherwise, so context management never becomes a hard
dependency on the local model.
"""

from __future__ import annotations

import logging

from models.schemas import Message

log = logging.getLogger(__name__)

#: Newest N messages handed to the MainAgent verbatim.
HISTORY_WINDOW = 6
#: Older messages are compressed into at most this many characters.
DIGEST_BUDGET = 1200
#: Per-worker context ceiling (characters, ~4 chars/token heuristic).
WORKER_CONTEXT_BUDGET = 6000


class ContextManager:
    """Bounded history windows, digests and worker payloads."""

    def __init__(self, *, history_window: int = HISTORY_WINDOW, digest_budget: int = DIGEST_BUDGET) -> None:
        self._history_window = history_window
        self._digest_budget = digest_budget

    # -- history -----------------------------------------------------------
    def split_history(self, messages: list[Message]) -> tuple[list[Message], list[Message]]:
        """Return ``(older, recent)`` for a full message list."""
        if len(messages) <= self._history_window:
            return [], list(messages)
        return list(messages[: -self._history_window]), list(messages[-self._history_window :])

    def digest(self, older: list[Message], *, current_input: str = "") -> str:
        """Deterministic digest of older turns.

        A summarizer-based digest can be layered on top by the orchestrator; this
        fallback is always available and costs nothing.
        """
        if not older:
            return ""

        turns = [m for m in older if (m.content or "").strip()]
        if not turns:
            return ""

        lines: list[str] = []
        for message in turns:
            role = "User" if message.role == "user" else "Assistant"
            text = " ".join((message.content or "").split())
            lines.append(f"{role}: {text}")

        joined = "\n".join(lines)
        if len(joined) <= self._digest_budget:
            return f"Earlier turns ({len(turns)} messages):\n{joined}"

        # Keep the head (topic) and the tail (most recent decisions).
        head_len = self._digest_budget // 3
        tail_len = self._digest_budget - head_len - 40
        head = joined[:head_len].rstrip()
        tail = joined[-tail_len:].lstrip()
        return (
            f"Earlier turns ({len(turns)} messages, compressed):\n"
            f"{head}\n...[middle omitted]...\n{tail}"
        )

    def build_history_view(self, messages: list[Message]) -> tuple[list[Message], str]:
        """History to pass verbatim plus a digest of the omitted prefix."""
        older, recent = self.split_history(messages)
        return recent, self.digest(older)

    # -- worker payloads ---------------------------------------------------
    @staticmethod
    def clamp_worker_context(context: str | None, *, budget: int = WORKER_CONTEXT_BUDGET) -> str:
        """Bound a worker's context so a large document cannot blow the window.

        The head and tail are preserved because they usually carry the framing
        and the conclusion respectively.
        """
        text = (context or "").strip()
        if len(text) <= budget:
            return text
        head_len = int(budget * 0.65)
        tail_len = budget - head_len - 40
        return (
            f"{text[:head_len].rstrip()}\n"
            f"...[middle omitted: {len(text) - head_len - tail_len} chars]...\n"
            f"{text[-tail_len:].lstrip()}"
        )

    def worker_payload(self, context: str | None) -> str:
        return self.clamp_worker_context(context)

    @property
    def history_window(self) -> int:
        return self._history_window


__all__ = ["DIGEST_BUDGET", "HISTORY_WINDOW", "WORKER_CONTEXT_BUDGET", "ContextManager"]
