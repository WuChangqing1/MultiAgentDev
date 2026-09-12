"""DeepSeek provider tests.

Focus: how reasoning control is decided. The original implementation keyed off
the substring "reasoner" in the model name, which silently disabled reasoning
control for accounts whose reasoning models are named `deepseek-flash` or
`deepseek-v4-pro`. These tests pin the behaviour that replaced it.
"""

from __future__ import annotations

import pytest

from core.config import Settings
from providers.base import GenerateOptions, LLMMessage
from providers.deepseek import DeepSeekProvider
from tests.conftest import make_response


def provider_for(model: str, **overrides) -> DeepSeekProvider:
    settings = Settings(
        deepseek_api_key="test-key",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model=model,
        **overrides,
    )
    return DeepSeekProvider(settings)


def options(effort: str) -> GenerateOptions:
    return GenerateOptions(max_tokens=64, temperature=0.0, reasoning_effort=effort)


# --------------------------------------------------------------------------
# What gets sent
# --------------------------------------------------------------------------


def test_effort_is_not_sent_before_support_is_known():
    """Unprobed models must not receive an unverified parameter.

    Sending it blind risks a 400 on deployments that do not accept it.
    """
    provider = provider_for("deepseek-flash")
    kwargs = provider._build_kwargs(  # noqa: SLF001
        [LLMMessage(role="user", content="hi")], options("high"), stream=False
    )
    assert "reasoning_effort" not in kwargs


def test_effort_is_sent_for_classic_reasoner_names():
    """The historical naming convention still short-circuits the probe."""
    provider = provider_for("deepseek-reasoner")
    kwargs = provider._build_kwargs(  # noqa: SLF001
        [LLMMessage(role="user", content="hi")], options("high"), stream=False
    )
    assert kwargs["reasoning_effort"] == "high"


def test_effort_is_sent_once_support_is_learned():
    """The fix: a probed-as-supported model does receive the parameter."""
    provider = provider_for("deepseek-flash")
    provider._reasoning_support["deepseek-flash"] = True  # noqa: SLF001
    kwargs = provider._build_kwargs(  # noqa: SLF001
        [LLMMessage(role="user", content="hi")], options("medium"), stream=False
    )
    assert kwargs["reasoning_effort"] == "medium"


@pytest.mark.parametrize(
    ("effort", "expected"),
    [("none", None), ("low", "low"), ("medium", "medium"), ("high", "high")],
)
def test_effort_levels_map_through(effort: str, expected: str | None):
    provider = provider_for("deepseek-reasoner")
    kwargs = provider._build_kwargs(  # noqa: SLF001
        [LLMMessage(role="user", content="hi")], options(effort), stream=False
    )
    if expected is None:
        assert "reasoning_effort" not in kwargs
    else:
        assert kwargs["reasoning_effort"] == expected


def test_native_reasoning_model_is_not_sent_an_effort():
    """A model that already reports reasoning_tokens must be left alone."""
    provider = provider_for("deepseek-flash")
    provider._reasoning_support["deepseek-flash"] = False  # noqa: SLF001
    kwargs = provider._build_kwargs(  # noqa: SLF001
        [LLMMessage(role="user", content="hi")], options("high"), stream=False
    )
    assert "reasoning_effort" not in kwargs


# --------------------------------------------------------------------------
# Cache invalidation and reporting
# --------------------------------------------------------------------------


def test_changing_model_drops_cached_support():
    """Support is per model; a stale entry would send the wrong control."""
    provider = provider_for("deepseek-flash")
    provider._reasoning_support["deepseek-v4-pro"] = True  # noqa: SLF001
    provider.set_model("deepseek-v4-pro")
    assert "deepseek-v4-pro" not in provider._reasoning_support  # noqa: SLF001


def test_reasoning_mode_reporting():
    provider = provider_for("deepseek-flash")
    assert provider.reasoning_mode == "unprobed"

    provider._reasoning_support["deepseek-flash"] = True  # noqa: SLF001
    assert provider.reasoning_mode == "effort-controlled"

    provider._reasoning_support["deepseek-flash"] = False  # noqa: SLF001
    assert provider.reasoning_mode == "model-native"

    assert provider_for("deepseek-reasoner").reasoning_mode == "effort-controlled"


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_detects_a_native_reasoning_model():
    """A model returning reasoning_content is classified as native, no parameter."""
    provider = provider_for("deepseek-flash")

    async def fake_generate(messages, opts=None):
        return make_response("42", reasoning="thinking out loud")

    provider.generate = fake_generate  # type: ignore[method-assign]
    assert await provider.detect_reasoning_support() is False
    assert provider.reasoning_mode == "model-native"


@pytest.mark.asyncio
async def test_probe_accepts_a_model_that_takes_the_parameter():
    provider = provider_for("deepseek-chat")

    async def fake_generate(messages, opts=None):
        return make_response("42")  # no reasoning -> not native

    class FakeCompletions:
        async def create(self, **kwargs):
            assert kwargs["reasoning_effort"] == "low"
            return object()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    provider.generate = fake_generate  # type: ignore[method-assign]
    provider._get_client = lambda: FakeClient()  # type: ignore[method-assign]  # noqa: SLF001

    assert await provider.detect_reasoning_support() is True
    assert provider.reasoning_mode == "effort-controlled"


@pytest.mark.asyncio
async def test_probe_treats_a_rejection_as_unsupported():
    provider = provider_for("deepseek-chat")

    async def fake_generate(messages, opts=None):
        return make_response("42")

    class FakeCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("Unsupported parameter: reasoning_effort")

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    provider.generate = fake_generate  # type: ignore[method-assign]
    provider._get_client = lambda: FakeClient()  # type: ignore[method-assign]  # noqa: SLF001

    assert await provider.detect_reasoning_support() is False


@pytest.mark.asyncio
async def test_probe_never_raises():
    """A failed probe must not break anything -- fall back to sending nothing."""
    provider = provider_for("deepseek-chat")

    async def exploding_generate(messages, opts=None):
        raise RuntimeError("network down")

    provider.generate = exploding_generate  # type: ignore[method-assign]
    assert await provider.detect_reasoning_support() is False


@pytest.mark.asyncio
async def test_probe_is_cached():
    """Detection costs a call, so it must happen at most once per model."""
    provider = provider_for("deepseek-flash")
    calls = {"n": 0}

    async def counting_generate(messages, opts=None):
        calls["n"] += 1
        return make_response("42", reasoning="native")

    provider.generate = counting_generate  # type: ignore[method-assign]
    await provider.detect_reasoning_support()
    await provider.detect_reasoning_support()
    await provider.detect_reasoning_support()
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_probe_returns_false_without_an_api_key():
    settings = Settings(deepseek_api_key="", deepseek_model="deepseek-chat")
    provider = DeepSeekProvider(settings)
    assert await provider.detect_reasoning_support() is False


# --------------------------------------------------------------------------
# The removed config must stay removed
# --------------------------------------------------------------------------


def test_legacy_reasoner_model_setting_is_gone():
    """It was dead config: declared, documented, and never read.

    Leaving it in place implies switching models requires editing it, which is
    not true and would mislead.
    """
    assert "deepseek_reasoner_model" not in Settings.model_fields
