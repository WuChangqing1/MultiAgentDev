"""Router and fallback behaviour.

Requirement #29: a worker that is offline, unknown, or broken must never fail
the task -- the work falls back to DeepSeek.
"""

from __future__ import annotations

import pytest

from models.schemas import AgentDecision
from orchestration.router import Router

WORKERS = ["local_extractor", "local_summarizer", "local_classifier", "local_reviewer"]


def make_router(*, local_available: bool = True, enabled: bool = True) -> Router:
    return Router(worker_keys=WORKERS, local_available=local_available, enable_local_workers=enabled)


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------


def test_delegates_to_a_known_worker_when_local_is_online():
    route = make_router().route(
        AgentDecision(action="delegate", agent="local_extractor", task="extract")
    )
    assert route.mode == "worker"
    assert route.agent_key == "local_extractor"


def test_answer_action_finalizes():
    route = make_router().route(AgentDecision(action="answer", answer="done"))
    assert route.mode == "finalize"


def test_continue_and_replan_stay_with_main():
    assert make_router().route(AgentDecision(action="continue", task="t")).mode == "main"
    replan = make_router().route(AgentDecision(action="replan", reason="bad plan"))
    assert replan.mode == "main"
    assert replan.notice


# --------------------------------------------------------------------------
# Fallbacks
# --------------------------------------------------------------------------


def test_offline_local_model_falls_back_to_main():
    route = make_router(local_available=False).route(
        AgentDecision(action="delegate", agent="local_extractor", task="extract")
    )
    assert route.mode == "main"
    assert route.notice is not None
    assert "offline" in route.notice.lower()


def test_disabled_local_workers_fall_back_to_main():
    route = make_router(enabled=False).route(
        AgentDecision(action="delegate", agent="local_summarizer", task="summarise")
    )
    assert route.mode == "main"
    assert route.notice is not None
    assert "disabled" in route.notice.lower()


def test_unknown_agent_falls_back_to_main():
    route = make_router().route(AgentDecision(action="delegate", agent="coding_agent", task="write code"))
    assert route.mode == "main"
    assert route.notice is not None
    assert "coding_agent" in route.notice


def test_review_without_agent_defaults_to_reviewer():
    route = make_router().route(AgentDecision(action="review", task="check this", context="{}"))
    assert route.mode == "worker"
    assert route.agent_key == "local_reviewer"


def test_delegate_without_agent_is_not_routable():
    route = make_router().route(AgentDecision(action="delegate", task="something"))
    assert route.mode == "main"


def test_capability_helpers():
    router = make_router(local_available=False)
    assert router.local_usable is False
    assert router.can_route("main") is True
    assert router.can_route("local_extractor") is False
    assert router.unknown_worker("local_extractor") is False
    assert router.unknown_worker("search_agent") is True
    assert router.unknown_worker(None) is False


@pytest.mark.parametrize("agent", WORKERS)
def test_every_registered_worker_is_routable(agent):
    route = make_router().route(AgentDecision(action="delegate", agent=agent, task="t"))
    assert route.mode == "worker"
    assert route.agent_key == agent
