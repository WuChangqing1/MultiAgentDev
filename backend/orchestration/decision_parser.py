"""Fault-tolerant parsing of MainAgent decisions.

A 2B model in the loop guarantees malformed JSON eventually, and a strong model
still drifts. This module therefore never raises at the caller: every entry point
returns an :class:`AgentDecision`, degrading to ``action="answer"`` when nothing
usable can be recovered.

Recovery ladder
---------------
1. strict ``json.loads``
2. a fenced ```json block
3. balanced-brace scan (first *complete* object, tolerant of trailing prose)
4. brace-completion for truncated output (small models hit ``max_tokens``)
5. common repairs: raw newlines in strings, single quotes, trailing commas,
   unquoted keys, Python literals, ``//`` comments
6. **reassembly from an unrecognized shape**: any object carrying a plausible
   ``task``/``agent``/``answer`` field is coerced into a valid decision, even if
   the model invented its own key names (e.g. ``{"steps": [...]}``)

Every attempt is recorded so Debug Mode can show exactly what happened.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from models.schemas import AgentDecision

log = logging.getLogger(__name__)

_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.DOTALL)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")
# An object key that is unquoted or single-quoted. Only keys following ``{`` or
# ``,`` are matched, so a colon inside a string value is never rewritten, and
# already-valid JSON is left byte-identical.
_UNQUOTED_KEY_RE = re.compile(r"([{,]\s*)['\"]?([A-Za-z_][A-Za-z0-9_\-]*)['\"]?(\s*:)")
_LINE_COMMENT_RE = re.compile(r"(?<![:\"'\\])//[^\n\"]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)

#: Sentinel used to shield already-double-quoted strings while single-quoted
#: strings are converted. Chosen to be extremely unlikely in model output.
_PROTECT = "\x00"


def _quote_unquoted_keys(match: re.Match[str]) -> str:
    lead, key, colon = match.group(1), match.group(2), match.group(3)
    return f'{lead}"{key}"{colon}'


def _single_to_double_quotes(text: str) -> str:
    """Convert single-quoted strings to double-quoted ones.

    Text inside an existing ``"..."`` string is protected first, so apostrophes
    in normal prose are never touched.
    """
    protected: list[str] = []

    def _shield(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"{_PROTECT}{len(protected) - 1}{_PROTECT}"

    shielded = re.sub(r'"(?:[^"\\]|\\.)*"', _shield, text)

    out: list[str] = []
    in_single = False
    for char in shielded:
        if char == "'":
            out.append('"')
            in_single = not in_single
            continue
        out.append(char)
    if in_single:
        out.append('"')

    joined = "".join(out)
    for index, original in enumerate(protected):
        joined = joined.replace(f"{_PROTECT}{index}{_PROTECT}", original)
    return joined

VALID_ACTIONS = {"delegate", "answer", "continue", "review", "replan"}

#: Keys whose values we mine when the model ignored our protocol entirely.
_TASK_KEYS = ("task", "instruction", "subtask", "prompt", "query", "input", "text")
_AGENT_KEYS = ("agent", "worker", "target", "to", "agent_name", "delegate_to")
_CONTEXT_KEYS = ("context", "source", "content", "material", "payload", "data", "document")
_ANSWER_KEYS = ("answer", "final_answer", "response", "reply", "result", "output", "final")
_REASON_KEYS = ("reason", "reasoning", "why", "rationale", "justification")
_STEP_LIST_KEYS = ("steps", "plan", "tasks", "actions", "subtasks", "delegations")


@dataclass
class ParseAttempt:
    strategy: str
    ok: bool
    detail: str = ""


@dataclass
class ParseResult:
    """Decision plus the audit trail Debug Mode renders."""

    decision: AgentDecision
    ok: bool
    strategy: str
    attempts: list[ParseAttempt] = field(default_factory=list)
    error: str | None = None

    def debug_payload(self, raw: str | None = None) -> dict[str, Any]:
        return {
            "parse_ok": self.ok,
            "strategy": self.strategy,
            "attempts": [{"strategy": a.strategy, "ok": a.ok, "detail": a.detail} for a in self.attempts],
            "error": self.error,
            "raw_decision": raw,
        }


# --------------------------------------------------------------------------
# JSON recovery primitives
# --------------------------------------------------------------------------


def strip_code_fence(text: str) -> str | None:
    match = _FENCE_RE.search(text)
    return match.group(1).strip() if match else None


def extract_balanced_object(text: str) -> str | None:
    """Return the first *balanced* ``{...}`` block, ignoring braces in strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def complete_truncated_object(text: str) -> str | None:
    """Close an unterminated object (model hit max_tokens mid-JSON)."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    last_safe = len(text)

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
        elif char in ",:":
            last_safe = index

    if depth <= 0:
        return None

    fragment = text[start:]
    if in_string:
        fragment += '"'
    # Drop a dangling key/value separator and close every open container.
    fragment = fragment.rstrip()
    while fragment and fragment[-1] in ",:":
        fragment = fragment[:-1].rstrip()
    fragment += "}" * max(depth, 0)
    if last_safe <= 0:  # pragma: no cover - defensive
        return None
    return fragment


def repair_json(text: str) -> str:
    """Apply the mechanical repairs that account for most real failures."""
    out = _BLOCK_COMMENT_RE.sub("", text)
    out = _LINE_COMMENT_RE.sub("", out)
    out = out.replace("\u201c", '"').replace("\u201d", '"')
    out = out.replace("\uff1a", ":").replace("\uff0c", ",")
    # Newlines inside string literals are the single most common defect.
    out = _escape_control_chars_in_strings(out)
    out = _TRAILING_COMMA_RE.sub(r"\1", out)
    out = _replace_python_literals(out)
    out = _single_to_double_quotes(out)
    out = _UNQUOTED_KEY_RE.sub(_quote_unquoted_keys, out)
    out = _balance_delimiters(out)
    return out


def _escape_control_chars_in_strings(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
                result.append(char)
                continue
            if char == "\\":
                escaped = True
                result.append(char)
                continue
            if char == '"':
                in_string = False
                result.append(char)
                continue
            if char == "\n":
                result.append("\\n")
                continue
            if char == "\r":
                result.append("\\r")
                continue
            if char == "\t":
                result.append("\\t")
                continue
            result.append(char)
            continue

        if char == '"':
            in_string = True
        result.append(char)
    return "".join(result)


def _replace_python_literals(text: str) -> str:
    out = re.sub(r"\bNone\b", "null", text)
    out = re.sub(r"\bTrue\b", "true", out)
    out = re.sub(r"\bFalse\b", "false", out)
    return out


def _balance_delimiters(text: str) -> str:
    """Close unbalanced ``{}`` / ``[]`` outside of strings."""
    stack: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]" and stack:
            stack.pop()

    closing = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    if in_string:
        text += '"'
    text = text.rstrip()
    while text and text[-1] in ",:":
        text = text[:-1].rstrip()
    return text + closing


# --------------------------------------------------------------------------
# Shape normalization
# --------------------------------------------------------------------------


def _first_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and key in _ANSWER_KEYS:
            return str(value)
    return None


def _flatten(container: Any) -> dict[str, Any] | None:
    """Find a decision-shaped dict inside a list / wrapper."""
    if isinstance(container, dict):
        return container
    candidates: list[Any] = []
    if isinstance(container, list):
        candidates = container
    if not candidates:
        return None
    outer: dict[str, Any] = {}
    for item in candidates:
        if not isinstance(item, dict):
            continue
        for key in _AGENT_KEYS + _TASK_KEYS + _CONTEXT_KEYS:
            if key in item and key not in outer:
                outer[key] = item[key]
    return outer or None


def normalize_decision(payload: Any) -> AgentDecision | None:
    """Coerce an arbitrary dict into an AgentDecision.

    Returns ``None`` only when nothing decision-like can be found.
    """
    if isinstance(payload, list):
        payload = _flatten(payload)
    if not isinstance(payload, dict):
        return None

    # Unwrap {"decision": {...}} / {"plan": {...}} style nesting.
    for wrapper in ("decision", "result", "output", "response", "plan", "action_plan"):
        inner = payload.get(wrapper)
        if isinstance(inner, dict) and ("action" in inner or "agent" in inner or "task" in inner):
            merged = {k: v for k, v in payload.items() if k != wrapper}
            merged.update(inner)
            payload = merged
            break

    raw_action = payload.get("action") or payload.get("type") or payload.get("next_action")
    action = str(raw_action).strip().lower() if isinstance(raw_action, (str, int)) else ""

    # A multi-step plan array means the model tried to plan ahead; take step one.
    if action not in VALID_ACTIONS:
        for list_key in _STEP_LIST_KEYS:
            items = payload.get(list_key)
            if isinstance(items, list) and items:
                nested = normalize_decision(_flatten(items) or items[0])
                if nested is not None:
                    if not nested.reason and _first_str(payload, _REASON_KEYS):
                        nested.reason = _first_str(payload, _REASON_KEYS)
                    return nested

    agent = _first_str(payload, _AGENT_KEYS)
    task = _first_str(payload, _TASK_KEYS)
    context = payload.get("context") if isinstance(payload.get("context"), str) else _first_str(
        payload, _CONTEXT_KEYS
    )
    answer = _first_str(payload, _ANSWER_KEYS)
    reason = _first_str(payload, _REASON_KEYS)

    if not action:
        if agent and task:
            action = "delegate"
        elif answer:
            action = "answer"
        elif task:
            action = "continue"
        else:
            return None

    if action == "delegate" and not agent:
        return None
    if action == "answer" and not answer:
        return None
    if action in ("delegate", "review", "continue") and not task:
        return None

    expected_format = payload.get("expected_format") or payload.get("format")
    if not isinstance(expected_format, str) or expected_format.lower() not in ("text", "json"):
        expected_format = "json" if action in ("delegate", "review") else "text"

    return AgentDecision(
        action=action,  # type: ignore[arg-type]
        agent=agent,
        task=task,
        context=context,
        reason=reason,
        answer=answer,
        expected_format=expected_format.lower(),  # type: ignore[arg-type]
        parallel=bool(payload.get("parallel", False)),
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def parse_decision(raw: str, *, fallback_answer: str | None = None) -> ParseResult:
    """Parse a MainAgent response into a decision, never raising."""
    attempts: list[ParseAttempt] = []
    text = (raw or "").strip()
    original = text

    if not text:
        decision = _fallback(fallback_answer, "Model returned an empty response.")
        attempts.append(ParseAttempt("empty", False, "no content"))
        return ParseResult(decision, False, "empty", attempts, "empty response")

    fenced = strip_code_fence(text)
    candidates: list[tuple[str, str]] = [("strict", text)]
    if fenced:
        candidates.append(("fence", fenced))

    balanced = extract_balanced_object(fenced or text)
    if balanced:
        candidates.append(("balanced_scan", balanced))

    for strategy, candidate in candidates:
        parsed, detail = _try_loads(candidate)
        if parsed is not None:
            decision = normalize_decision(parsed)
            if decision is not None:
                attempts.append(ParseAttempt(strategy, True, "parsed"))
                decision.raw = original
                return ParseResult(decision, True, strategy, attempts)
            attempts.append(ParseAttempt(strategy, False, "json ok but no decision fields"))
        else:
            attempts.append(ParseAttempt(strategy, False, detail))

    # Repair pass over the best available fragment.
    fragment = balanced or fenced or text
    repaired = repair_json(fragment)
    parsed, detail = _try_loads(repaired)
    if parsed is not None:
        decision = normalize_decision(parsed)
        if decision is not None:
            attempts.append(ParseAttempt("repair", True, "repaired and parsed"))
            decision.raw = original
            return ParseResult(decision, True, "repair", attempts)
        attempts.append(ParseAttempt("repair", False, "json ok but no decision fields"))
    else:
        attempts.append(ParseAttempt("repair", False, detail))

    truncated = complete_truncated_object(fragment)
    if truncated:
        parsed, detail = _try_loads(repair_json(truncated))
        if parsed is not None:
            decision = normalize_decision(parsed)
            if decision is not None:
                attempts.append(ParseAttempt("truncation_repair", True, "closed truncated object"))
                decision.raw = original
                return ParseResult(decision, True, "truncation_repair", attempts)
            attempts.append(ParseAttempt("truncation_repair", False, "no decision fields"))
        else:
            attempts.append(ParseAttempt("truncation_repair", False, detail))

    # Last resort: treat whatever the model wrote as the answer.
    answer = _salvage_answer(text)
    decision = _fallback(
        fallback_answer or answer,
        "Decision JSON could not be parsed; degraded to a direct answer.",
    )
    decision.raw = original
    attempts.append(ParseAttempt("answer_fallback", bool(answer), "used raw text as answer"))
    log.warning("decision_parse_failed", extra={"strategies": len(attempts)})
    return ParseResult(decision, False, "answer_fallback", attempts, "could not parse decision JSON")


def _try_loads(text: str) -> tuple[Any | None, str]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"json error at {exc.pos}: {exc.msg}"
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)

    # ``json.loads("null")`` succeeds but carries no decision; treat bare
    # scalars as a parse failure so the repair pass still gets a chance.
    if value is None or isinstance(value, (str, int, float, bool)):
        return None, "json scalar, not an object"
    return value, "ok"


def _salvage_answer(text: str) -> str | None:
    """Recover a usable answer from non-JSON output."""
    cleaned = strip_code_fence(text) or text
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if cleaned[0] in "{[":
        # Looks like a broken structure; strip outer braces we cannot use.
        inner = cleaned.strip("{}[] \n")
        return inner or None
    return cleaned


def _fallback(answer: str | None, reason: str) -> AgentDecision:
    return AgentDecision(
        action="answer",
        answer=answer or "I could not complete this request correctly. Please try rephrasing it.",
        reason=reason,
        expected_format="text",
    )


__all__ = [
    "ParseAttempt",
    "ParseResult",
    "complete_truncated_object",
    "extract_balanced_object",
    "normalize_decision",
    "parse_decision",
    "repair_json",
    "strip_code_fence",
]
