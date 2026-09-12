"""Local Classifier Agent -- labels, intent and task type."""

from __future__ import annotations

from agents.local_agent import LocalAgent


class ClassifierAgent(LocalAgent):
    key = "local_classifier"
    label = "LocalClassifierAgent"
    role_description = "文本分类 / 意图识别 / 任务类型与标签判定"
    reasoning_field = "reasoning_classifier"
    prompt_name = "classifier"
    output_kind = "json"


__all__ = ["ClassifierAgent"]
