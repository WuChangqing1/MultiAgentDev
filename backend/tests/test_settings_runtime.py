"""Runtime settings propagation tests.

These guard a subtle failure mode: providers and agents capture a reference to
one ``Settings`` instance at construction time. If a Settings change produced a
*copy*, every long-lived object would silently keep using stale configuration --
the UI would report the new value while requests still went to the old endpoint.
"""

from __future__ import annotations

from core.config import RuntimeOverrides, Settings, settings_store


def test_overrides_are_applied_in_place():
    store = settings_store()
    store.reset()

    original = store.base
    original_url = original.local_model_base_url

    store.update(RuntimeOverrides(local_model_base_url="http://127.0.0.1:9999/v1"))
    effective = store.effective()

    # The same object is returned, so existing references observe the change.
    assert effective is original
    assert effective.local_model_base_url == "http://127.0.0.1:9999/v1"

    store.reset()
    assert store.effective().local_model_base_url == original_url


def test_reset_restores_env_values():
    store = settings_store()
    baseline_steps = store.effective().max_agent_steps

    store.update(RuntimeOverrides(max_agent_steps=3))
    assert store.effective().max_agent_steps == 3

    store.reset()
    assert store.effective().max_agent_steps == baseline_steps


def test_reasoning_lookup_reads_current_values():
    store = settings_store()
    store.reset()
    settings = store.effective()

    store.update(RuntimeOverrides(reasoning_extractor="high"))
    assert settings.reasoning_for("local_extractor") == "high"

    store.update(RuntimeOverrides(reasoning_extractor="none"))
    assert settings.reasoning_for("local_extractor") == "none"

    store.reset()


def test_local_provider_refresh_drops_stale_client():
    """A changed endpoint must invalidate the cached HTTP client."""
    from providers.llama_cpp import LlamaCppProvider

    settings = Settings(local_model_base_url="http://127.0.0.1:8080/v1", local_model_api_key="local")
    provider = LlamaCppProvider(settings)

    # Force a client to exist.
    client = provider._get_client()  # noqa: SLF001 - white-box on purpose
    assert provider._client_signature is not None  # noqa: SLF001

    settings.local_model_base_url = "http://127.0.0.1:9999/v1"
    provider.refresh()

    assert provider._client is None  # noqa: SLF001
    assert provider.base_url == "http://127.0.0.1:9999/v1"
    assert provider._get_client() is not client  # noqa: SLF001


def test_deepseek_provider_refresh_drops_stale_client():
    from providers.deepseek import DeepSeekProvider

    settings = Settings(deepseek_api_key="test-key", deepseek_base_url="https://api.deepseek.com/v1")
    provider = DeepSeekProvider(settings)

    provider._get_client()  # noqa: SLF001
    settings.deepseek_base_url = "https://example.invalid/v1"
    provider.refresh()

    assert provider._client is None  # noqa: SLF001


def test_settings_never_expose_the_api_key_verbatim():
    """`redact` is what the Settings API uses; it must not echo the key."""
    from core.config import redact

    key = "sk-abcdefghijklmnopqrstuvwxyz012345"
    masked = redact(key)
    assert key not in masked
    assert masked.startswith("sk-a")
    assert "*" in masked
    assert redact("") == ""
    assert redact("short") == "*****"
