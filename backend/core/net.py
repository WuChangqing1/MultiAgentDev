"""Environment hygiene, applied before any HTTP client is constructed.

Why this exists
---------------
On Windows, ``NO_PROXY`` from the system environment frequently contains
``[::1]``. Some HTTP stacks split the list naively on ``:`` and then fail to
parse the port, raising ``InvalidURL: Invalid port: ':1]'`` for *every* request
-- including requests to localhost, which are exactly the ones the entry was
meant to exempt.

This module rewrites the no-proxy list into an unambiguous form and guarantees
that the local model endpoint and the backend itself are always exempt. It is
imported by :mod:`core.config`, so importing the configuration is enough to make
the fix effective.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

_NO_PROXY_VARS = ("NO_PROXY", "no_proxy")

#: Always exempt: loopback in every spelling the OS might use. Bracketed IPv6
#: forms are deliberately absent -- they are what breaks URL parsing.
_ALWAYS_EXEMPT = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _normalize_token(token: str) -> str | None:
    """Drop entries that break URL parsing; keep the usable ones.

    ``[::1]`` is the specific offender: the HTTP stack splits ``NO_PROXY`` on
    commas and then parses each entry as a URL pattern, where the bracketed form
    is read as host ``[`` with port ``:1]``. The unbracketed ``::1`` equivalent
    is added back by :data:`_ALWAYS_EXEMPT`, so nothing is actually lost.
    """
    candidate = token.strip()
    if not candidate:
        return None
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    if candidate == "*":
        return candidate
    if any(ch in candidate for ch in "[]@#?"):
        return None
    return candidate


def sanitize_no_proxy(extra_hosts: list[str] | None = None) -> str:
    """Rewrite ``NO_PROXY`` / ``no_proxy`` into a parseable form.

    Returns the value that was written, for logging and tests.
    """
    existing: list[str] = []
    for var in _NO_PROXY_VARS:
        raw = os.environ.get(var)
        if raw:
            existing.extend(raw.split(","))

    # "*" already exempts everything; appending hosts would only add noise.
    if any(token.strip() == "*" for token in existing):
        for var in _NO_PROXY_VARS:
            os.environ[var] = "*"
        return "*"

    tokens: list[str] = []
    seen: set[str] = set()
    for token in [*existing, *list(extra_hosts or []), *_ALWAYS_EXEMPT]:
        normalized = _normalize_token(token)
        if normalized and normalized.lower() not in seen:
            seen.add(normalized.lower())
            tokens.append(normalized)

    value = ",".join(tokens)
    for var in _NO_PROXY_VARS:
        os.environ[var] = value
    return value


def host_from_url(url: str) -> str | None:
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def sanitize_for_endpoints(urls: list[str]) -> str:
    """Sanitize, exempting the host of every endpoint we talk to."""
    hosts = [host for host in (host_from_url(u) for u in urls) if host]
    return sanitize_no_proxy(hosts)


# Applied at import time on purpose: HTTP clients read NO_PROXY when they are
# constructed, so patching it from a later call site would already be too late.
sanitize_no_proxy()


__all__ = ["host_from_url", "sanitize_for_endpoints", "sanitize_no_proxy"]
