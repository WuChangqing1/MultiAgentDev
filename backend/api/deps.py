"""FastAPI dependency accessors.

The container is attached to ``app.state`` by ``main.py``; these helpers keep
route signatures clean and give one place to raise a clear 503 when the app is
not fully started.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from api.container import AppContainer


def get_container(request: Request) -> AppContainer:
    container = getattr(request.app.state, "container", None)
    if container is None:  # pragma: no cover - only during a failed startup
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Backend is still starting up. Please retry in a moment.",
        )
    return container


__all__ = ["get_container"]
