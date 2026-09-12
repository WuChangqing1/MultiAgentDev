"""Verify fallback behaviour on a live backend.

Requirement #50: when the local worker is unavailable the application must stay
up, report it, and let DeepSeek finish the job.

Two independent mechanisms are exercised, because they cover different failures:

1. ``enable_local_workers = false`` -- an operator decision.
2. A health-check override that reports the local model as offline -- a runtime
   outage (equivalent to ``llama-server`` being stopped).

The second is simulated by pointing the provider at a dead port, which produces
a real connection refusal rather than a mocked error.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core.config  # noqa: E402,F401

BASE = "http://127.0.0.1:8000"
DEAD_ENDPOINT = "http://127.0.0.1:9/v1"  # discard port: refuses instantly
PROMPT = "请分析下面这段内容，提取关键信息并总结。\n张三今年20岁，是软件工程专业学生。"


def run_chat(client: httpx.Client, label: str) -> dict:
    print(f"\n--- {label} ---")
    response = client.post("/api/chat", json={"message": PROMPT})
    if response.status_code != 202:
        print(f"  FAILED to start: {response.status_code} {response.text[:200]}")
        return {"status": "error"}

    execution_id = response.json()["execution_id"]
    notices: list[str] = []
    agents: list[str] = []
    deadline = time.time() + 180

    with client.stream("GET", f"/api/events/{execution_id}", timeout=200.0) as stream:
        buffer = ""
        for chunk in stream.iter_text():
            buffer += chunk
            while "\n\n" in buffer:
                frame, buffer = buffer.split("\n\n", 1)
                data = [line[5:].strip() for line in frame.split("\n") if line.startswith("data:")]
                if not data:
                    continue
                event = json.loads("".join(data))
                if event["type"] == "agent_started":
                    label_ = event.get("agent_label") or event.get("agent")
                    agents.append(label_)
                    print(f"  [agent_started] {label_}")
                elif event["type"] == "notice":
                    notices.append(event.get("message") or "")
                    print(f"  [notice/{event.get('level')}] {event.get('message')}")
                elif event["type"] == "final_answer":
                    print(f"  [final_answer] {(event.get('text') or '')[:110]}...")
            if "execution_completed" in buffer or "execution_failed" in buffer:
                pass
            if any(term in buffer for term in ("execution_completed", "execution_failed")):
                break
            if time.time() > deadline:
                print("  TIMEOUT")
                break

    execution = client.get(f"/api/executions/{execution_id}").json()
    print(f"  status={execution['status']} agents={agents}")
    return {
        "status": execution["status"],
        "agents": agents,
        "notices": notices,
        "answer": execution.get("final_answer") or "",
    }


def main() -> int:
    failures: list[str] = []

    with httpx.Client(base_url=BASE, timeout=60.0) as client:
        print("=== Baseline: local worker enabled ===")
        baseline = run_chat(client, "local online")
        if baseline["status"] != "completed":
            failures.append("baseline run did not complete")
        if "LocalExtractorAgent" not in baseline["agents"]:
            print("  (note: the planner chose not to delegate in this run)")

        print("\n=== Fallback A: local workers disabled ===")
        client.patch("/api/settings", json={"enable_local_workers": False})
        disabled = run_chat(client, "enable_local_workers=false")
        if disabled["status"] != "completed":
            failures.append("run with workers disabled did not complete")
        if any(name.startswith("Local") for name in disabled["agents"]):
            failures.append("a local worker ran while local workers were disabled")
        if disabled["answer"].strip() == "":
            failures.append("no answer produced with workers disabled")
        client.post("/api/settings/reset")

        print("\n=== Fallback B: local endpoint unreachable (simulated outage) ===")
        client.patch("/api/settings", json={"local_model_base_url": DEAD_ENDPOINT})
        client.post("/api/settings/validate")
        offline = run_chat(client, "local endpoint dead")
        if offline["status"] != "completed":
            failures.append("run with the local model offline did not complete")
        if any(name.startswith("Local") for name in offline["agents"]):
            failures.append("a local worker ran while the endpoint was unreachable")
        if offline["answer"].strip() == "":
            failures.append("no answer produced with the local model offline")
        # The user must be told, not left guessing.
        if not any("offline" in n.lower() or "unavailable" in n.lower() for n in offline["notices"]):
            failures.append("no user-facing notice about the unavailable local model")

        client.post("/api/settings/reset")

        print("\n=== Backend still healthy after both fallbacks ===")
        health = client.get("/api/health").json()
        print(f"  backend={health['backend']} minicpm={health['minicpm']} database={health['database']}")
        if health["backend"] != "online":
            failures.append("backend reported unhealthy after fallbacks")

    print("\n" + "=" * 70)
    if failures:
        for item in failures:
            print(f"FAIL: {item}")
        return 1
    print("RESULT: OK — fallbacks behave correctly and the app stayed up")
    return 0


if __name__ == "__main__":
    sys.exit(main())
