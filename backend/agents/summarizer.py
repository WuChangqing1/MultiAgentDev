"""Local Summarizer Agent -- compression and key-point extraction."""

from __future__ import annotations

from agents.local_agent import LocalAgent


class SummarizerAgent(LocalAgent):
    key = "local_summarizer"
    label = "LocalSummarizerAgent"
    role_description = "摘要 / 长文本压缩 / 上下文精简"
    prompt_name = "summarizer"
    output_kind = "text"


__all__ = ["SummarizerAgent"]
