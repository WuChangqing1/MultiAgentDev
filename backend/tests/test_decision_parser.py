"""Decision parser tests.

The parser sits between an unreliable model and the whole runtime, so it is
tested against the failure modes actually observed from small and large models.
"""

from __future__ import annotations

import pytest

from orchestration.decision_parser import (
    complete_truncated_object,
    extract_balanced_object,
    normalize_decision,
    parse_decision,
    repair_json,
    strip_code_fence,
)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


def test_parses_clean_delegate_json():
    raw = (
        '{"action":"delegate","agent":"local_extractor",'
        '"task":"Extract name and age","reason":"simple extraction"}'
    )
    result = parse_decision(raw)
    assert result.ok
    assert result.strategy == "strict"
    assert result.decision.action == "delegate"
    assert result.decision.agent == "local_extractor"
    assert result.decision.task == "Extract name and age"
    assert result.decision.reason == "simple extraction"


def test_parses_answer_action():
    result = parse_decision('{"action":"answer","answer":"张三，20 岁。"}')
    assert result.ok
    assert result.decision.action == "answer"
    assert result.decision.answer == "张三，20 岁。"


@pytest.mark.parametrize("action", ["delegate", "answer", "continue", "review", "replan"])
def test_all_documented_actions_are_accepted(action):
    payload = {
        "delegate": '{"action":"delegate","agent":"local_extractor","task":"t"}',
        "answer": '{"action":"answer","answer":"a"}',
        "continue": '{"action":"continue","task":"think more"}',
        "review": '{"action":"review","task":"check this","context":"{}"}',
        "replan": '{"action":"replan","reason":"the plan failed"}',
    }[action]
    result = parse_decision(payload)
    assert result.ok, result.error
    assert result.decision.action == action


# --------------------------------------------------------------------------
# Recovery ladder
# --------------------------------------------------------------------------


def test_strips_json_code_fence():
    raw = '```json\n{"action":"answer","answer":"fenced"}\n```'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.answer == "fenced"


def test_recovers_object_wrapped_in_prose():
    raw = 'Sure, here is my decision:\n{"action":"answer","answer":"done"}\nLet me know!'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.answer == "done"
    assert result.strategy in {"balanced_scan", "repair"}


def test_repairs_raw_newlines_inside_strings():
    raw = '{"action":"answer","answer":"line one\nline two"}'
    result = parse_decision(raw)
    assert result.ok
    assert "line one" in result.decision.answer
    assert "line two" in result.decision.answer


def test_repairs_trailing_commas():
    raw = '{"action":"delegate","agent":"local_summarizer","task":"sum it",}'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.agent == "local_summarizer"


def test_repairs_single_quotes_and_python_literals():
    raw = "{'action':'delegate','agent':'local_extractor','task':'t','reason':None}"
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.agent == "local_extractor"
    assert result.decision.reason is None


def test_repairs_unquoted_keys():
    raw = '{action:"answer",answer:"unquoted keys"}'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.answer == "unquoted keys"


def test_ignores_json_line_comments():
    raw = '{\n"action":"answer", // chosen because done\n"answer":"ok"\n}'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.answer == "ok"


def test_repairs_fenced_json_with_trailing_prose_and_comma():
    raw = '```json\n{"action":"delegate","agent":"local_reviewer","task":"verify",}\n```\nHope that helps.'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.action == "delegate"


# --------------------------------------------------------------------------
# Truncation
# --------------------------------------------------------------------------


def test_completes_truncated_object():
    raw = '{"action":"answer","answer":"The answer is 42'
    result = parse_decision(raw)
    assert result.ok, result.error
    assert result.decision.action == "answer"
    assert "42" in (result.decision.answer or "")


def test_completes_truncated_delegate():
    raw = '{"action":"delegate","agent":"local_extractor","task":"extract fields'
    result = parse_decision(raw)
    assert result.ok, result.error
    assert result.decision.action == "delegate"
    assert "extract fields" in (result.decision.task or "")


def test_complete_truncated_object_returns_none_for_balanced_input():
    assert complete_truncated_object('{"a": 1}') == '{"a": 1}'


def test_extract_balanced_object_ignores_braces_inside_strings():
    raw = 'prefix {"a":"} not the end {","b":2} suffix'
    assert extract_balanced_object(raw) == '{"a":"} not the end {","b":2}'


# --------------------------------------------------------------------------
# Shape normalization (protocol drift)
# --------------------------------------------------------------------------


def test_infers_delegate_when_action_missing():
    raw = '{"agent":"local_extractor","task":"Extract the name"}'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.action == "delegate"
    assert result.decision.agent == "local_extractor"


def test_infers_answer_when_action_missing():
    result = parse_decision('{"answer":"Just the answer."}')
    assert result.ok
    assert result.decision.action == "answer"


def test_uses_first_step_of_a_plan_array():
    raw = (
        '{"plan":[{"agent":"local_extractor","task":"Extract name"},'
        '{"agent":"local_summarizer","task":"Summarise"}]}'
    )
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.agent == "local_extractor"
    assert result.decision.task == "Extract name"


def test_unwraps_nested_decision_object():
    raw = '{"decision":{"action":"answer","answer":"nested"}}'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.answer == "nested"


def test_synonym_keys_are_understood():
    raw = '{"type":"delegate","worker":"local_classifier","instruction":"Label this"}'
    result = parse_decision(raw)
    assert result.ok
    assert result.decision.action == "delegate"
    assert result.decision.agent == "local_classifier"
    assert result.decision.task == "Label this"


def test_normalize_decision_returns_none_for_unknown_shape():
    assert normalize_decision({"foo": "bar"}) is None
    assert normalize_decision("a string") is None


# --------------------------------------------------------------------------
# Degradation (must never raise)
# --------------------------------------------------------------------------


def test_plain_prose_degrades_to_answer():
    result = parse_decision("I think the answer is 三.")
    assert not result.ok
    assert result.decision.action == "answer"
    assert "三" in (result.decision.answer or "")
    assert result.strategy == "answer_fallback"


def test_empty_response_is_handled():
    result = parse_decision("")
    assert not result.ok
    assert result.decision.action == "answer"
    assert result.decision.answer


def test_garbage_never_raises():
    for payload in ["{{{{", "]]]", "\x00\x01", '{"action":', "null", "[]", "-", "```"]:
        result = parse_decision(payload)
        assert result.decision.action in {"answer", "delegate", "continue", "review", "replan"}
        assert isinstance(result.ok, bool)


def test_delegate_without_agent_is_rejected_and_degrades():
    result = parse_decision('{"action":"delegate","task":"do something"}')
    assert not result.ok
    assert result.decision.action == "answer"


def test_debug_payload_lists_every_attempt():
    result = parse_decision("total nonsense")
    payload = result.debug_payload("total nonsense")
    assert payload["parse_ok"] is False
    assert len(payload["attempts"]) >= 2
    assert payload["raw_decision"] == "total nonsense"


def test_repair_json_is_idempotent_on_valid_json():
    valid = '{"a": 1, "b": [1, 2]}'
    assert repair_json(valid) == valid


def test_strip_code_fence_returns_none_without_fence():
    assert strip_code_fence("no fence here") is None
