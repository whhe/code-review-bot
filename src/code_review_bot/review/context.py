import hashlib
import json
import logging
import re

from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

METADATA_RE = re.compile(r"<!-- code-review-bot:(?P<json>\{.*?\}) -->", re.DOTALL)


class BotMetadata(BaseModel):
    note_id: int | None = None
    head_sha: str = ""
    skill: str = ""
    version: str = ""
    fingerprints: set[str] = Field(default_factory=set)


def extract_metadata(notes: list[dict[str, object]]) -> BotMetadata | None:
    """Find the most recent bot metadata comment in a list of MR notes."""
    for note in reversed(notes):
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
        return metadata
    return None


def compute_fingerprint(
    skill_name: str,
    skill_version: str,
    finding: object,
    include_skill_version: bool = True,
) -> str:
    parts = [skill_name]
    if include_skill_version:
        parts.append(skill_version)
    parts.extend(
        [
            getattr(finding, "file_path", ""),
            _normalize(getattr(finding, "anchor_text", None) or getattr(finding, "line_range", "")),
            _normalize(getattr(finding, "description", "")),
        ]
    )
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())
