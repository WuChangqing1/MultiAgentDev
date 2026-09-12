"""Execution inspection API (history, timeline, per-step detail)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from api.container import AppContainer
from api.deps import get_container
from models.schemas import AgentStep, Execution

router = APIRouter(prefix="/api/executions", tags=["executions"])


@router.get("/{execution_id}", response_model=Execution)
async def get_execution(
    execution_id: str,
    container: AppContainer = Depends(get_container),
) -> Execution:
    execution = await container.database.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    return execution


@router.get("/{execution_id}/steps", response_model=list[AgentStep])
async def get_steps(
    execution_id: str,
    container: AppContainer = Depends(get_container),
) -> list[AgentStep]:
    execution = await container.database.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    steps = [
        step
        for step in execution.steps
        if container.settings.debug_mode or not _is_debug_only(step)
    ]
    return steps


@router.get("/{execution_id}/calls")
async def get_model_calls(
    execution_id: str,
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    """Raw model-call telemetry for one execution (Debug Mode / analysis)."""
    execution = await container.database.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found")
    calls = await container.database.list_model_calls(execution_id)
    totals = container.token_tracker.aggregate(execution_id)
    return {
        "execution_id": execution_id,
        "calls": calls,
        "totals": totals.model_dump(mode="json"),
        "by_agent": {
            name: agg.model_dump(mode="json")
            for name, agg in container.token_tracker.by_agent(execution_id).items()
        },
    }


@router.get("/{execution_id}/state")
async def get_agent_state(
    execution_id: str,
    container: AppContainer = Depends(get_container),
) -> dict[str, object]:
    """Live user-visible telemetry for an execution.

    Debug Mode additionally exposes the *internal* agent-visible state, clearly
    labelled so it is never confused with user-visible telemetry.
    """
    telemetry = container.runtime_state.get(execution_id)
    payload: dict[str, object] = {
        "execution_id": execution_id,
        "telemetry": telemetry.model_dump(mode="json") if telemetry else None,
        "agent_status": {k: str(v) for k, v in container.runtime_state.agent_statuses().items()},
        "running": container.orchestrator.is_running(execution_id),
    }
    if container.settings.debug_mode:
        execution = await container.database.get_execution(execution_id)
        payload["internal"] = {
            "label": "INTERNAL — agent-visible state, injected into prompts",
            "agent_visible_state": telemetry.model_dump(mode="json") if telemetry else None,
            "user_input": execution.user_input if execution else None,
        }
    return payload


def _is_debug_only(step: AgentStep) -> bool:
    """Steps that carry only debug payloads are hidden outside Debug Mode."""
    return bool(step.debug) and not (step.output or step.error or step.reasoning)


__all__ = ["router"]
