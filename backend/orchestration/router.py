"""Routing rules.

The MainAgent chooses *what* to do; the router decides whether that choice is
executable right now and, when it is not, what the honest fallback is.

Kept separate from the orchestrator so routing policy can be unit-tested without
spinning up models, a database or a web server.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from models.schemas import AgentDecision

log = logging.getLogger(__name__)


@dataclass
class RoutingDecision:
    """Verdict for one MainAgent step."""

    #: "worker" | "main" | "finalize" | "stop"
    mode: str
    agent_key: str | None = None
    #: Set when the router overrode the MainAgent; rendered into agent state.
    notice: str | None = None
    #: Reason recorded for Debug Mode.
    reason: str = ""


class Router:
    """Decides executability of a :class:`AgentDecision`."""

    def __init__(
        self,
        *,
        worker_keys: list[str],
        local_available: bool,
        enable_local_workers: bool,
    ) -> None:
        self._worker_keys = list(worker_keys)
        self._local_available = local_available
        self._enabled = enable_local_workers

    # -- capabilities ------------------------------------------------------
    @property
    def local_usable(self) -> bool:
        return self._enabled and self._local_available

    def can_route(self, agent_key: str | None) -> bool:
        if not agent_key:
            return False
        if agent_key == "main":
            return True
        if agent_key not in self._worker_keys:
            return False
        return self.local_usable

    def unknown_worker(self, agent_key: str | None) -> bool:
        return bool(agent_key) and agent_key != "main" and agent_key not in self._worker_keys

    # -- routing -----------------------------------------------------------
    def route(self, decision: AgentDecision) -> RoutingDecision:
        action = decision.action

        if action == "answer":
            return RoutingDecision("finalize", reason="model produced a final answer")

        if action in ("delegate", "review"):
            agent_key = decision.agent or ("local_reviewer" if action == "review" else None)

            if self.unknown_worker(agent_key):
                return RoutingDecision(
                    "main",
                    notice=(
                        f"Requested agent '{agent_key}' is not registered. "
                        f"Available: {', '.join(self._worker_keys)}. Handle it yourself."
                    ),
                    reason=f"unknown agent {agent_key}",
                )

            if not self.can_route(agent_key):
                if not self._enabled:
                    detail = "Local workers are disabled in settings."
                else:
                    detail = "The local MiniCPM worker is offline."
                return RoutingDecision(
                    "main",
                    notice=(
                        f"{detail} Complete the request yourself with DeepSeek and mention "
                        "briefly in the final answer that the local worker was unavailable."
                    ),
                    reason="local worker unavailable",
                )

            return RoutingDecision("worker", agent_key=agent_key, reason="delegated")

        if action == "continue":
            return RoutingDecision("main", reason="model asked for another reasoning step")

        if action == "replan":
            return RoutingDecision(
                "main",
                notice="Previous plan did not work out. Produce a corrected plan.",
                reason=decision.reason or "model requested replan",
            )

        return RoutingDecision("main", notice=f"Unknown action '{action}'.", reason="unknown action")


__all__ = ["Router", "RoutingDecision"]
