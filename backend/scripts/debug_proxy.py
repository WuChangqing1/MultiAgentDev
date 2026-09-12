"""Probe NO_PROXY handling (development aid, not shipped behaviour)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

print("before NO_PROXY =", repr(os.environ.get("NO_PROXY")))
print("before no_proxy =", repr(os.environ.get("no_proxy")))

import core.config  # noqa: E402

print("after  NO_PROXY =", repr(os.environ.get("NO_PROXY")))
print("after  no_proxy =", repr(os.environ.get("no_proxy")))
print("HTTP_PROXY      =", repr(os.environ.get("HTTP_PROXY")))
print("HTTPS_PROXY     =", repr(os.environ.get("HTTPS_PROXY")))

try:
    import httpx

    with httpx.Client(base_url="http://127.0.0.1:5173", timeout=5.0) as client:
        print("httpx client constructed OK")
except Exception as exc:  # noqa: BLE001
    print(f"httpx client FAILED: {type(exc).__name__}: {exc}")
