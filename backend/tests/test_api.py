"""API tests using FastAPI's TestClient with the container stubbed out.

These verify the HTTP surface, error friendliness and SSE framing without
needing a live model.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import chat, executions, health, stats  # noqa: E402
from core.config import Settings  # noqa: E402
from db.database import Database  # noqa: E402
from models.events import ExecutionEvent  # noqa: E402
from orchestration.prompt_loader import PromptRepository  # noqa: E402
from services.event_bus import EventBus  # noqa: E402


@pytest.fixture
def app_client(tmp_path: Path):
    """A trimmed app: real routers, real database, stubbed orchestrator."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'api.db').as_posix()}",
        deepseek_api_key="test-key",
    )

    class StubOrchestrator:
        def __init__(self) -> None:
            self.started: list[dict] = []
            self.cancelled: list[str] = []

        async def start(self, *, conversation_id, user_input, user_message_id, max_steps=None):
            from models.schemas import Execution

            execution = Execution(
                id="exec-stub", conversation_id=conversation_id, user_input=user_input, status="running"
            )
            self.started.append({"user_input": user_input, "execution_id": execution.id})
            return execution

        async def cancel(self, execution_id: str) -> bool:
            self.cancelled.append(execution_id)
            return True

        def is_running(self, execution_id: str) -> bool:
            return False

        def running_executions(self) -> list[str]:
            return []

    class StubHealth:
        def __init__(self) -> None:
            self.invalidated = 0

        async def statuses(self, deepseek, local, *, force=False, include_deepseek=True):
            from models.schemas import ModelStatus

            return [
                ModelStatus(
                    name="deepseek-chat",
                    provider="deepseek",
                    is_local=False,
                    status="configured",
                    detail="API key present",
                ),
                ModelStatus(
                    name="MiniCPM5-2B",
                    provider="llamacpp",
                    is_local=True,
                    status="online",
                    detail="serving MiniCPM5-2B",
                    models=["MiniCPM5-2B"],
                ),
            ]

        async def local_status(self, local, *, force=False):
            from models.schemas import ModelStatus

            return ModelStatus(
                name="MiniCPM5-2B",
                provider="llamacpp",
                is_local=True,
                status="online",
                detail="serving MiniCPM5-2B",
            )

        async def local_available(self, local) -> bool:
            return True

        def invalidate(self) -> None:
            self.invalidated += 1

    class StubRegistry:
        def keys(self):
            return ["main", "local_extractor"]

        def worker_keys(self):
            return ["local_extractor"]

        def descriptors(self, *, local_available: bool):
            return [
                {
                    "key": "main",
                    "label": "DeepSeek MainAgent",
                    "model": "deepseek-chat",
                    "provider": "deepseek",
                    "is_local": False,
                    "role": "planner",
                    "reasoning_effort": "medium",
                    "available": True,
                }
            ]

        def all_agents(self):
            return []

    class StubContainer:
        def __init__(self) -> None:
            self.settings = settings
            self.database = database
            self.event_bus = EventBus()
            self.orchestrator = StubOrchestrator()
            self.model_health = StubHealth()
            self.registry = StubRegistry()
            self.prompts = PromptRepository()
            self.runtime_state = None
            self.deepseek = object()
            self.local = object()

        def refresh_settings(self):
            return self.settings

    loop = asyncio.new_event_loop()
    database = Database(settings)
    loop.run_until_complete(database.connect())

    container = StubContainer()
    app = FastAPI()
    app.state.container = container
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(executions.router)
    app.include_router(stats.router)

    with TestClient(app) as client:
        yield client, container

    loop.run_until_complete(database.disconnect())
    loop.close()


def test_health_endpoint_reports_both_backends(app_client):
    client, _ = app_client
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["backend"] == "online"
    assert body["deepseek"] == "configured"
    assert body["minicpm"] == "online"
    assert body["local_workers_enabled"] is True


def test_model_status_endpoint(app_client):
    client, _ = app_client
    response = client.get("/api/models/status")
    assert response.status_code == 200
    statuses = response.json()
    assert {s["provider"] for s in statuses} == {"deepseek", "llamacpp"}


def test_agents_endpoint(app_client):
    client, _ = app_client
    response = client.get("/api/agents")
    assert response.status_code == 200
    assert response.json()[0]["key"] == "main"


def test_chat_creates_execution_and_conversation(app_client):
    client, container = app_client
    response = client.post("/api/chat", json={"message": "hello there"})
    assert response.status_code == 202

    body = response.json()
    assert body["execution_id"] == "exec-stub"
    assert body["conversation_id"]
    assert body["stream_url"] == "/api/events/exec-stub"
    assert container.orchestrator.started[0]["user_input"] == "hello there"


def test_chat_rejects_empty_message(app_client):
    client, _ = app_client
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_chat_reuses_an_existing_conversation(app_client):
    client, _ = app_client
    first = client.post("/api/chat", json={"message": "first"}).json()
    second = client.post(
        "/api/chat", json={"message": "second", "conversation_id": first["conversation_id"]}
    ).json()
    assert second["conversation_id"] == first["conversation_id"]

    messages = client.get(f"/api/conversations/{first['conversation_id']}/messages").json()
    assert [m["content"] for m in messages] == ["first", "second"]


def test_conversations_listing_and_not_found(app_client):
    client, _ = app_client
    client.post("/api/chat", json={"message": "hello"})

    listed = client.get("/api/conversations").json()
    assert len(listed) == 1

    assert client.get("/api/conversations/nope").status_code == 404
    assert client.get("/api/executions/nope").status_code == 404
    assert client.delete("/api/conversations/nope").status_code == 404


def test_delete_conversation(app_client):
    client, _ = app_client
    conversation_id = client.post("/api/chat", json={"message": "hi"}).json()["conversation_id"]
    assert client.delete(f"/api/conversations/{conversation_id}").status_code == 200
    assert client.get("/api/conversations").json() == []


def test_stats_endpoint(app_client):
    client, _ = app_client
    body = client.get("/api/stats").json()
    assert body["requests"] == 0
    assert body["total_tokens"] is None


def test_sse_replays_backlog(app_client):
    client, container = app_client
    bus = container.event_bus
    for event in (
        ExecutionEvent(type="execution_started", execution_id="exec-sse", status="running"),
        ExecutionEvent(
            type="agent_started", execution_id="exec-sse", agent="local_extractor", model="MiniCPM5-2B"
        ),
        ExecutionEvent(type="final_answer", execution_id="exec-sse", text="done"),
        ExecutionEvent(type="execution_completed", execution_id="exec-sse", status="completed"),
    ):
        bus.publish(event)

    with client.stream("GET", "/api/events/exec-sse") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        payload = "".join(response.iter_text())

    assert "event: execution_started" in payload
    assert "event: agent_started" in payload
    assert "event: final_answer" in payload
    assert "event: execution_completed" in payload

    frames = [line for line in payload.splitlines() if line.startswith("data: ")]
    parsed = [json.loads(frame[len("data: ") :]) for frame in frames]
    assert parsed[0]["type"] == "execution_started"
    assert parsed[-1]["type"] == "execution_completed"


def test_cancel_endpoint(app_client):
    client, container = app_client
    body = client.post("/api/executions/exec-stub/cancel").json()
    assert body["cancelled"] is True
    assert container.orchestrator.cancelled == ["exec-stub"]
