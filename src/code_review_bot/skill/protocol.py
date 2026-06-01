from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, field_validator

_SEVERITIES = ("critical", "high", "medium", "low")


class Finding(BaseModel):
    """A single review finding with location, severity, and explanatory text."""

    severity: Literal["low", "medium", "high", "critical"]
    description: str
    file_path: str
    line_range: str
    anchor_text: str = ""
    reason: str
    confidence: int = Field(ge=0, le=100)

    @field_validator("severity", mode="before")
    @classmethod
    def _normalize_severity(cls, value: Any) -> str:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _normalize_confidence(cls, value: Any) -> int:
        if isinstance(value, bool):
            return int(value) * 100
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(round(value))
        if isinstance(value, str):
            raw = value.strip()
            if raw.endswith("%"):
                raw = raw[:-1].strip()
            level = raw.upper()
            by_level = {
                "CRITICAL": 95,
                "HIGH": 85,
                "MEDIUM": 50,
                "LOW": 25,
                "NONE": 0,
            }
            if level in by_level:
                return by_level[level]
            try:
                return int(float(raw))
            except ValueError:
                pass
        raise ValueError(f"confidence must be 0-100 or a known level, got {value!r}")

    @field_validator("line_range", mode="before")
    @classmethod
    def _normalize_line_range(cls, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, int):
            return str(value)
        if isinstance(value, dict):
            start = value.get("start")
            end = value.get("end", start)
            if start is None:
                raise ValueError("line_range dict must include start")
            return f"{start}-{end}" if str(start) != str(end) else str(start)
        if isinstance(value, list | tuple) and value:
            start = value[0]
            end = value[-1]
            return f"{start}-{end}" if str(start) != str(end) else str(start)
        return value


class SkillResult(BaseModel):
    """Aggregated output from a review skill run: an overall summary and a list of findings."""

    summary: str
    findings: list[Finding] = Field(default_factory=list)


class ReviewSkill(Protocol):
    """Protocol for review skills that build a prompt from a task context."""

    name: str
    version: str

    def build_prompt(self, context: object) -> str:
        pass


def count_findings_by_severity(findings: list[Finding]) -> dict[str, int]:
    return {sev: sum(1 for f in findings if f.severity == sev) for sev in _SEVERITIES}
