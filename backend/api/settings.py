"""Settings API.

Secrets never travel through this endpoint. ``GET`` reports only *whether* a key
is configured, plus a redacted fingerprint; the key itself lives in ``.env``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.container import AppContainer
from api.deps import get_container
from core.config import RuntimeOverrides, redact, settings_store
from models.schemas import ModelStatus

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _public_view(container: AppContainer) -> dict:
    """Everything the Settings panel needs, with no secret material."""
    settings = container.settings
    return {
        "deepseek": {
            "model": settings.deepseek_model,
            "base_url": settings.deepseek_base_url,
            "configured": settings.deepseek_configured,
            "api_key_fingerprint": redact(settings.deepseek_api_key),
            "max_tokens": settings.main_agent_max_tokens,
            "temperature": settings.main_agent_temperature,
            "reasoning_effort": settings.reasoning_main,
            "price_input": settings.deepseek_price_input,
            "price_output": settings.deepseek_price_output,
        },
        "local": {
            "model": settings.local_model_name,
            "base_url": settings.local_model_base_url,
            "max_tokens": settings.worker_max_tokens,
            "temperature": settings.worker_temperature,
            "top_p": settings.worker_top_p,
            "context_window": settings.local_model_context_window,
            # Derived from the registry, so a newly registered worker shows up in
            # the Settings panel automatically instead of being silently missing.
            "reasoning": {
                key: settings.reasoning_for(key) for key in container.registry.worker_keys()
            },
            # agent key -> Settings field name, so the UI never has to guess.
            "reasoning_fields": container.registry.reasoning_settings_fields(),
        },
        "orchestration": {
            "max_agent_steps": settings.max_agent_steps,
            "enable_local_workers": settings.enable_local_workers,
        },
        "ui": {
            "show_reasoning": settings.show_reasoning,
            "debug_mode": settings.debug_mode,
        },
        "overrides": settings_store().overrides.model_dump(exclude_none=True),
        "prompts": container.prompts.describe(),
    }


@router.get("")
async def get_settings(container: AppContainer = Depends(get_container)) -> dict:
    return _public_view(container)


@router.patch("")
async def update_settings(
    patch: RuntimeOverrides,
    container: AppContainer = Depends(get_container),
) -> dict:
    """Apply runtime overrides (in memory, for this process lifetime)."""
    store = settings_store()
    store.update(patch)
    container.refresh_settings()
    return {"applied": patch.as_patch(), "effective": _public_view(container)}


@router.post("/reset")
async def reset_settings(container: AppContainer = Depends(get_container)) -> dict:
    settings_store().reset()
    container.refresh_settings()
    return {"reset": True, "effective": _public_view(container)}


@router.post("/reload")
async def reload_env(container: AppContainer = Depends(get_container)) -> dict:
    """Re-read ``.env`` from disk without restarting the process."""
    settings_store().reload_from_env()
    container.refresh_settings()
    container.model_health.invalidate()
    return {"reloaded": True, "effective": _public_view(container)}


@router.get("/prompts")
async def prompt_info(container: AppContainer = Depends(get_container)) -> dict:
    """Character counts per prompt file -- useful for cache diagnostics."""
    main = container.registry.main
    return {
        "files": container.prompts.describe(),
        "static_prefix_chars": main.estimate_static_prefix_chars(),
        "worker_keys": container.registry.worker_keys(),
    }


@router.post("/validate", response_model=list[ModelStatus])
async def validate(container: AppContainer = Depends(get_container)) -> list[ModelStatus]:
    """Ping both backends with the current settings, bypassing the cache."""
    container.model_health.invalidate()
    return await container.model_health.statuses(
        container.deepseek, container.local, force=True, include_deepseek=True
    )


__all__ = ["router"]
