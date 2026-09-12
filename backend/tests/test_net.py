"""Environment hygiene tests.

A malformed ``NO_PROXY`` (``[::1]`` is the common Windows offender) makes every
HTTP request raise before it is even attempted, so this deserves its own test.
"""

from __future__ import annotations

import os

import pytest

from core.net import host_from_url, sanitize_no_proxy


@pytest.fixture
def restore_no_proxy():
    saved = {var: os.environ.get(var) for var in ("NO_PROXY", "no_proxy")}
    yield
    for var, value in saved.items():
        if value is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = value


def test_bracketed_ipv6_is_removed(restore_no_proxy):
    os.environ["NO_PROXY"] = "localhost,[::1],example.com"
    value = sanitize_no_proxy()
    assert "[::1]" not in value
    assert "localhost" in value
    assert "example.com" in value
    # The usable unbracketed form is re-added, so nothing is actually lost.
    assert "::1" in value


def test_loopback_is_always_present(restore_no_proxy):
    os.environ["NO_PROXY"] = "example.com"
    value = sanitize_no_proxy()
    for required in ("localhost", "127.0.0.1", "127.0.0.1"):
        assert required in value


def test_both_casing_variants_are_written(restore_no_proxy):
    os.environ["NO_PROXY"] = "a.com"
    sanitize_no_proxy()
    assert os.environ["NO_PROXY"] == os.environ["no_proxy"]


def test_duplicates_are_collapsed(restore_no_proxy):
    os.environ["NO_PROXY"] = "localhost,localhost,LOCALHOST"
    value = sanitize_no_proxy()
    assert value.lower().count("localhost") == 1


def test_extra_hosts_are_appended(restore_no_proxy):
    os.environ["NO_PROXY"] = ""
    value = sanitize_no_proxy(["api.deepseek.com"])
    assert "api.deepseek.com" in value


def test_wildcard_is_preserved(restore_no_proxy):
    os.environ["NO_PROXY"] = "*"
    assert sanitize_no_proxy() == "*"


def test_host_from_url():
    assert host_from_url("http://127.0.0.1:8080/v1") == "127.0.0.1"
    assert host_from_url("https://api.deepseek.com/v1") == "api.deepseek.com"
    assert host_from_url("not a url") is None


def test_httpx_client_can_be_constructed(restore_no_proxy):
    """The end-to-end symptom of a bad NO_PROXY is that this raises."""
    import httpx

    os.environ["NO_PROXY"] = "localhost,[::1],::1"
    sanitize_no_proxy()
    with httpx.Client(base_url="http://127.0.0.1:5173", timeout=1.0) as client:
        assert str(client.base_url).startswith("http://127.0.0.1")
