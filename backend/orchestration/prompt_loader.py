"""Prompt loading.

Prompts live in ``backend/prompts/*.md`` so they can be edited without touching
Python (requirement: no hardcoded prompts). Files are cached in memory and
reloaded when their mtime changes, which keeps the prompt-cache prefix stable
during a run while still allowing live edits.

Layout of a MainAgent prompt::

    [ static prefix (cached) ][ role file ][ TASK/CONTEXT ][ INTERNAL AGENT STATE ]

The first two segments are byte-identical across requests, which is what makes
them cheap under provider-side prompt caching.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from core.config import PROMPTS_DIR

log = logging.getLogger(__name__)


class PromptNotFoundError(FileNotFoundError):
    pass


@dataclass
class PromptFile:
    name: str
    path: Path
    text: str
    mtime: float
    char_count: int


class PromptRepository:
    """mtime-validated cache over the prompts directory."""

    def __init__(self, directory: Path | None = None) -> None:
        self._dir = directory or PROMPTS_DIR
        self._cache: dict[str, PromptFile] = {}
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._dir

    def load(self, name: str) -> str:
        """Return the prompt text for ``name`` (without the ``.md`` suffix)."""
        path = self._resolve(name)
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            raise PromptNotFoundError(f"Prompt file unreadable: {path}") from exc

        with self._lock:
            cached = self._cache.get(name)
            if cached is not None and cached.mtime == mtime and cached.path == path:
                return cached.text
            text = path.read_text(encoding="utf-8")
            self._cache[name] = PromptFile(
                name=name, path=path, text=text, mtime=mtime, char_count=len(text)
            )
            log.debug("prompt_loaded name=%s chars=%d", name, len(text))
            return text

    def _resolve(self, name: str) -> Path:
        candidate = self._dir / (name if name.endswith(".md") else f"{name}.md")
        if not candidate.is_file():
            raise PromptNotFoundError(
                f"Prompt {name!r} not found in {self._dir}. Expected {candidate.name}."
            )
        return candidate

    # -- composition -------------------------------------------------------
    def main_agent_static_prefix(self) -> str:
        """Cacheable prefix: static rules + role definition, concatenated once."""
        return f"{self.load('_static_prefix').strip()}\n\n---\n\n{self.load('main_agent').strip()}"

    def worker_system_prompt(self, agent_key: str) -> str:
        """Worker system prompt (short by design — small models need focus)."""
        return self.load(agent_key).strip()

    def describe(self) -> dict[str, int]:
        """Char counts per prompt file, surfaced in the Settings panel."""
        out: dict[str, int] = {}
        for path in sorted(self._dir.glob("*.md")):
            try:
                out[path.stem] = len(path.read_text(encoding="utf-8"))
            except OSError:  # pragma: no cover
                continue
        return out

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()


__all__ = ["PromptNotFoundError", "PromptRepository"]
