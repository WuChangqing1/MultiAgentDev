"""Local Reviewer Agent -- mechanical verification of another worker's output.

Advisory only. The MainAgent keeps the final say (requirement #5.5).
"""

from __future__ import annotations

from agents.local_agent import LocalAgent


class ReviewerAgent(LocalAgent):
    key = "local_reviewer"
    label = "LocalReviewerAgent"
    role_description = "格式校验 / 完整性检查 / 明显错误与一致性核查"
    reasoning_field = "reasoning_reviewer"
    prompt_name = "reviewer"
    output_kind = "json"


__all__ = ["ReviewerAgent"]
