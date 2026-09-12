"""Ad-hoc debug probe for the JSON repair ladder (not part of the test suite)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestration.decision_parser import parse_decision, repair_json  # noqa: E402

CASES = [
    '{"a": 1, "b": [1, 2]}',
    "{'action':'delegate','agent':'local_extractor','task':'t','reason':None}",
    '{action:"answer",answer:"unquoted keys"}',
    '{"action":"delegate","agent":"local_summarizer","task":"sum it",}',
    '{"action":"answer","answer":"line one\nline two"}',
    '{\n"action":"answer", // because done\n"answer":"ok"\n}',
    '{"action":"answer","answer":"The answer is 42',
    '{"action":"delegate","agent":"local_extractor","task":"extract fields',
    "```json\n{'action':'review','task':'check','context':'{}',}\n```\ntrailing prose",
]

for raw in CASES:
    repaired = repair_json(raw)
    result = parse_decision(raw)
    print(f"raw      : {raw[:66]!r}")
    print(f"repaired : {repaired[:110]!r}")
    print(f"decision : ok={result.ok} strategy={result.strategy} action={result.decision.action} "
          f"agent={result.decision.agent}")
    print("-" * 90)
