"""Statistics API (session-level telemetry)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from api.container import AppContainer
from api.deps import get_container
from models.schemas import StatsResponse
from models.token_usage import TokenUsageAggregate

router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats", response_model=StatsResponse)
async def stats(container: AppContainer = Depends(get_container)) -> StatsResponse:
    """Aggregate token/latency figures across the whole database."""
    raw = await container.database.aggregate_stats()
    by_agent = {
        name: (value if isinstance(value, TokenUsageAggregate) else TokenUsageAggregate(**value))
        for name, value in raw.pop("by_agent", {}).items()
    }
    return StatsResponse(**raw, by_agent=by_agent)


__all__ = ["router"]
