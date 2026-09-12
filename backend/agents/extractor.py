"""Local Extractor Agent -- text to structured data."""

from __future__ import annotations

from agents.local_agent import LocalAgent


class ExtractorAgent(LocalAgent):
    key = "local_extractor"
    label = "LocalExtractorAgent"
    role_description = "信息抽取 / 实体与字段提取 / 文本转 JSON"
    prompt_name = "extractor"
    output_kind = "json"


__all__ = ["ExtractorAgent"]
