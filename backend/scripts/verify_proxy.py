"""Verify the browser-facing path exactly as the frontend uses it.

Talks to the *Vite dev server* (port 5173), which proxies /api to FastAPI. That
proves the whole chain the browser depends on:

    browser -> vite proxy -> FastAPI -> DeepSeek / llama.cpp -> SSE back

Run with both servers up:  python scripts/verify_proxy.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import httpx

# Importing the config normalizes NO_PROXY (see core/net.py) before httpx reads
# it. Without this, a malformed system NO_PROXY breaks every request.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import core.config  # noqa: E402,F401

BASE = "http://127.0.0.1:5173"
PROMPT = "请分析下面这段内容，提取关键信息并总结。\n张三今年20岁，是软件工程专业学生，目前正在学习大模型和Agent开发。"


def main() -> int:
    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        print("1. GET /api/health through the dev proxy")
        health = client.get("/api/health").json()
        print(f"   backend={health['backend']} deepseek={health['deepseek']} minicpm={health['minicpm']}")

        print("\n2. GET /api/agents through the dev proxy")
        for agent in client.get("/api/agents").json():
            flag = "local" if agent["is_local"] else "cloud"
            print(f"   {agent['key']:<18} {agent['model']:<14} {flag:<6} available={agent['available']}")

        print("\n3. POST /api/chat through the dev proxy")
        started = time.perf_counter()
        response = client.post("/api/chat", json={"message": PROMPT})
        if response.status_code != 202:
            print(f"   FAILED: {response.status_code} {response.text[:300]}")
            return 1
        payload = response.json()
        execution_id = payload["execution_id"]
        print(f"   accepted in {(time.perf_counter() - started) * 1000:.0f} ms")
        print(f"   execution_id={execution_id}")
        print(f"   stream_url={payload['stream_url']}")

        print("\n4. Stream /api/events/{id} (SSE) through the dev proxy")
        counts: dict[str, int] = {}
        agents_seen: list[str] = []
        final_answer = ""
        deadline = time.time() + 180

        with client.stream("GET", f"/api/events/{execution_id}", timeout=200.0) as stream:
            if stream.status_code != 200:
                print(f"   FAILED: stream returned {stream.status_code}")
                return 1
            print(f"   content-type={stream.headers.get('content-type')}")

            buffer = ""
            for chunk in stream.iter_text():
                buffer += chunk
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    data = [
                        line[5:].strip()
                        for line in frame.split("\n")
                        if line.startswith("data:")
                    ]
                    if not data:
                        continue
                    event = json.loads("".join(data))
                    kind = event["type"]
                    counts[kind] = counts.get(kind, 0) + 1

                    if kind == "agent_started":
                        who = event.get("agent_label") or event.get("agent")
                        agents_seen.append(who)
                        print(f"   [{kind}] {who} stage={event.get('stage')}")
                    elif kind == "decision":
                        data_block = event.get("data") or {}
                        print(
                            f"   [decision] action={data_block.get('action')} "
                            f"agent={data_block.get('agent')} parse_ok={data_block.get('parse_ok')}"
                        )
                    elif kind == "token_usage":
                        usage = event.get("usage") or {}
                        total = (event.get("data") or {}).get("execution_totals") or {}
                        print(
                            f"   [token_usage] {event.get('agent')}: "
                            f"prompt={usage.get('prompt_tokens')} "
                            f"completion={usage.get('completion_tokens')} "
                            f"reasoning={usage.get('reasoning_tokens')} "
                            f"latency={usage.get('latency_ms')}ms "
                            f"running_total={total.get('total_tokens')}"
                        )
                    elif kind in ("agent_failed", "notice"):
                        print(f"   [{kind}] {event.get('message')}")
                    elif kind == "final_answer":
                        final_answer = event.get("text") or ""

                if counts.get("execution_completed") or counts.get("execution_failed"):
                    break
                if time.time() > deadline:
                    print("   TIMEOUT waiting for a terminal event")
                    return 1

        print(f"\n5. Event counts: {json.dumps(counts, sort_keys=True)}")
        print(f"   agent order: {' -> '.join(agents_seen)}")

        print("\n6. GET /api/executions/{id} (persistence)")
        execution = client.get(f"/api/executions/{execution_id}").json()
        print(f"   status={execution['status']} steps={len(execution['steps'])}")
        for step in execution["steps"]:
            usage = step.get("usage") or {}
            print(
                f"     #{step['step_index']} {step['agent_name']:<18} {step['status']:<10} "
                f"model={step.get('model')} tokens={usage.get('total_tokens')}"
            )

        print("\n7. Final answer as the browser would render it")
        answer = execution.get("final_answer") or final_answer
        for line in (answer or "").splitlines()[:14]:
            print(f"   | {line}")

        print("\n8. GET /api/stats (session telemetry)")
        stats = client.get("/api/stats").json()
        print(
            f"   requests={stats['requests']} agent_calls={stats['agent_calls']} "
            f"cloud={stats['cloud_tokens']} local={stats['local_tokens']} "
            f"cost={stats['cost_usd']}"
        )

        if execution["status"] != "completed":
            print("\nRESULT: FAILED — execution did not complete")
            return 1
        print("\nRESULT: OK — browser path verified end to end")
        return 0


if __name__ == "__main__":
    sys.exit(main())
