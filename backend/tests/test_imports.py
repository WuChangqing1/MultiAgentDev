"""Import and wiring smoke tests.

These exist because a module-level ``NameError`` (a missing import used only in a
decorator) is invisible to every other test in the suite while making the whole
application fail to start. Importing the real entry point is the cheapest
possible guard against that.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


@pytest.mark.parametrize(
    "module",
    [
        "main",
        "api.chat",
        "api.executions",
        "api.health",
        "api.stats",
        "api.settings",
        "api.container",
        "agents.base",
        "agents.main_agent",
        "agents.local_agent",
        "agents.extractor",
        "agents.summarizer",
        "agents.classifier",
        "agents.reviewer",
        "core.config",
        "core.net",
        "core.prompt_builder",
        "core.logging_setup",
        "db.database",
        "db.models",
        "models.schemas",
        "models.events",
        "models.token_usage",
        "orchestration.orchestrator",
        "orchestration.router",
        "orchestration.state_manager",
        "orchestration.context_manager",
        "orchestration.registry",
        "orchestration.prompt_loader",
        "orchestration.decision_parser",
        "providers.base",
        "providers.deepseek",
        "providers.llama_cpp",
        "services.event_bus",
        "services.token_tracker",
        "services.runtime_state",
        "services.model_health",
    ],
)
def test_module_imports_cleanly(module: str):
    importlib.import_module(module)


def test_app_registers_every_route():
    """The FastAPI app must expose the documented surface."""
    main = importlib.import_module("main")

    # Route discovery is done from the OpenAPI schema rather than by walking
    # `app.routes`: newer Starlette versions wrap included routers in objects
    # that do not expose `.path`, and the schema is the contract clients see.
    paths = set(main.app.openapi().get("paths", {}))
    # `include_in_schema=False` routes still need to exist; check those directly.
    paths |= {
        route.path
        for route in getattr(main.app, "routes", [])
        if isinstance(getattr(route, "path", None), str)
    }

    expected = {
        "/",
        "/api/health",
        "/api/models/status",
        "/api/agents",
        "/api/chat",
        "/api/events/{execution_id}",
        "/api/executions/{execution_id}",
        "/api/executions/{execution_id}/steps",
        "/api/executions/{execution_id}/calls",
        "/api/executions/{execution_id}/cancel",
        "/api/conversations",
        "/api/conversations/{conversation_id}",
        "/api/conversations/{conversation_id}/messages",
        "/api/conversations/{conversation_id}/executions",
        "/api/stats",
        "/api/settings",
        "/api/settings/reset",
        "/api/settings/reload",
        "/api/settings/prompts",
        "/api/settings/validate",
    }
    missing = expected - paths
    assert not missing, f"routes missing from the app: {sorted(missing)}"


def test_app_binds_to_loopback_by_default():
    """A local-first tool must not default to 0.0.0.0 (requirement #45)."""
    from core.config import Settings

    settings = Settings(_env_file=None)
    assert settings.host == "127.0.0.1"
    assert settings.local_model_base_url.startswith("http://127.0.0.1")


def test_prompt_files_all_exist_and_are_non_empty():
    from orchestration.prompt_loader import PromptRepository

    repo = PromptRepository()
    for name in ("_static_prefix", "main_agent", "extractor", "summarizer", "classifier", "reviewer"):
        text = repo.load(name)
        assert text.strip(), f"{name}.md is empty"

    # The cacheable prefix must contain both the static rules and the role file.
    prefix = repo.main_agent_static_prefix()
    assert "CACHE_PREFIX_V1" in prefix
    assert "Main Agent" in prefix


def test_static_prefix_is_byte_stable_across_calls():
    """Prompt-cache friendliness: the prefix must not vary between requests."""
    from orchestration.prompt_loader import PromptRepository

    repo = PromptRepository()
    assert repo.main_agent_static_prefix() == repo.main_agent_static_prefix()


def test_no_api_key_is_hardcoded_in_source():
    """Guard against a real key ever being committed (requirement #55).

    Only shipped source is scanned. ``tests/`` and ``scripts/`` legitimately
    contain key-shaped placeholder strings used to prove redaction works, and
    ``.env`` is git-ignored and is where a key is *supposed* to live.
    """
    import re

    pattern = re.compile(r"sk-[A-Za-z0-9]{20,}")
    skip_dirs = {".tmp", "__pycache__", ".pylibs", "tests", "scripts"}
    offenders: list[str] = []

    for path in BACKEND_DIR.rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if pattern.search(text):
            offenders.append(str(path.relative_to(BACKEND_DIR)))

    assert not offenders, f"possible API key hardcoded in: {offenders}"


def test_env_file_is_git_ignored():
    """The one file allowed to hold a real key must never be committable."""
    import subprocess

    gitignore = BACKEND_DIR.parent / ".gitignore"
    assert gitignore.is_file(), ".gitignore is missing from the project root"
    entries = {line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()}
    assert ".env" in entries

    # `git check-ignore` is authoritative when the repo exists.
    if (BACKEND_DIR.parent / ".git").exists():
        result = subprocess.run(
            ["git", "check-ignore", ".env"],
            cwd=BACKEND_DIR.parent,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, ".env is not ignored by git"
