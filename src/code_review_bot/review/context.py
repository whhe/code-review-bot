import hashlib
import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from code_review_bot.skill.protocol import Finding

logger = logging.getLogger(__name__)

METADATA_RE = re.compile(r"<!-- code-review-bot:(?P<json>\{.*?\}) -->", re.DOTALL)
MAX_FINDING_HISTORY_CHARS = 30_000
MAX_FINDING_HISTORY_ITEMS = 50
MAX_METADATA_FINDING_TEXT_CHARS = 4_000
MAX_METADATA_FINDING_LOCATION_CHARS = 1_000


class BotMetadata(BaseModel):
    note_id: int | None = None
    schema_version: int = 1
    head_sha: str = ""
    skill: str = ""
    version: str = ""
    fingerprints: set[str] = Field(default_factory=set)
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
        match = METADATA_RE.search(body)
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
    merged_fingerprints: set[str] = set()
    newest_findings: list[Finding] = []
    for metadata in matching:
        if metadata.skill != latest.skill or metadata.version != latest.version:
            continue
        merged_fingerprints.update(metadata.fingerprints)
    for metadata in reversed(matching):
        for finding in reversed(metadata.unlocated_findings):
            if finding not in newest_findings:
                newest_findings.append(finding)
    latest.fingerprints = merged_fingerprints
    latest.unlocated_findings = limit_finding_history(list(reversed(newest_findings)))
    return latest


def limit_finding_history(findings: list[Finding]) -> list[Finding]:
    """Keep compact forms of the newest findings within fixed metadata budgets."""
    retained_reversed: list[Finding] = []
    used_chars = 2
    candidates = findings[-MAX_FINDING_HISTORY_ITEMS:]
    for finding in reversed(candidates):
        compacted = _compact_metadata_finding(finding)
        serialized = json.dumps(compacted.model_dump(mode="json"), separators=(",", ":"))
        serialized = serialized.replace("--", r"\u002d\u002d")
        additional_chars = len(serialized) + (1 if retained_reversed else 0)
        if used_chars + additional_chars > MAX_FINDING_HISTORY_CHARS:
            continue
        retained_reversed.append(compacted)
        used_chars += additional_chars

    retained = list(reversed(retained_reversed))
    dropped = len(findings) - len(retained)
    if dropped:
        logger.warning(
            "Dropped %s older review metadata findings to stay within %s characters",
            dropped,
            MAX_FINDING_HISTORY_CHARS,
        )
    return retained


def _compact_metadata_finding(finding: Finding) -> Finding:
    updates = {
        "description": _compact_metadata_text(finding.description, MAX_METADATA_FINDING_TEXT_CHARS),
        "reason": _compact_metadata_text(finding.reason, MAX_METADATA_FINDING_TEXT_CHARS),
        "file_path": _compact_metadata_text(finding.file_path, MAX_METADATA_FINDING_LOCATION_CHARS),
        "line_range": _compact_metadata_text(
            finding.line_range, MAX_METADATA_FINDING_LOCATION_CHARS
        ),
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


def compute_fingerprint(
    skill_name: str,
    skill_version: str,
    finding: object,
    include_skill_version: bool = True,
) -> str:
    """Build the legacy publisher fingerprint for extension compatibility."""
    parts = [skill_name]
    if include_skill_version:
        parts.append(skill_version)
    parts.extend(
        [
            getattr(finding, "file_path", ""),
            _normalize(
                getattr(finding, "legacy_anchor_text", None)
                or getattr(finding, "anchor_text", None)
                or getattr(finding, "line_range", "")
            ),
            _normalize(getattr(finding, "description", "")),
        ]
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())
