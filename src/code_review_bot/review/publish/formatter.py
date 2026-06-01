"""Formats review findings and bot metadata into Markdown comment text."""

import json

from code_review_bot.platforms.models import ChangeRequest
from code_review_bot.skill.protocol import Finding, count_findings_by_severity

BOT_METADATA_PREFIX = "<!-- code-review-bot:"

SEVERITY_LABELS: dict[str, str] = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}


def format_review_note(
    cr: ChangeRequest,
    skill_name: str,
    skill_version: str,
    fingerprints: list[str],
    summary: str | None = None,
    severity_counts: dict[str, int] | None = None,
    located_count: int = 0,
    unlocated_findings: list[Finding] | None = None,
    resolved_findings: list[Finding] | None = None,
) -> str:
    findings = unlocated_findings or []
    counts = severity_counts or count_findings_by_severity(findings)
    lines = [
        "### Code Review",
        "",
        summary or "No significant issues found.",
        "",
        (
            "Severity: "
            f"Critical {counts.get('critical', 0)} / "
            f"High {counts.get('high', 0)} / "
            f"Medium {counts.get('medium', 0)} / "
            f"Low {counts.get('low', 0)}"
        ),
        f"Inline comments posted: {located_count}",
        "",
    ]
    if findings:
        lines.append("#### Findings without diff position (not in this MR's changed lines)")
        lines.append("")
        for finding in findings:
            label = SEVERITY_LABELS.get(finding.severity, finding.severity)
            lines.extend(
                [
                    f"- **[{label}]** `{finding.file_path}:{finding.line_range}`: "
                    f"{finding.description}",
                    f"  - Reason: {finding.reason}",
                ]
            )
        lines.append("")
    if resolved_findings:
        lines.append("#### Previously resolved (not re-reported)")
        lines.append("")
        for finding in resolved_findings:
            label = SEVERITY_LABELS.get(finding.severity, finding.severity)
            lines.append(
                f"- ~~**[{label}]** `{finding.file_path}:{finding.line_range}`: "
                f"{finding.description}~~"
            )
        lines.append("")
    lines.append(f"_Skill: `{skill_name}` v`{skill_version}`_")
    metadata = {
        "head_sha": cr.head_sha,
        "skill": skill_name,
        "version": skill_version,
        "fingerprints": sorted(fingerprints),
    }
    lines.append(f"{BOT_METADATA_PREFIX}{json.dumps(metadata, separators=(',', ':'))} -->")
    return "\n".join(lines)
