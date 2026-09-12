"""Health, model status and agent catalogue."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query

from api.container import AppContainer
from api.deps import get_container
from models.schemas import AgentDescriptor, HealthResponse, ModelStatus

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health", response_model=HealthResponse)
async def health(
    deepseek_probe: bool = Query(
        default=False,
        description="Also verify DeepSeek reachability (costs one network round trip).",
    ),
    container: AppContainer = Depends(get_container),
) -> HealthResponse:
    """Liveness of the backend and both model backends.

    The backend never reports itself offline just because a model is down --
    that distinction is exactly what this endpoint exists to make.
    """
    statuses = await container.model_health.statuses(
        container.deepseek, container.local, include_deepseek=deepseek_probe
    )
    by_provider = {status.provider: status for status in statuses}
    database_ok = await container.database.ping()

    return HealthResponse(
        backend="online",
        deepseek=by_provider.get("deepseek", ModelStatus(
            name=container.settings.deepseek_model,
            provider="deepseek",
            is_local=False,
            status="unknown",
        )).status,
        minicpm=by_provider.get("llamacpp", ModelStatus(
            name=container.settings.local_model_name,
            provider="llamacpp",
            is_local=True,
            status="offline",
        )).status,
        local_workers_enabled=container.settings.enable_local_workers,
        database="online" if database_ok else "error",
        details={
            "models": [status.model_dump(mode="json") for status in statuses],
            "max_agent_steps": container.settings.max_agent_steps,
            "debug_mode": container.settings.debug_mode,
            "prompts_dir": str(container.prompts.directory),
        },
    )


@router.get("/models/status", response_model=list[ModelStatus])
async def model_status(
    force: bool = Query(default=False, description="Bypass the short-lived status cache."),
    probe_deepseek: bool = Query(default=True),
    container: AppContainer = Depends(get_container),
) -> list[ModelStatus]:
    return await container.model_health.statuses(
        container.deepseek, container.local, force=force, include_deepseek=probe_deepseek
    )


@router.get("/agents", response_model=list[AgentDescriptor])
async def list_agents(
    probe_local: bool = Query(default=True),
    container: AppContainer = Depends(get_container),
) -> list[AgentDescriptor]:
    """Agent catalogue for the left-hand panel."""
    local_available = True
    if probe_local:
        status = await container.model_health.local_status(container.local)
        local_available = status.status == "online"
    return [AgentDescriptor(**descriptor) for descriptor in container.registry.descriptors(
        local_available=local_available
    )]


__all__ = ["router"]
