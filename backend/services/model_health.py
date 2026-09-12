"""Model availability probing.

The system must never crash because ``llama-server`` is not running: this
service reports status, and the orchestrator degrades to DeepSeek-only.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from core.config import effective_settings
from models.schemas import ModelStatus
from providers.deepseek import DeepSeekProvider
from providers.llama_cpp import LlamaCppProvider

log = logging.getLogger(__name__)

_CACHE_TTL_S = 10.0
_PROBE_TIMEOUT_S = 6.0


@dataclass
class _CacheEntry:
    statuses: list[ModelStatus]
    at: float


@dataclass
class _LocalCacheEntry:
    status: ModelStatus
    at: float


class ModelHealthService:
    """Cached health probes for both backends."""

    def __init__(self) -> None:
        self._cache: _CacheEntry | None = None
        self._local_cache: _LocalCacheEntry | None = None
        self._lock = asyncio.Lock()

    async def _probe(
        self,
        deepseek: DeepSeekProvider,
        local: LlamaCppProvider,
        *,
        include_deepseek: bool,
    ) -> list[ModelStatus]:
        settings = effective_settings()

        async def probe_local() -> ModelStatus:
            try:
                return await asyncio.wait_for(self._local_probe(local, settings), timeout=_PROBE_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                return ModelStatus(
                    name=settings.local_model_name,
                    provider=local.name,
                    is_local=True,
                    status="offline",
                    detail="probe timed out",
                )

        async def probe_deepseek() -> ModelStatus:
            if not settings.deepseek_configured:
                return ModelStatus(
                    name=settings.deepseek_model,
                    provider=deepseek.name,
                    is_local=False,
                    status="unknown",
                    detail="DEEPSEEK_API_KEY is not configured in .env",
                )
            if not include_deepseek:
                # Avoid burning a network round trip on every page load.
                return ModelStatus(
                    name=settings.deepseek_model,
                    provider=deepseek.name,
                    is_local=False,
                    status="configured",
                    detail="API key present; reachability not probed",
                )
            try:
                result = await asyncio.wait_for(deepseek.health_check(), timeout=_PROBE_TIMEOUT_S)
            except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                result = {"status": "error", "detail": "probe timed out", "models": []}
            return ModelStatus(
                name=settings.deepseek_model,
                provider=deepseek.name,
                is_local=False,
                status=result.get("status", "unknown"),  # type: ignore[arg-type]
                detail=result.get("detail"),
                latency_ms=result.get("latency_ms"),
                models=result.get("models", []),
            )

        return await asyncio.gather(probe_deepseek(), probe_local())

    async def statuses(
        self,
        deepseek: DeepSeekProvider,
        local: LlamaCppProvider,
        *,
        force: bool = False,
        include_deepseek: bool = True,
    ) -> list[ModelStatus]:
        async with self._lock:
            fresh = self._cache is not None and (time.monotonic() - self._cache.at) < _CACHE_TTL_S
            if fresh and not force:
                return self._cache.statuses  # type: ignore[union-attr]
            try:
                statuses = await self._probe(deepseek, local, include_deepseek=include_deepseek)
            except Exception:  # noqa: BLE001 - health must never raise
                log.warning("model_health_probe_failed", exc_info=True)
                statuses = [
                    ModelStatus(
                        name=effective_settings().deepseek_model,
                        provider="deepseek",
                        is_local=False,
                        status="unknown",
                        detail="probe failed",
                    ),
                    ModelStatus(
                        name=effective_settings().local_model_name,
                        provider="llamacpp",
                        is_local=True,
                        status="offline",
                        detail="probe failed",
                    ),
                ]
            self._cache = _CacheEntry(statuses=statuses, at=time.monotonic())
            return statuses

    async def local_status(self, local: LlamaCppProvider, *, force: bool = False) -> ModelStatus:
        """Probe only the local endpoint (used before scheduling local work)."""
        settings = effective_settings()

        async def probe() -> ModelStatus:
            return await asyncio.wait_for(self._local_probe(local, settings), timeout=_PROBE_TIMEOUT_S)

        async with self._lock:
            if (
                not force
                and self._local_cache is not None
                and (time.monotonic() - self._local_cache.at) < _CACHE_TTL_S
            ):
                return self._local_cache.status
            try:
                # Built inside the try so a probe is never created and dropped
                # un-awaited (which would emit a RuntimeWarning).
                status = await probe()
            except Exception:  # noqa: BLE001
                status = ModelStatus(
                    name=settings.local_model_name,
                    provider=local.name,
                    is_local=True,
                    status="offline",
                    detail="probe failed",
                )
            self._local_cache = _LocalCacheEntry(status=status, at=time.monotonic())
            return status

    async def local_available(self, local: LlamaCppProvider) -> bool:
        """Fast gate used by the router before scheduling local work."""
        if not effective_settings().enable_local_workers:
            return False
        status = await self.local_status(local)
        return status.status == "online"

    def invalidate(self) -> None:
        self._cache = None
        self._local_cache = None

    @staticmethod
    async def _local_probe(local: LlamaCppProvider, settings) -> ModelStatus:
        result = await local.health_check()
        return ModelStatus(
            name=settings.local_model_name,
            provider=local.name,
            is_local=True,
            status=result.get("status", "offline"),  # type: ignore[arg-type]
            detail=result.get("detail"),
            latency_ms=result.get("latency_ms"),
            models=result.get("models", []),
        )


__all__ = ["ModelHealthService"]
