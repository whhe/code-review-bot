import hashlib
import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

from code_review_bot.skill.protocol import Finding

logger = logging.getLogger(__name__)

METADATA_RE = re.compile(r"<!-- code-review-bot:(?P<json>\{.*?\}) -->", re.DOTALL)


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
    """Return the latest matching metadata with its complete finding history."""
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
    merged_findings: list[Finding] = []
    for metadata in matching:
        if metadata.skill != latest.skill or metadata.version != latest.version:
            continue
        merged_fingerprints.update(metadata.fingerprints)
        for finding in metadata.unlocated_findings:
            if finding not in merged_findings:
                merged_findings.append(finding)
    latest.fingerprints = merged_fingerprints
    latest.unlocated_findings = merged_findings
    return latest


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
