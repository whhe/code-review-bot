from __future__ import annotations

from typing import Protocol

from code_review_bot.platforms.models import ChangeRequest
from code_review_bot.review.models import ReviewOutcome
from code_review_bot.skill.protocol import Finding, SkillResult


class ReviewPublisher(Protocol):
    """Plugin interface for publishing review results to a backend."""

    async def publish(
        self,
        cr: ChangeRequest,
        result: SkillResult,
        skill_name: str,
        skill_version: str,
        fingerprints: list[str],
        existing_notes: list[dict[str, object]] | None = None,
        resolved_findings: list[Finding] | None = None,
    ) -> ReviewOutcome: ...
