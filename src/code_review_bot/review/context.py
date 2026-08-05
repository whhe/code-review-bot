import hashlib
import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from code_review_bot.skill.protocol import Finding

logger = logging.getLogger(__name__)

BOT_METADATA_PREFIX = "<!-- code-review-bot:"
METADATA_RE = re.compile(
    rf"{re.escape(BOT_METADATA_PREFIX)}(?P<json>\{{.*?\}}) -->",
    re.DOTALL,
)
MAX_FINDING_HISTORY_CHARS = 30_000
MAX_METADATA_FINDING_TEXT_CHARS = 4_000
MAX_METADATA_FINDING_LOCATION_CHARS = 1_000
MAX_METADATA_ANCHOR_CHARS = 80


class BotMetadata(BaseModel):
    note_id: int | None = None
    schema_version: Literal[2]
    head_sha: str = ""
    skill: str = ""
    version: str = ""
    unlocated_findings: list[Finding] = Field(default_factory=list)


def extract_metadata(
    notes: list[dict[str, object]],
    *,
    skill_name: str | None = None,
    skill_version: str | None = None,
) -> BotMetadata | None:
    """Return the latest matching metadata with bounded recent finding history."""
    parsed: list[BotMetadata] = []
    for note in notes:
        body = str(note.get("body") or "")
        marker_index = body.rfind(BOT_METADATA_PREFIX)
        match = METADATA_RE.fullmatch(body[marker_index:].strip()) if marker_index >= 0 else None
        if not match:
            continue
        try:
            data = json.loads(match.group("json"))
            metadata = BotMetadata.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            logger.debug("Skipping malformed bot metadata note id=%s", note.get("id"))
            continue
        metadata.note_id = int(note["id"]) if note.get("id") is not None else None
        parsed.append(metadata)

    matching = [
        metadata
        for metadata in parsed
        if (skill_name is None or metadata.skill == skill_name)
        and (skill_version is None or metadata.version == skill_version)
    ]
    if not matching:
        return None

    latest = matching[-1]
    newest_findings: list[Finding] = []
    seen_finding_identities: set[tuple[str, str, str, str, str, str, int]] = set()
    for metadata in reversed(matching):
        if metadata.skill != latest.skill or metadata.version != latest.version:
            continue
        for finding in reversed(metadata.unlocated_findings):
            identity = finding_identity(finding)
            if identity in seen_finding_identities:
                continue
            seen_finding_identities.add(identity)
            newest_findings.append(finding)
    latest.unlocated_findings = limit_finding_history(list(reversed(newest_findings)))
    return latest


def limit_finding_history(findings: list[Finding]) -> list[Finding]:
    """Keep compact forms of the newest findings within fixed metadata budgets."""
    retained_reversed: list[Finding] = []
    used_chars = 2
    for finding in reversed(findings):
        compacted = _compact_metadata_finding(finding)
        serialized = encode_metadata_json(serialize_metadata_finding(compacted))
        additional_chars = len(serialized) + (1 if retained_reversed else 0)
        if used_chars + additional_chars > MAX_FINDING_HISTORY_CHARS:
            continue
        retained_reversed.append(compacted)
        used_chars += additional_chars

    retained = list(reversed(retained_reversed))
    dropped = len(findings) - len(retained)
    if dropped:
        logger.warning(
            "Dropped %s review metadata findings outside the retention budget (max_chars=%s)",
            dropped,
            MAX_FINDING_HISTORY_CHARS,
        )
    return retained


def metadata_finding_chars(finding: Finding) -> int:
    """Return the encoded character cost of one compacted metadata finding."""
    compacted = _compact_metadata_finding(finding)
    return len(encode_metadata_json(serialize_metadata_finding(compacted)))


def finding_identity(
    finding: Finding,
) -> tuple[str, str, str, str, str, str, int]:
    """Return the identity of the Finding's final persisted representation."""
    persisted = _compact_metadata_finding(finding)
    return (
        persisted.severity,
        persisted.description,
        persisted.file_path,
        persisted.line_range,
        persisted.anchor_text,
        persisted.reason,
        persisted.confidence,
    )


def serialize_metadata_finding(finding: Finding) -> dict[str, object]:
    """Serialize a Finding for storage in review metadata."""
    return finding.model_dump(mode="json")


def encode_metadata_json(value: object) -> str:
    """Encode metadata exactly as it will be embedded in an HTML comment."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).replace(
        "--", r"\u002d\u002d"
    )


def _compact_metadata_finding(finding: Finding) -> Finding:
    updates = {
        "description": _compact_metadata_text(finding.description, MAX_METADATA_FINDING_TEXT_CHARS),
        "reason": _compact_metadata_text(finding.reason, MAX_METADATA_FINDING_TEXT_CHARS),
        "file_path": _compact_metadata_text(finding.file_path, MAX_METADATA_FINDING_LOCATION_CHARS),
        "line_range": _compact_metadata_text(
            finding.line_range, MAX_METADATA_FINDING_LOCATION_CHARS
        ),
        "anchor_text": _compact_metadata_text(finding.anchor_text, MAX_METADATA_ANCHOR_CHARS),
    }
    if all(getattr(finding, field) == value for field, value in updates.items()):
        return finding
    return finding.model_copy(update=updates)


def _compact_metadata_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    suffix = f"… [truncated sha256:{digest}]"
    return value[: max_chars - len(suffix)] + suffix
