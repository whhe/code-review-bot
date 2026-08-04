from __future__ import annotations

import logging
import re

from code_review_bot.platforms.models import ChangeRequest, InlinePosition
from code_review_bot.platforms.protocol import PlatformAdapter
from code_review_bot.review.context import finding_identity
from code_review_bot.review.models import ReviewOutcome
from code_review_bot.review.publish.formatter import (
    SEVERITY_LABELS,
    format_additional_findings_notes,
    format_review_note,
)
from code_review_bot.skill.protocol import Finding, SkillResult, count_findings_by_severity

logger = logging.getLogger(__name__)


class PlatformPublisher:
    """ReviewPublisher that posts findings through a PlatformAdapter.

    Attempts to create inline diff comments for each finding. Falls back to
    including the finding in the summary note when the API rejects the position.
    """

    def __init__(self, adapter: PlatformAdapter) -> None:
        self.adapter = adapter

    async def publish(
        self,
        cr: ChangeRequest,
        result: SkillResult,
        skill_name: str,
        skill_version: str,
        existing_notes: list[dict[str, object]] | None = None,
        resolved_findings: list[Finding] | None = None,
        metadata_findings: list[Finding] | None = None,
        publish_summary: bool = True,
    ) -> ReviewOutcome:
        located_count, unlocated = await self._publish_inline(cr, result.findings)
        metadata_history_reversed: list[Finding] = []
        metadata_identities: set[tuple[str, str, str, str, str, str, int]] = set()
        candidates = [*(metadata_findings or []), *unlocated]
        for finding in reversed(candidates):
            identity = finding_identity(finding)
            if identity in metadata_identities:
                continue
            metadata_identities.add(identity)
            metadata_history_reversed.append(finding)
        metadata_history = list(reversed(metadata_history_reversed))
        severity_counts = count_findings_by_severity(result.findings)
        body = format_review_note(
            cr=cr,
            summary=result.summary,
            severity_counts=severity_counts,
            located_count=located_count,
            unlocated_findings=unlocated,
            resolved_findings=resolved_findings,
            skill_name=skill_name,
            skill_version=skill_version,
            metadata_findings=metadata_history,
            runtime=result.runtime,
        )
        for additional_body in format_additional_findings_notes(
            unlocated,
            cr,
            skill_name,
            skill_version,
        ):
            await self.adapter.publish_summary(cr.project_ref, cr.cr_id, additional_body)
        if publish_summary:
            await self.adapter.publish_summary(cr.project_ref, cr.cr_id, body)
        return ReviewOutcome(
            summary=result.summary,
            published=True,
            inline_comments=located_count,
            review_body=body,
        )

    async def _publish_inline(
        self,
        cr: ChangeRequest,
        findings: list[Finding],
    ) -> tuple[int, list[Finding]]:
        located_count = 0
        unlocated: list[Finding] = []
        for finding in findings:
            new_line = _parse_first_line(finding.line_range)
            if new_line is None:
                unlocated.append(finding)
                continue
            position = InlinePosition(
                file_path=finding.file_path,
                new_line=new_line,
                base_sha=cr.diff_refs.get("base_sha", ""),
                start_sha=cr.diff_refs.get("start_sha", ""),
                head_sha=cr.diff_refs.get("head_sha", cr.head_sha),
            )
            try:
                await self.adapter.publish_inline_comment(
                    cr.project_ref,
                    cr.cr_id,
                    _format_inline_body(finding),
                    position,
                )
                located_count += 1
            except Exception:
                logger.warning(
                    "Inline comment failed for %s:%s, adding to unlocated",
                    finding.file_path,
                    finding.line_range,
                    exc_info=True,
                )
                unlocated.append(finding)
        return located_count, unlocated


def _format_inline_body(finding: Finding) -> str:
    label = SEVERITY_LABELS.get(finding.severity, finding.severity)
    return f"**[{label}]** {finding.description}\n\n_Reason: {finding.reason}_"


def _parse_first_line(line_range: str) -> int | None:
    numbers = re.findall(r"\d+", line_range)
    if numbers:
        return int(numbers[0])
    return None
