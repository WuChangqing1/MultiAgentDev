"""FastAPI application entry point.

Run from the ``backend`` directory::

    uvicorn main:app --host 127.0.0.1 --port 8000 --reload

The backend binds to ``127.0.0.1`` only: it is a local-first application, and the
browser never talks to ``llama-server`` directly.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api import chat, executions, health, settings as settings_api, stats
from api.container import AppContainer, build_container, shutdown_container
from core.config import Settings, ensure_data_dir, get_settings
from core.logging_setup import configure_logging

log = logging.getLogger(__name__)

VERSION = "1.0.0"

_container: AppContainer | None = None


def get_container_instance() -> AppContainer | None:
    return _container


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _container
    settings: Settings = get_settings()
    configure_logging(settings.log_level)
    ensure_data_dir()

    log.info("backend_starting", extra={"host": settings.host, "port": settings.port})
    _container = await build_container()
    app.state.container = _container

    # Warm the health cache so the first page load is instant.
    try:
        await _container.model_health.statuses(
            _container.deepseek, _container.local, force=True, include_deepseek=False
        )
    except Exception:  # noqa: BLE001 - never block startup on a probe
        log.warning("initial health probe failed", exc_info=True)

    try:
        yield
    finally:
        if _container is not None:
            await shutdown_container(_container)
        _container = None
        log.info("backend_stopped")


app = FastAPI(
    title="Multi-Agent Orchestrator",
    description=(
        "DeepSeek MainAgent + local MiniCPM5-2B workers. "
        "Context and Agent State go to prompts; telemetry goes to the UI."
    ),
    version=VERSION,
    lifespan=lifespan,
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Turn unexpected errors into a friendly, structured payload.

    Model outages are handled closer to the source; this is the last resort so
    the user never sees a bare ``500 Internal Server Error``.
    """
    log.exception("unhandled_error path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": (
                "Something went wrong inside the backend. The error has been logged. "
                "Try again, or check that both model backends are reachable."
            ),
            "error_type": type(exc).__name__,
            "path": request.url.path,
        },
    )


app.include_router(health.router)
app.include_router(chat.router)
app.include_router(executions.router)
app.include_router(stats.router)
app.include_router(settings_api.router)


@app.get("/", include_in_schema=False)
async def root() -> dict:
    return {
        "name": "Multi-Agent Orchestrator",
        "version": VERSION,
        "docs": "/docs",
        "health": "/api/health",
    }


if __name__ == "__main__":  # pragma: no cover - convenience only
    import uvicorn

    _s = get_settings()
    uvicorn.run("main:app", host=_s.host, port=_s.port, reload=False)
