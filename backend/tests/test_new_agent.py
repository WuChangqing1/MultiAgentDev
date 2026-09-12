"""Tests for adding a new worker agent.

`CoderAgent` is a real, shipped example of the extension path: a prompt file
plus a handful of class attributes. These tests document the full checklist that
adding any new agent has to satisfy, so a contributor can copy them.
"""

from __future__ import annotations

import pytest

from agents.base import WorkerAgent
from agents.coder import CoderAgent
from core.config import Settings, settings_store
from orchestration.prompt_loader import PromptRepository
from orchestration.registry import build_workers
from orchestration.router import Router
from tests.conftest import ScriptedProvider, make_response


@pytest.fixture
def local_provider():
    return ScriptedProvider(
        [make_response("```python\nprint('hi')\n```", model="MiniCPM5-2B", provider="llamacpp", is_local=True)],
        name="llamacpp",
        model="MiniCPM5-2B",
        is_local=True,
    )


class StubMain:
    """Minimal stand-in for MainAgent.

    The registry only needs ``settings``, ``prompts``, ``key`` and
    ``reasoning_field``. Declaring them here keeps the stub honest: if the real
    MainAgent's interface changes, this breaks loudly rather than the registry
    failing at runtime in production.
    """

    key = "main"
    reasoning_field = "reasoning_main"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.prompts = PromptRepository()


# --------------------------------------------------------------------------
# Checklist item 1: the agent is registered
# --------------------------------------------------------------------------


def test_coder_is_registered(local_provider):
    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    assert "local_coder" in workers
    assert isinstance(workers["local_coder"], CoderAgent)
    # Every worker shares ONE provider instance -- that is how four agents avoid
    # loading four models.
    assert all(worker.provider is local_provider for worker in workers.values())


def test_new_agent_is_routable(local_provider):
    """The router must accept it, otherwise the MainAgent can never reach it."""
    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    router = Router(
        worker_keys=list(workers),
        local_available=True,
        enable_local_workers=True,
    )
    from models.schemas import AgentDecision

    route = router.route(
        AgentDecision(action="delegate", agent="local_coder", task="write fizzbuzz")
    )
    assert route.mode == "worker"
    assert route.agent_key == "local_coder"


def test_unknown_key_still_falls_back(local_provider):
    """A typo must degrade to DeepSeek, not crash."""
    router = Router(worker_keys=["local_coder"], local_available=True, enable_local_workers=True)
    from models.schemas import AgentDecision

    route = router.route(AgentDecision(action="delegate", agent="local_nope", task="x"))
    assert route.mode == "main"
    assert route.notice and "local_nope" in route.notice


# --------------------------------------------------------------------------
# Checklist item 2: prompt file exists and is loadable
# --------------------------------------------------------------------------


def test_prompt_file_loads_and_is_non_empty():
    repo = PromptRepository()
    text = repo.worker_system_prompt("coder")
    assert text.strip()
    assert "Coder" in text
    # The registry uses prompt_name, not key, so a mismatch here breaks at runtime.
    assert repo.worker_system_prompt(CoderAgent.prompt_name) == text


def test_prompt_name_resolves_for_every_worker(local_provider):
    """Guards against a worker whose prompt_name does not match any file."""
    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    for key, worker in workers.items():
        assert worker.system_prompt().strip(), f"{key} has an empty system prompt"


# --------------------------------------------------------------------------
# Checklist item 3: it runs and produces a usable result
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coder_runs_and_returns_output(local_provider):
    from models.schemas import AgentTask, AgentVisibleState

    worker = CoderAgent(settings_store().effective(), local_provider, PromptRepository())
    task = AgentTask(
        task_id="t1",
        agent="local_coder",
        instruction="Write a one-line Python hello world.",
        context="Language: Python 3.12",
    )
    result = await worker.run(task, AgentVisibleState(user_goal="demo"))

    assert result.status == "completed"
    assert "print" in result.output
    assert result.usage is not None
    assert result.usage.is_local is True


# --------------------------------------------------------------------------
# Checklist item 4: the prompt the worker sees is well-formed
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_prompt_has_task_context_and_state_tail(local_provider):
    from models.schemas import AgentTask, AgentVisibleState

    worker = CoderAgent(settings_store().effective(), local_provider, PromptRepository())
    await worker.run(
        AgentTask(task_id="t2", agent="local_coder", instruction="INSTRUCTION_MARKER", context="CONTEXT_MARKER"),
        AgentVisibleState(user_goal="goal", current_stage="working"),
    )

    prompt = local_provider.prompts[0][0].content
    assert "INSTRUCTION_MARKER" in prompt
    assert "CONTEXT_MARKER" in prompt
    # Agent state must be the tail, and telemetry must not appear at all.
    assert "===== INTERNAL AGENT STATE =====" in prompt
    assert prompt.rindex("===== INTERNAL AGENT STATE =====") > prompt.rindex("CONTEXT_MARKER")
    for forbidden in ("prompt_tokens", "latency_ms", "execution_id", "tokens_per_second"):
        assert forbidden not in prompt


# --------------------------------------------------------------------------
# Checklist item 5: reasoning policy is configurable like every other worker
# --------------------------------------------------------------------------


def test_reasoning_defaults_are_safe_for_unmapped_keys():
    """An unmapped agent key must not silently inherit someone else's policy.

    A newly added worker is cheap by default and has to opt in to more thinking
    explicitly, which is the safe direction: forgetting to configure a new agent
    costs a little quality, not a lot of latency.
    """
    settings = Settings()
    assert settings.reasoning_for("local_some_new_agent") == "none"
    assert settings.reasoning_for("") == "none"


def test_coder_reasoning_default_is_low():
    """The shipped coder agent does opt in, because code needs some thought."""
    assert Settings().reasoning_for("local_coder") == "low"


def test_reasoning_can_be_overridden_at_runtime():
    from core.config import RuntimeOverrides

    store = settings_store()
    original = store.effective().reasoning_for("local_coder")
    try:
        store.update(RuntimeOverrides(reasoning_coder="high"))
        assert store.effective().reasoning_for("local_coder") == "high"
    finally:
        store.reset()
    assert store.effective().reasoning_for("local_coder") == original


def test_all_shipped_workers_subclass_worker_agent(local_provider):
    """A worker that forgets to subclass WorkerAgent would bypass task/context
    handling and the telemetry guard."""
    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    for key, worker in workers.items():
        assert isinstance(worker, WorkerAgent), f"{key} is not a WorkerAgent"
        assert worker.key.startswith("local_"), f"{key} must use the local_ prefix"
        assert worker.label and worker.role_description


# --------------------------------------------------------------------------
# Checklist item 6: the rest of the system learns about it automatically
# --------------------------------------------------------------------------


def test_every_agent_declares_its_reasoning_settings_field(local_provider):
    """Without this, the Settings UI cannot configure the agent.

    This is the check that caught `local_coder` being registered but missing
    from the settings response.
    """
    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    for key, worker in workers.items():
        assert worker.reasoning_field, f"{key} does not declare reasoning_field"


def test_settings_response_lists_every_registered_worker():
    """The settings payload must be derived from the registry, not hardcoded.

    Hardcoding is exactly how a new agent ends up invisible in the UI.
    """
    import inspect

    from api import settings as settings_module

    source = inspect.getsource(settings_module._public_view)  # noqa: SLF001
    assert "container.registry.worker_keys()" in source, (
        "reasoning policies must come from the registry, not a literal dict"
    )
    assert "reasoning_fields" in source


def test_registry_exposes_field_mapping(local_provider):
    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    from orchestration.registry import AgentRegistry

    registry = AgentRegistry(
        StubMain(settings_store().effective()),  # type: ignore[arg-type]
        workers,
    )
    mapping = registry.reasoning_settings_fields()
    assert set(mapping) == set(workers) | {"main"}
    assert mapping["local_coder"] == "reasoning_coder"


def test_main_agent_declares_the_same_interface_as_the_stub():
    """Keep the stub above in sync with the real MainAgent."""
    from agents.main_agent import MainAgent

    assert MainAgent.key == StubMain.key
    assert MainAgent.reasoning_field == StubMain.reasoning_field


def test_reasoning_field_names_are_real_settings_fields():
    """Guards against a typo that would make an override silently do nothing."""
    from core.config import RuntimeOverrides, Settings

    valid = set(Settings.model_fields) | set(RuntimeOverrides.model_fields)
    for field in (
        "reasoning_main",
        "reasoning_extractor",
        "reasoning_summarizer",
        "reasoning_classifier",
        "reasoning_reviewer",
        "reasoning_coder",
    ):
        assert field in valid, f"{field} is not a real settings field"


def test_agent_display_order_covers_every_worker(local_provider):
    """The left-hand panel must have a slot for every registered worker."""
    from services.runtime_state import AGENT_DISPLAY_ORDER

    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    missing = set(workers) - set(AGENT_DISPLAY_ORDER)
    assert not missing, f"workers missing from AGENT_DISPLAY_ORDER: {sorted(missing)}"


# --------------------------------------------------------------------------
# Checklist item 7: the MainAgent is TOLD about it (this is what makes
# delegation automatic rather than requiring a prompt edit)
# --------------------------------------------------------------------------


def test_worker_catalog_describes_every_worker(local_provider):
    from orchestration.registry import AgentRegistry

    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    registry = AgentRegistry(StubMain(settings_store().effective()), workers)  # type: ignore[arg-type]

    catalog = registry.worker_catalog(local_available=True)
    assert set(catalog) == set(workers)
    # Each entry must carry the human-readable role, otherwise the MainAgent has
    # a key with no idea what it is for.
    assert "Coder" in catalog["local_coder"]
    assert "代码" in catalog["local_coder"] or "code" in catalog["local_coder"].lower()


def test_worker_catalog_is_empty_when_local_model_is_offline(local_provider):
    """An offline worker must never be offered as a delegation target."""
    from orchestration.registry import AgentRegistry

    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    registry = AgentRegistry(StubMain(settings_store().effective()), workers)  # type: ignore[arg-type]
    assert registry.worker_catalog(local_available=False) == {}


def test_new_agent_reaches_the_prompt_without_editing_prompts(local_provider):
    """End-to-end proof that registering an agent is sufficient.

    The static prefix deliberately no longer lists workers, so if this passes
    the catalogue can only have come from the registry.
    """
    from orchestration.registry import AgentRegistry
    from orchestration.state_manager import ExecutionStateManager

    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    registry = AgentRegistry(StubMain(settings_store().effective()), workers)  # type: ignore[arg-type]

    state = ExecutionStateManager(
        "exec-1",
        "write a palindrome checker",
        max_steps=12,
        available_agents=["main", *registry.worker_keys()],
    )
    state.set_local_available(True)
    state.set_worker_catalog(registry.worker_catalog(local_available=True))

    rendered = state.visible_state().render()
    assert "available_workers" in rendered
    assert "local_coder" in rendered
    assert "Coder" in rendered


def test_prompt_tail_omits_workers_when_offline(local_provider):
    from orchestration.registry import AgentRegistry
    from orchestration.state_manager import ExecutionStateManager

    workers = build_workers(StubMain(settings_store().effective()), local_provider)  # type: ignore[arg-type]
    registry = AgentRegistry(StubMain(settings_store().effective()), workers)  # type: ignore[arg-type]

    state = ExecutionStateManager("exec-2", "goal", max_steps=6, available_agents=["main"])
    state.set_local_available(False)
    state.set_worker_catalog(registry.worker_catalog(local_available=False))

    rendered = state.visible_state().render()
    assert "available_workers: (none" in rendered
    assert "local_coder" not in rendered


def test_static_prefix_no_longer_hardcodes_worker_keys():
    """A hardcoded list goes stale the moment an agent is added.

    This is the exact defect that made the freshly added coder agent never get
    picked: it was registered and routable, but the MainAgent had never been
    told it existed.
    """
    from orchestration.prompt_loader import PromptRepository

    prefix = PromptRepository().main_agent_static_prefix()
    for key in ("local_extractor", "local_summarizer", "local_classifier", "local_reviewer", "local_coder"):
        # The key may appear in worked examples, but not as a catalogue row.
        assert f"| `{key}` |" not in prefix, (
            f"{key} is listed as a catalogue row in the static prefix; "
            "the catalogue must come from the registry instead"
        )
