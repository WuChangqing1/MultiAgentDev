"""Application container.

One explicit composition root. Every dependency is constructed here and exposed
as an attribute, which avoids module-level globals scattered across the codebase
and makes the whole runtime constructible inside a test.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agents.main_agent import MainAgent
from core.config import Settings, effective_settings, ensure_data_dir
from db.database import Database
from orchestration.context_manager import ContextManager
from orchestration.orchestrator import Orchestrator
from orchestration.prompt_loader import PromptRepository
from orchestration.registry import AgentRegistry, build_registry
from providers.deepseek import DeepSeekProvider
from providers.llama_cpp import LlamaCppProvider
from services.event_bus import EventBus
from services.model_health import ModelHealthService
from services.runtime_state import RuntimeStateStore
from services.token_tracker import TokenTracker

log = logging.getLogger(__name__)


@dataclass
class AppContainer:
    """Holds the wired object graph for the running process."""

    settings: Settings
    prompts: PromptRepository
    database: Database
    event_bus: EventBus
    token_tracker: TokenTracker
    runtime_state: RuntimeStateStore
    model_health: ModelHealthService
    deepseek: DeepSeekProvider
    local: LlamaCppProvider
    registry: AgentRegistry
    orchestrator: Orchestrator
    context_manager: ContextManager

    @property
    def main_agent(self) -> MainAgent:
        return self.registry.main

    def refresh_settings(self) -> Settings:
        """Re-apply runtime overrides to everything holding a Settings reference.

        Settings are mutated in place, so this only has to notify the components
        that cache derived state (HTTP clients, health cache, agent references).
        """
        settings = effective_settings()
        self.settings = settings
        self.deepseek.set_model(settings.deepseek_model)
        self.deepseek.refresh()
        self.local.refresh()
        self.model_health.invalidate()
        for agent in self.registry.all_agents():
            agent.update_settings(settings)
        return settings


async def build_container() -> AppContainer:
    """Construct and start every component."""
    settings = effective_settings()
    ensure_data_dir()

    prompts = PromptRepository()
    database = Database(settings)
    await database.connect()

    event_bus = EventBus()
    token_tracker = TokenTracker(database, event_bus)
    runtime_state = RuntimeStateStore(event_bus)
    model_health = ModelHealthService()

    deepseek = DeepSeekProvider(settings)
    local = LlamaCppProvider(settings)

    main_agent = MainAgent(settings, deepseek, prompts)
    registry = build_registry(main_agent, local)

    context_manager = ContextManager()
    orchestrator = Orchestrator(
        settings=settings,
        registry=registry,
        database=database,
        event_bus=event_bus,
        token_tracker=token_tracker,
        runtime_state=runtime_state,
        model_health=model_health,
        local_provider=local,
        context_manager=context_manager,
    )

    log.info(
        "container_ready",
        extra={
            "deepseek_configured": settings.deepseek_configured,
            "local_model": settings.local_model_name,
            "max_agent_steps": settings.max_agent_steps,
        },
    )

    return AppContainer(
        settings=settings,
        prompts=prompts,
        database=database,
        event_bus=event_bus,
        token_tracker=token_tracker,
        runtime_state=runtime_state,
        model_health=model_health,
        deepseek=deepseek,
        local=local,
        registry=registry,
        orchestrator=orchestrator,
        context_manager=context_manager,
    )


async def shutdown_container(container: AppContainer) -> None:
    for execution_id in container.orchestrator.running_executions():
        await container.orchestrator.cancel(execution_id)
    await container.deepseek.aclose()
    await container.local.aclose()
    await container.database.disconnect()


__all__ = ["AppContainer", "build_container", "shutdown_container"]
