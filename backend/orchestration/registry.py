"""Agent registry.

Maps a stable agent key to its implementation, so the orchestrator, the API and
the router all resolve agents by name rather than by ``if/elif`` chains. Adding
``CodingAgent`` / ``SearchAgent`` / ``RAGAgent`` later means registering one more
class.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from agents.base import BaseAgent, WorkerAgent
from agents.classifier import ClassifierAgent
from agents.extractor import ExtractorAgent
from agents.local_agent import LocalAgent
from agents.main_agent import MainAgent, is_local_agent_key
from agents.reviewer import ReviewerAgent
from agents.summarizer import SummarizerAgent

if TYPE_CHECKING:  # pragma: no cover
    from providers.base import LLMProvider

log = logging.getLogger(__name__)

MAIN_AGENT_KEY = "main"


class AgentRegistry:
    """Holds one instance per agent key."""

    def __init__(self, main_agent: MainAgent, workers: dict[str, WorkerAgent]) -> None:
        self._main = main_agent
        self._workers = dict(workers)
        self._all: dict[str, BaseAgent] = {MAIN_AGENT_KEY: main_agent, **self._workers}

    # -- access ------------------------------------------------------------
    @property
    def main(self) -> MainAgent:
        return self._main

    def get(self, key: str) -> BaseAgent | None:
        return self._all.get(key)

    def worker(self, key: str) -> WorkerAgent | None:
        return self._workers.get(key)

    def keys(self) -> list[str]:
        return list(self._all.keys())

    def worker_keys(self) -> list[str]:
        return list(self._workers.keys())

    def all_agents(self) -> list[BaseAgent]:
        return list(self._all.values())

    def workers(self) -> list[WorkerAgent]:
        return list(self._workers.values())

    def descriptors(self, *, local_available: bool) -> list[dict]:
        """Static descriptions for the Agent panel (user-visible, not promptable)."""
        settings = self._main.settings
        out: list[dict] = []
        for agent in self._all.values():
            available = True
            if is_local_agent_key(agent.key):
                available = local_available and settings.enable_local_workers
            out.append(
                {
                    "key": agent.key,
                    "label": agent.label,
                    "model": agent.provider.model,
                    "provider": agent.provider.name,
                    "is_local": agent.provider.is_local,
                    "role": agent.role_description,
                    "reasoning_effort": settings.reasoning_for(agent.key),
                    "available": available,
                }
            )
        return out

    def reload_workers(self, providers_factory) -> None:
        """Rebuild worker instances after a settings change.

        Workers are cheap (prompt + config, no model load), so recreating them is
        the simplest correct way to pick up new prompts or reasoning policies.
        """
        self._workers = build_workers(self._main, providers_factory)
        self._all = {MAIN_AGENT_KEY: self._main, **self._workers}


def build_workers(main_agent: MainAgent, local_provider: "LLMProvider") -> dict[str, WorkerAgent]:
    """Instantiate every local worker on the shared local provider."""
    classes = (ExtractorAgent, SummarizerAgent, ClassifierAgent, ReviewerAgent)
    workers: dict[str, WorkerAgent] = {}
    for cls in classes:
        worker = cls(main_agent.settings, local_provider, main_agent.prompts)  # type: ignore[arg-type]
        workers[worker.key] = worker
    return workers


def build_registry(main_agent: MainAgent, local_provider: "LLMProvider") -> AgentRegistry:
    return AgentRegistry(main_agent, build_workers(main_agent, local_provider))


__all__ = [
    "MAIN_AGENT_KEY",
    "AgentRegistry",
    "LocalAgent",
    "build_registry",
    "build_workers",
]
