"""Local Coder Agent -- small, self-contained code generation and review.

This file is deliberately the *entire* implementation of a new worker agent: a
prompt file plus a handful of class attributes. If adding capability ever
requires more than this, something is wrong with the design.
"""

from __future__ import annotations

from agents.local_agent import LocalAgent


class CoderAgent(LocalAgent):
    key = "local_coder"
    label = "LocalCoderAgent"
    role_description = "小段代码生成 / 代码缺陷检查 / 单元测试草稿"
    reasoning_field = "reasoning_coder"
    prompt_name = "coder"
    output_kind = "text"


__all__ = ["CoderAgent"]
