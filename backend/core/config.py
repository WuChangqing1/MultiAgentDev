"""Central configuration.

Every tunable lives here. Nothing else in the codebase reads ``os.environ``
directly, which keeps secrets in exactly one place (``.env``) and makes the
runtime-mutable settings (exposed through ``/api/settings``) easy to reason
about.

Two layers exist:

* ``Settings``   -- immutable process-level configuration loaded from ``.env``.
* ``RuntimeOverrides`` -- values the user may change from the Settings UI.

``effective_settings()`` merges both. API keys are NEVER part of the override
layer and are never returned by the API in clear text.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Imported for its side effect: normalizing NO_PROXY before any HTTP client is
# built. See core/net.py for why a malformed system value breaks every request.
from core import net as _net  # noqa: F401

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BACKEND_DIR.parent
PROMPTS_DIR = BACKEND_DIR / "prompts"
ENV_FILE = PROJECT_ROOT / ".env"


# --------------------------------------------------------------------------
# Process settings (.env)
# --------------------------------------------------------------------------


class Settings(BaseSettings):
    """Process-level configuration sourced from ``.env`` + environment."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        # Long-lived objects (providers, agents) hold a reference to one Settings
        # instance. Runtime overrides are applied in place so those references
        # stay valid instead of silently pointing at a stale copy.
        frozen=False,
    )

    # ---- Server -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"

    # ---- Database ---------------------------------------------------------
    database_url: str = f"sqlite+aiosqlite:///{(PROJECT_ROOT / 'data' / 'multiagent.db').as_posix()}"

    # ---- DeepSeek main agent ---------------------------------------------
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    deepseek_reasoner_model: str = "deepseek-reasoner"
    deepseek_timeout_s: float = 300.0
    deepseek_max_retries: int = 2

    # Optional pricing (USD per 1M tokens). Absent -> cost renders as "N/A".
    deepseek_price_input: float | None = None
    deepseek_price_output: float | None = None
    deepseek_price_cached_input: float | None = None

    # ---- Local MiniCPM worker --------------------------------------------
    local_model_base_url: str = "http://127.0.0.1:8080/v1"
    local_model_name: str = "MiniCPM5-2B"
    local_model_api_key: str = "local"
    local_model_timeout_s: float = 180.0
    local_model_max_retries: int = 2
    local_model_context_window: int = 16384

    # ---- Orchestration ----------------------------------------------------
    max_agent_steps: int = 12
    main_agent_max_tokens: int = 4096
    worker_max_tokens: int = 2048
    main_agent_temperature: float = 0.3
    worker_temperature: float = 1.0
    worker_top_p: float = 0.95

    # Per-worker reasoning policy: none | low | medium | high
    reasoning_extractor: str = "none"
    reasoning_summarizer: str = "low"
    reasoning_classifier: str = "none"
    reasoning_reviewer: str = "medium"
    reasoning_coder: str = "low"
    reasoning_main: str = "medium"

    # ---- Feature flags ----------------------------------------------------
    enable_local_workers: bool = True
    show_reasoning: bool = True
    debug_mode: bool = False
    stream_final_answer: bool = True

    @field_validator("deepseek_base_url", "local_model_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    # -- derived helpers ---------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def deepseek_configured(self) -> bool:
        return bool(self.deepseek_api_key.strip())

    def reasoning_for(self, agent_key: str) -> str:
        """Reasoning policy for an agent key.

        Unknown keys get ``"none"`` rather than another agent's policy: a
        newly added worker should be cheap by default and opt in to more
        thinking explicitly (see ``docs/DEVELOPING.md``).
        """
        return {
            "main": self.reasoning_main,
            "local_extractor": self.reasoning_extractor,
            "local_summarizer": self.reasoning_summarizer,
            "local_classifier": self.reasoning_classifier,
            "local_reviewer": self.reasoning_reviewer,
            "local_coder": self.reasoning_coder,
        }.get(agent_key, "none")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    # Make sure the endpoints we actually call are exempt from any system proxy.
    _net.sanitize_for_endpoints([settings.local_model_base_url, settings.deepseek_base_url])
    return settings


# --------------------------------------------------------------------------
# Runtime overrides (Settings UI)
# --------------------------------------------------------------------------

ReasoningEffort = Literal["none", "low", "medium", "high"]


class RuntimeOverrides(BaseModel):
    """Values the user can change at runtime from the Settings panel.

    Secrets are deliberately excluded: the DeepSeek key is only ever read from
    ``.env``.
    """

    deepseek_model: str | None = None
    deepseek_base_url: str | None = None

    local_model_base_url: str | None = None
    local_model_name: str | None = None

    max_agent_steps: int | None = Field(default=None, ge=1, le=64)
    main_agent_max_tokens: int | None = Field(default=None, ge=64, le=65536)
    worker_max_tokens: int | None = Field(default=None, ge=64, le=65536)
    main_agent_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    worker_temperature: float | None = Field(default=None, ge=0.0, le=2.0)

    reasoning_main: ReasoningEffort | None = None
    reasoning_extractor: ReasoningEffort | None = None
    reasoning_summarizer: ReasoningEffort | None = None
    reasoning_classifier: ReasoningEffort | None = None
    reasoning_reviewer: ReasoningEffort | None = None
    reasoning_coder: ReasoningEffort | None = None

    enable_local_workers: bool | None = None
    show_reasoning: bool | None = None
    debug_mode: bool | None = None

    def as_patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class SettingsStore:
    """Holds :class:`Settings` plus the in-memory override layer."""

    def __init__(self) -> None:
        self._base = get_settings()
        #: Snapshot of the .env values, so ``reset`` can restore them.
        self._env_baseline: Settings = self._base.model_copy()
        self._overrides = RuntimeOverrides()

    # -- access ------------------------------------------------------------
    @property
    def base(self) -> Settings:
        return self._base

    @property
    def overrides(self) -> RuntimeOverrides:
        return self._overrides

    def effective(self) -> Settings:
        """``Settings`` with overrides applied, **in place**.

        Applied in place on purpose: providers and agents captured a reference to
        this object at construction time, so returning a copy would leave them
        reading stale configuration.
        """
        for field, value in self._overrides.as_patch().items():
            setattr(self._base, field, value)
        return self._base

    # -- mutation ----------------------------------------------------------
    def update(self, patch: RuntimeOverrides) -> RuntimeOverrides:
        merged = self._overrides.model_dump(exclude_none=True)
        merged.update(patch.as_patch())
        self._overrides = RuntimeOverrides(**merged)
        # Apply immediately so the mutation and its effect cannot drift apart.
        self.effective()
        return self._overrides

    def reset(self) -> RuntimeOverrides:
        """Drop every override and restore the values read from ``.env``."""
        previous = self._overrides.as_patch()
        self._overrides = RuntimeOverrides()
        for field in previous:
            setattr(self._base, field, getattr(self._env_baseline, field))
        return self._overrides

    def reload_from_env(self) -> Settings:
        """Re-read ``.env`` from disk (used by ``POST /api/settings/reload``)."""
        get_settings.cache_clear()
        self._base = get_settings()
        self._env_baseline = self._base.model_copy()
        return self._base


_settings_store: SettingsStore | None = None


def settings_store() -> SettingsStore:
    global _settings_store
    if _settings_store is None:
        _settings_store = SettingsStore()
    return _settings_store


def effective_settings() -> Settings:
    return settings_store().effective()


def ensure_data_dir() -> Path:
    data_dir = PROJECT_ROOT / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def redact(value: str | None) -> str:
    """Render a secret as a fingerprint so it can never leak into logs/UI."""
    if not value:
        return ""
    v = value.strip()
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:4]}{'*' * 8}{v[-4:]}"


def is_secret_env(name: str) -> bool:
    upper = name.upper()
    return any(tok in upper for tok in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


__all__ = [
    "BACKEND_DIR",
    "ENV_FILE",
    "PROMPTS_DIR",
    "PROJECT_ROOT",
    "RuntimeOverrides",
    "Settings",
    "SettingsStore",
    "effective_settings",
    "ensure_data_dir",
    "get_settings",
    "is_secret_env",
    "redact",
    "settings_store",
]
