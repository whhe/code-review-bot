from __future__ import annotations

import logging
import re
from pathlib import Path

from code_review_bot.paths import project_root
from code_review_bot.platforms.models import ChangeRequest
from code_review_bot.review.models import ReviewOutcome
from code_review_bot.review.publish.formatter import (
    BOT_METADATA_PREFIX,
    SEVERITY_LABELS,
    format_additional_findings_notes,
    format_review_note,
)
from code_review_bot.skill.protocol import Finding, SkillResult, count_findings_by_severity

logger = logging.getLogger(__name__)


class DebugMarkdownPublisher:
    """Markdown backend: writes the review report to a local .md file instead of posting."""

    def __init__(self, output_dir: str | Path) -> None:
        raw = Path(output_dir)
        self.output_dir = raw if raw.is_absolute() else (project_root() / raw).resolve()

    async def publish(
        self,
        cr: ChangeRequest,
        result: SkillResult,
        skill_name: str,
        skill_version: str,
        existing_notes: list[dict[str, object]] | None = None,
        resolved_findings: list[Finding] | None = None,
    ) -> ReviewOutcome:
        inline_findings, unlocated = _split_by_line(result.findings)

        severity_counts = count_findings_by_severity(result.findings)
        summary_note = format_review_note(
            cr=cr,
            summary=result.summary,
            severity_counts=severity_counts,
            located_count=len(inline_findings),
            unlocated_findings=unlocated,
            resolved_findings=resolved_findings,
            skill_name=skill_name,
            skill_version=skill_version,
            runtime=result.runtime,
        )
        summary_note_clean = _strip_metadata_comment(summary_note)
        additional_notes = [
            _strip_metadata_comment(note)
            for note in format_additional_findings_notes(
                unlocated,
                cr,
                skill_name,
                skill_version,
            )
        ]

        # Sanitize project_ref for use in filename
        safe_ref = cr.project_ref.replace("/", "-").replace("\\", "-")[:64]
        if cr.web_url:
            mr_ref = f"[!{cr.cr_id} {cr.title}]({cr.web_url})"
        else:
            mr_ref = f"!{cr.cr_id} {cr.title}"

        lines: list[str] = [
            "# Code Review Report",
            "",
            "| | |",
            "|---|---|",
            f"| **CR** | {mr_ref} |",
            f"| **Branch** | `{cr.source_branch}` → `{cr.target_branch}` |",
        ]
        if cr.author:
            lines.append(f"| **Author** | {cr.author} |")
        lines += [
            f"| **Skill** | `{skill_name}` v`{skill_version}` |",
            "",
            "---",
            "",
            "## Summary",
            "",
            summary_note_clean,
        ]
        for additional_note in additional_notes:
            lines += ["", "---", "", additional_note]

        if inline_findings:
            lines += ["", "---", "", f"## Inline Comments ({len(inline_findings)})", ""]
            by_file: dict[str, list[Finding]] = {}
            for finding in inline_findings:
                by_file.setdefault(finding.file_path, []).append(finding)

            for path, file_findings in by_file.items():
                lines.append(f"### `{path}`")
                lines.append("")
                for finding in file_findings:
                    label = SEVERITY_LABELS.get(finding.severity, finding.severity)
                    header = f"**Line {finding.line_range} · [{label}]**"
                    if finding.anchor_text:
                        header += f" `{finding.anchor_text}`"
                    lines += [
                        f"> {header}  ",
                        f"> {finding.description}",
                        ">",
                        f"> _Reason: {finding.reason}_",
                        "",
                    ]
                lines += ["---", ""]

        self.output_dir.mkdir(parents=True, exist_ok=True)
        out_path = self.output_dir / f"project-{safe_ref}_cr-{cr.cr_id}.md"
        out_path.write_text("\n".join(lines), encoding="utf-8")

        try:
            display_path = out_path.relative_to(project_root())
        except ValueError:
            display_path = out_path

        return ReviewOutcome(
            summary=result.summary,
            published=True,
            inline_comments=len(inline_findings),
            report_path=str(display_path),
        )


def _split_by_line(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    inline: list[Finding] = []
    unlocated: list[Finding] = []
    for finding in findings:
        if re.search(r"\d+", finding.line_range):
            inline.append(finding)
        else:
            unlocated.append(finding)
    return inline, unlocated


def _strip_metadata_comment(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith(BOT_METADATA_PREFIX))
