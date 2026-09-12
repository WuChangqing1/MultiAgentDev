"""Shared pytest fixtures.

Every test here runs without network access and without a live model: the
providers are faked, so the suite is deterministic and costs nothing.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

#: Workspace-local scratch space. pytest's default temp root is redirected by the
#: DSH sandbox to a read-only location, and SQLite needs a path it can create.
TMP_ROOT = BACKEND_DIR / ".tmp"
_tmp_counter = {"n": 0}


@pytest.fixture
def tmp_path() -> Iterator[Path]:
    """Workspace-local replacement for pytest's ``tmp_path``.

    Overridden globally because creating files under the platform temp root is
    denied in this environment.
    """
    _tmp_counter["n"] += 1
    directory = TMP_ROOT / f"t{_tmp_counter['n']:04d}"
    directory.mkdir(parents=True, exist_ok=True)
    yield directory
    shutil.rmtree(directory, ignore_errors=True)

from models.schemas import AgentVisibleState  # noqa: E402
from models.token_usage import TokenUsage, utcnow  # noqa: E402
from orchestration.prompt_loader import PromptRepository  # noqa: E402
from providers.base import (  # noqa: E402
    GenerateOptions,
    LLMMessage,
    LLMProvider,
    LLMResponse,
    ProviderUnavailable,
)

# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


class ScriptedProvider(LLMProvider):
    """Replays a fixed list of responses, then repeats the last one.

    Records every prompt it was given so tests can assert on prompt structure --
    which is how the telemetry-isolation invariant is verified.
    """

    def __init__(
        self,
        responses: list[LLMResponse],
        *,
        name: str = "fake",
        model: str = "fake-model",
        is_local: bool = False,
    ) -> None:
        self._responses = responses
        self._index = 0
        self.name = name
        self.is_local = is_local
        self._model = model
        self.prompts: list[list[LLMMessage]] = []
        self.options: list[GenerateOptions] = []
        #: ``(response_index, content_prefix)`` per call, for failure diagnostics.
        self.calls: list[tuple[int, str]] = []
        #: Content prefixes served through the streaming path.
        self.streams: list[str] = []

    @property
    def model(self) -> str:
        return self._model

    def _next(self) -> LLMResponse:
        if not self._responses:
            raise ProviderUnavailable("no scripted responses", provider=self.name)
        index = min(self._index, len(self._responses) - 1)
        response = self._responses[index]
        self._index += 1
        # Trace so a failing test can show exactly which scripted response was
        # consumed by which call (see ScriptedProvider.calls).
        self.calls.append((index, response.content[:60]))
        return response

    async def generate(
        self, messages: list[LLMMessage], options: GenerateOptions | None = None
    ) -> LLMResponse:
        self.prompts.append(list(messages))
        self.options.append(options or GenerateOptions())
        return self._next()

    async def stream(self, messages: list[LLMMessage], options: GenerateOptions | None = None):
        self.prompts.append(list(messages))
        self.options.append(options or GenerateOptions())
        response = self._next()
        self.streams.append(response.content[:40])
        yield response.content, response.reasoning

    async def health_check(self) -> dict:
        return {"status": "online", "detail": "fake", "models": [self._model]}


class OfflineProvider(LLMProvider):
    """Always fails the way an unreachable llama-server does."""

    name = "llamacpp"
    is_local = True

    def __init__(self, model: str = "MiniCPM5-2B") -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    async def generate(self, messages: list[LLMMessage], options: GenerateOptions | None = None):
        raise ProviderUnavailable("Local model is offline at http://127.0.0.1:8080/v1.", provider=self.name)

    async def stream(self, messages: list[LLMMessage], options: GenerateOptions | None = None):
        raise ProviderUnavailable("Local model is offline.", provider=self.name)
        yield "", None  # pragma: no cover - makes this an async generator

    async def health_check(self) -> dict:
        return {"status": "offline", "detail": "connection refused", "models": []}


def make_response(
    content: str,
    *,
    reasoning: str | None = None,
    model: str = "fake-model",
    provider: str = "fake",
    is_local: bool = False,
    prompt_tokens: int | None = 100,
    completion_tokens: int | None = 20,
    reasoning_tokens: int | None = None,
) -> LLMResponse:
    usage = TokenUsage(
        model=model,
        provider=provider,
        is_local=is_local,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=(
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else None
        ),
        reasoning_tokens=reasoning_tokens,
        reasoning_tokens_source="reported" if reasoning_tokens is not None else "unavailable",
        prompt_tokens_source="reported" if prompt_tokens is not None else "unavailable",
        completion_tokens_source="reported" if completion_tokens is not None else "unavailable",
        request_start_time=utcnow(),
        request_end_time=utcnow(),
    ).finalize()
    return LLMResponse(
        content=content,
        reasoning=reasoning,
        model=model,
        provider=provider,
        is_local=is_local,
        usage=usage,
    )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def prompts() -> PromptRepository:
    return PromptRepository()


@pytest.fixture
def sample_state() -> AgentVisibleState:
    return AgentVisibleState(
        user_goal="Extract the person's details.",
        current_stage="planning",
        completed_steps=["main: planned"],
        available_agents=["main", "local_extractor"],
        important_results={"local_extractor": '{"name": "张三"}'},
        errors=[],
        next_action_hint="finish",
        steps_remaining=5,
        step_index=1,
        local_workers_available=True,
    )


@pytest.fixture
def scripted() -> Iterator[ScriptedProvider]:
    yield ScriptedProvider([make_response('{"action":"answer","answer":"ok"}')])


@pytest.fixture
async def temp_db(tmp_path: Path) -> AsyncIterator["object"]:
    """A real SQLite database in a temp directory."""
    from core.config import Settings
    from db.database import Database

    settings = Settings(database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}")
    database = Database(settings)
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()
