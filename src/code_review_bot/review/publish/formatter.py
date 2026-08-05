"""Formats review findings and bot metadata into Markdown comment text."""

import hashlib
import logging

from code_review_bot.platforms.models import ChangeRequest
from code_review_bot.review.context import (
    BOT_METADATA_PREFIX,
    MAX_FINDING_HISTORY_CHARS,
    encode_metadata_json,
    finding_identity,
    limit_finding_history,
    metadata_finding_chars,
    serialize_metadata_finding,
)
from code_review_bot.skill.protocol import Finding, RuntimeMetadata, count_findings_by_severity

logger = logging.getLogger(__name__)

CODE_REVIEW_BOT_LABEL = "whhe/code-review-bot"
CODE_REVIEW_BOT_URL = "https://github.com/whhe/code-review-bot"
MAX_REVIEW_NOTE_CHARS = 60_000
MAX_VISIBLE_FINDINGS = 20
MAX_VISIBLE_SUMMARY_CHARS = 2_000
MAX_VISIBLE_FINDING_TEXT_CHARS = 160
MAX_VISIBLE_LOCATION_CHARS = 200
MAX_RUNTIME_LINE_CHARS = 1_000
MAX_FALLBACK_FIELD_CHARS = 1_000
TRUNCATION_NOTICE = "[Review output truncated to stay within platform comment limits.]"

AGENT_DISPLAY_NAMES: dict[str, str] = {
    "claude": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
}

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
    summary: str | None = None,
    severity_counts: dict[str, int] | None = None,
    located_count: int = 0,
    unlocated_findings: list[Finding] | None = None,
    resolved_findings: list[Finding] | None = None,
    metadata_findings: list[Finding] | None = None,
    runtime: RuntimeMetadata | None = None,
) -> str:
    findings = sorted(
        unlocated_findings or [],
        key=lambda finding: ("critical", "high", "medium", "low").index(finding.severity),
    )
    counts = severity_counts or count_findings_by_severity(findings)
    lines = [
        "### Code Review",
        "",
        _compact_visible_text(summary or "No significant issues found.", MAX_VISIBLE_SUMMARY_CHARS),
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
    finding_batches = _partition_findings_for_notes(findings)
    visible_findings = finding_batches[0] if finding_batches else []
    if visible_findings:
        lines.append("#### Findings without diff position (not in this MR's changed lines)")
        lines.append("")
        for finding in visible_findings:
            lines.extend(_format_visible_finding(finding))
        lines.append("")
    omitted_findings = [finding for batch in finding_batches[1:] for finding in batch]
    remaining_slots = MAX_VISIBLE_FINDINGS - len(visible_findings)
    visible_resolved = (resolved_findings or [])[:remaining_slots]
    if visible_resolved:
        lines.append("#### Previously resolved (not re-reported)")
        lines.append("")
        for finding in visible_resolved:
            label = SEVERITY_LABELS.get(finding.severity, finding.severity)
            lines.append(
                f"- ~~**[{label}]** `"
                f"{_compact_visible_text(finding.file_path, MAX_VISIBLE_LOCATION_CHARS)}:"
                f"{_compact_visible_text(finding.line_range, MAX_VISIBLE_LOCATION_CHARS)}`: "
                f"{_compact_visible_text(finding.description, MAX_VISIBLE_FINDING_TEXT_CHARS)}~~"
            )
        lines.append("")
    omitted_resolved = (resolved_findings or [])[remaining_slots:]
    if omitted_findings or omitted_resolved:
        omitted_counts = count_findings_by_severity(omitted_findings)
        lines.extend(
            [
                TRUNCATION_NOTICE,
                (
                    "Additional current findings: "
                    f"Critical {omitted_counts['critical']} / "
                    f"High {omitted_counts['high']} / "
                    f"Medium {omitted_counts['medium']} / "
                    f"Low {omitted_counts['low']}; "
                    f"previously resolved {len(omitted_resolved)}."
                ),
                "",
            ]
        )
    if lines and lines[-1] != "":
        lines.append("")
    visible_identities = {finding_identity(finding) for finding in visible_findings}
    metadata_candidates = [
        finding
        for finding in (metadata_findings or [])
        if finding_identity(finding) not in visible_identities
    ]
    # limit_finding_history retains from the tail, so append visible findings
    # in reverse severity order to preserve the highest-severity items first.
    metadata_candidates.extend(reversed(visible_findings))
    metadata_history = limit_finding_history(metadata_candidates)
    metadata = {
        "schema_version": 2,
        "head_sha": cr.head_sha,
        "skill": skill_name,
        "version": skill_version,
        "unlocated_findings": [serialize_metadata_finding(finding) for finding in metadata_history],
    }
    metadata_json = encode_metadata_json(metadata)
    suffix = "\n".join(
        [
            _compact_visible_text(
                _format_attribution_line(runtime, skill_version), MAX_RUNTIME_LINE_CHARS
            ),
            _compact_visible_text(_format_runtime_line(runtime), MAX_RUNTIME_LINE_CHARS),
            f"{BOT_METADATA_PREFIX}{metadata_json} -->",
        ]
    )
    visible_body = "\n".join(lines)
    body = f"{visible_body}{suffix}"
    if len(body) > MAX_REVIEW_NOTE_CHARS:
        logger.warning(
            "Formatted review note exceeds %s characters after compaction; "
            "publishing a minimal fallback summary",
            MAX_REVIEW_NOTE_CHARS,
        )
        fallback_metadata = {
            **metadata,
            "head_sha": _compact_visible_text(cr.head_sha, MAX_FALLBACK_FIELD_CHARS),
            "skill": _compact_visible_text(skill_name, MAX_FALLBACK_FIELD_CHARS),
            "version": _compact_visible_text(skill_version, MAX_FALLBACK_FIELD_CHARS),
        }
        fallback_metadata_json = encode_metadata_json(fallback_metadata)
        fallback_lines = [
            "### Code Review",
            "",
            TRUNCATION_NOTICE,
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
            finding = findings[0]
            label = SEVERITY_LABELS.get(finding.severity, finding.severity)
            fallback_lines.extend(
                [
                    "#### Highest-priority finding without diff position",
                    "",
                    f"- **[{label}]** `"
                    f"{_compact_visible_text(finding.file_path, MAX_VISIBLE_LOCATION_CHARS)}:"
                    f"{_compact_visible_text(finding.line_range, MAX_VISIBLE_LOCATION_CHARS)}`: "
                    f"{_compact_visible_text(finding.description, MAX_VISIBLE_FINDING_TEXT_CHARS)}",
                    "  - Reason: "
                    f"{_compact_visible_text(finding.reason, MAX_VISIBLE_FINDING_TEXT_CHARS)}",
                    "",
                ]
            )
        fallback_lines.extend(
            [
                _compact_visible_text(
                    _format_attribution_line(runtime, skill_version), MAX_FALLBACK_FIELD_CHARS
                ),
                _compact_visible_text(_format_runtime_line(runtime), MAX_FALLBACK_FIELD_CHARS),
                f"{BOT_METADATA_PREFIX}{fallback_metadata_json} -->",
            ]
        )
        body = "\n".join(fallback_lines)
    return body


def format_additional_findings_notes(
    findings: list[Finding],
    cr: ChangeRequest,
    skill_name: str,
    skill_version: str,
) -> list[str]:
    """Render current findings that do not fit in the primary summary as visible notes."""
    notes: list[str] = []
    for batch in _partition_findings_for_notes(findings)[1:]:
        lines = ["### Additional code review findings", ""]
        for finding in batch:
            lines.extend(_format_visible_finding(finding))
        metadata = {
            "schema_version": 2,
            "head_sha": cr.head_sha,
            "skill": skill_name,
            "version": skill_version,
            "unlocated_findings": [
                serialize_metadata_finding(finding) for finding in limit_finding_history(batch)
            ],
        }
        lines.extend(["", f"{BOT_METADATA_PREFIX}{encode_metadata_json(metadata)} -->"])
        notes.append("\n".join(lines))
    return notes


def _partition_findings_for_notes(findings: list[Finding]) -> list[list[Finding]]:
    ordered = sorted(
        findings,
        key=lambda finding: ("critical", "high", "medium", "low").index(finding.severity),
    )
    batches: list[list[Finding]] = []
    current: list[Finding] = []
    current_metadata_chars = 2
    for finding in ordered:
        finding_chars = metadata_finding_chars(finding)
        additional_chars = finding_chars + (1 if current else 0)
        if (
            len(current) < MAX_VISIBLE_FINDINGS
            and current_metadata_chars + additional_chars <= MAX_FINDING_HISTORY_CHARS
        ):
            current.append(finding)
            current_metadata_chars += additional_chars
            continue
        if current:
            batches.append(current)
        current = [finding]
        current_metadata_chars = 2 + finding_chars
    if current:
        batches.append(current)
    return batches


def _format_visible_finding(finding: Finding) -> list[str]:
    label = SEVERITY_LABELS.get(finding.severity, finding.severity)
    return [
        f"- **[{label}]** `"
        f"{_compact_visible_text(finding.file_path, MAX_VISIBLE_LOCATION_CHARS)}:"
        f"{_compact_visible_text(finding.line_range, MAX_VISIBLE_LOCATION_CHARS)}`: "
        f"{_compact_visible_text(finding.description, MAX_VISIBLE_FINDING_TEXT_CHARS)}",
        f"  - Reason: {_compact_visible_text(finding.reason, MAX_VISIBLE_FINDING_TEXT_CHARS)}",
    ]


def _compact_visible_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    suffix = f"… [truncated sha256:{digest}]"
    return value[: max_chars - len(suffix)] + suffix


def _format_runtime_line(runtime: RuntimeMetadata | None) -> str:
    model = runtime.model if runtime and runtime.model else "unavailable"
    input_tokens = _format_token_value(runtime.input_tokens if runtime else None)
    output_tokens = _format_token_value(runtime.output_tokens if runtime else None)
    total_tokens = _format_token_value(runtime.total_tokens if runtime else None)
    return (
        f"Model: {model} · Tokens: input {input_tokens} / "
        f"output {output_tokens} / total {total_tokens}"
    )


def _format_attribution_line(runtime: RuntimeMetadata | None, skill_version: str) -> str:
    raw_agent = runtime.agent_type if runtime and runtime.agent_type else "unavailable"
    agent = AGENT_DISPLAY_NAMES.get(raw_agent, raw_agent)
    return (
        f"*Generated by* [*{CODE_REVIEW_BOT_LABEL}*]({CODE_REVIEW_BOT_URL}) "
        f"*· Agent:* *{agent}* *· Skill fingerprint:* *{skill_version}*"
    )


def _format_token_value(value: int | None) -> str:
    if value is None:
        return "unavailable"
    return f"{value:,}"
