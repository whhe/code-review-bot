import json
import logging
from types import SimpleNamespace

import pytest

from code_review_bot.review.context import (
    compute_fingerprint,
    extract_metadata,
    limit_finding_history,
)
from code_review_bot.skill.protocol import Finding


def test_extract_metadata_reads_hidden_bot_comment() -> None:
    body = (
        '<!-- code-review-bot:{"head_sha":"old","skill":"default","version":"1",'
        '"fingerprints":["abc"]} -->'
    )

    metadata = extract_metadata([{"id": 1, "body": body}])

    assert metadata is not None
    assert metadata.note_id == 1
    assert metadata.head_sha == "old"
    assert metadata.unlocated_findings == []


def test_fingerprint_preserves_raw_anchor_compatibility_after_normalization() -> None:
    raw_anchor = "first changed line\nsecond changed line"
    legacy_finding = SimpleNamespace(
        file_path="src/a.py",
        line_range="10",
        anchor_text=raw_anchor,
        description="same issue",
    )
    current_finding = Finding(
        severity="high",
        description="same issue",
        file_path="src/a.py",
        line_range="10",
        anchor_text=raw_anchor,
        reason="reason",
        confidence=90,
    )

    assert current_finding.anchor_text == "first changed line"
    assert compute_fingerprint("default", "1", current_finding) == compute_fingerprint(
        "default", "1", legacy_finding
    )


def test_extract_metadata_reads_unlocated_finding_history() -> None:
    body = (
        '<!-- code-review-bot:{"head_sha":"old","skill":"default","version":"1",'
        '"unlocated_findings":[{"severity":"high","description":"summary-only issue",'
        '"file_path":"src/a.py","line_range":"outside diff","anchor_text":"",'
        '"reason":"No diff position","confidence":90}]} -->'
    )

    metadata = extract_metadata([{"id": 1, "body": body}])

    assert metadata is not None
    assert len(metadata.unlocated_findings) == 1
    assert metadata.unlocated_findings[0].description == "summary-only issue"


def test_extract_metadata_merges_history_from_concurrent_summaries() -> None:
    first = (
        '<!-- code-review-bot:{"head_sha":"head","skill":"default","version":"1",'
        '"unlocated_findings":[{"severity":"high","description":"issue A",'
        '"file_path":"src/a.py","line_range":"1","anchor_text":"",'
        '"reason":"reason A","confidence":90}]} -->'
    )
    second = (
        '<!-- code-review-bot:{"head_sha":"head","skill":"default","version":"1",'
        '"unlocated_findings":[{"severity":"high","description":"issue B",'
        '"file_path":"src/b.py","line_range":"2","anchor_text":"",'
        '"reason":"reason B","confidence":90}]} -->'
    )

    metadata = extract_metadata(
        [
            {"id": 1, "body": first},
            {"id": 2, "body": second},
        ]
    )

    assert metadata is not None
    assert [finding.description for finding in metadata.unlocated_findings] == [
        "issue A",
        "issue B",
    ]


def test_extract_metadata_preserves_finding_history_across_heads() -> None:
    previous_head = (
        '<!-- code-review-bot:{"head_sha":"head-1","skill":"default","version":"1",'
        '"unlocated_findings":[{"severity":"high","description":"fixed issue",'
        '"file_path":"src/a.py","line_range":"1","anchor_text":"",'
        '"reason":"reason","confidence":90}]} -->'
    )
    latest_head = (
        '<!-- code-review-bot:{"head_sha":"head-2","skill":"default","version":"1",'
        '"unlocated_findings":[]} -->'
    )

    metadata = extract_metadata(
        [
            {"id": 1, "body": previous_head},
            {"id": 2, "body": latest_head},
        ]
    )

    assert metadata is not None
    assert metadata.head_sha == "head-2"
    assert [finding.description for finding in metadata.unlocated_findings] == ["fixed issue"]


def test_extract_metadata_bounds_history_while_retaining_newest_findings() -> None:
    notes: list[dict[str, object]] = []
    findings: list[Finding] = []
    for index in range(50):
        finding = Finding(
            severity="medium",
            description=f"issue {index}: " + "d" * 2_000,
            file_path=f"src/{index}.py",
            line_range=str(index + 1),
            anchor_text="",
            reason="r" * 2_000,
            confidence=80,
        )
        findings.append(finding)
        payload = {
            "schema_version": 2,
            "head_sha": f"head-{index}",
            "skill": "default",
            "version": "1",
            "unlocated_findings": [finding.model_dump(mode="json")],
        }
        notes.append(
            {
                "id": index + 1,
                "body": (
                    "<!-- code-review-bot:" + json.dumps(payload, separators=(",", ":")) + " -->"
                ),
            }
        )

    metadata = extract_metadata(notes)

    assert metadata is not None
    assert len(metadata.unlocated_findings) < len(findings)
    assert metadata.unlocated_findings[-1] == findings[-1]


def test_extract_metadata_deduplicates_without_quadratic_finding_equality(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    equality_calls = 0
    original_eq = Finding.__eq__

    def counting_eq(self: Finding, other: object) -> bool:
        nonlocal equality_calls
        equality_calls += 1
        return original_eq(self, other)

    monkeypatch.setattr(Finding, "__eq__", counting_eq)
    notes = []
    for index in range(100):
        finding = Finding(
            severity="medium",
            description=f"issue {index}",
            file_path=f"src/{index}.py",
            line_range=str(index + 1),
            anchor_text="anchor",
            reason="reason",
            confidence=80,
        )
        payload = {
            "schema_version": 2,
            "head_sha": "head",
            "skill": "default",
            "version": "1",
            "unlocated_findings": [finding.model_dump(mode="json")],
        }
        notes.append(
            {
                "id": index + 1,
                "body": (
                    "<!-- code-review-bot:" + json.dumps(payload, separators=(",", ":")) + " -->"
                ),
            }
        )
    duplicate_payload = {
        "schema_version": 2,
        "head_sha": "head",
        "skill": "default",
        "version": "1",
        "unlocated_findings": [finding.model_dump(mode="json")],
    }
    notes.append(
        {
            "id": 101,
            "body": (
                "<!-- code-review-bot:"
                + json.dumps(duplicate_payload, separators=(",", ":"))
                + " -->"
            ),
        }
    )

    metadata = extract_metadata(notes)

    assert metadata is not None
    assert equality_calls < 500
    descriptions = [finding.description for finding in metadata.unlocated_findings]
    assert len(descriptions) == 50
    assert descriptions.count("issue 99") == 1
    assert descriptions[-1] == "issue 99"


def test_extract_metadata_deduplicates_legacy_full_and_compacted_findings() -> None:
    legacy_full = Finding(
        severity="medium",
        description="d" * 5_000,
        file_path="src/a.py",
        line_range="10",
        anchor_text="anchor",
        reason="reason",
        confidence=80,
    )
    compacted = limit_finding_history([legacy_full])[0]

    def note(note_id: int, finding: Finding) -> dict[str, object]:
        payload = {
            "schema_version": 2,
            "head_sha": "head",
            "skill": "default",
            "version": "1",
            "unlocated_findings": [finding.model_dump(mode="json")],
        }
        return {
            "id": note_id,
            "body": "<!-- code-review-bot:" + json.dumps(payload, separators=(",", ":")) + " -->",
        }

    metadata = extract_metadata([note(1, legacy_full), note(2, compacted)])

    assert metadata is not None
    assert metadata.unlocated_findings == [compacted]


def test_finding_history_warning_describes_all_retention_drops(
    caplog: pytest.LogCaptureFixture,
) -> None:
    findings = [
        Finding(
            severity="low",
            description=f"issue {index}",
            file_path="src/a.py",
            line_range=str(index),
            anchor_text="",
            reason="reason",
            confidence=70,
        )
        for index in range(51)
    ]

    with caplog.at_level(logging.WARNING):
        retained = limit_finding_history(findings)

    assert len(retained) == 50
    assert "Dropped 1 review metadata findings outside the retention budgets" in caplog.text


def test_extract_metadata_selects_history_for_requested_skill_version() -> None:
    skill_a_first = (
        '<!-- code-review-bot:{"head_sha":"head-1","skill":"skill-a","version":"1",'
        '"unlocated_findings":[{"severity":"high","description":"skill A issue",'
        '"file_path":"src/a.py","line_range":"1","anchor_text":"",'
        '"reason":"reason","confidence":90}]} -->'
    )
    skill_b = (
        '<!-- code-review-bot:{"head_sha":"head-1","skill":"skill-b","version":"1",'
        '"unlocated_findings":[]} -->'
    )
    skill_a_latest = (
        '<!-- code-review-bot:{"head_sha":"head-2","skill":"skill-a","version":"1",'
        '"unlocated_findings":[]} -->'
    )

    metadata = extract_metadata(
        [
            {"id": 1, "body": skill_a_first},
            {"id": 2, "body": skill_b},
            {"id": 3, "body": skill_a_latest},
        ],
        skill_name="skill-a",
        skill_version="1",
    )

    assert metadata is not None
    assert metadata.skill == "skill-a"
    assert metadata.head_sha == "head-2"
    assert [finding.description for finding in metadata.unlocated_findings] == ["skill A issue"]


def test_extract_metadata_isolates_history_to_latest_skill_version_without_full_filters() -> None:
    def metadata_body(skill: str, version: str, description: str) -> str:
        return (
            '<!-- code-review-bot:{"head_sha":"head","skill":"'
            + skill
            + '","version":"'
            + version
            + '","unlocated_findings":[{"severity":"high","description":"'
            + description
            + '","file_path":"src/a.py","line_range":"1","anchor_text":"",'
            '"reason":"reason","confidence":90}]} -->'
        )

    notes = [
        {"id": 1, "body": metadata_body("skill-a", "1", "skill A v1 issue")},
        {"id": 2, "body": metadata_body("skill-b", "1", "skill B issue")},
        {"id": 3, "body": metadata_body("skill-a", "2", "skill A v2 issue")},
    ]

    unfiltered = extract_metadata(notes)
    skill_filtered = extract_metadata(notes, skill_name="skill-a")

    assert unfiltered is not None
    assert unfiltered.skill == "skill-a"
    assert unfiltered.version == "2"
    assert [finding.description for finding in unfiltered.unlocated_findings] == [
        "skill A v2 issue"
    ]
    assert skill_filtered is not None
    assert skill_filtered.version == "2"
    assert [finding.description for finding in skill_filtered.unlocated_findings] == [
        "skill A v2 issue"
    ]


def test_extract_metadata_returns_none_when_no_comment() -> None:
    metadata = extract_metadata([{"id": 1, "body": "no metadata here"}])
    assert metadata is None


def test_extract_metadata_tolerates_malformed_json_and_continues() -> None:
    bad_body = "<!-- code-review-bot:{invalid json} -->"
    good_body = (
        '<!-- code-review-bot:{"head_sha":"ok","skill":"s","version":"1","fingerprints":[]} -->'
    )
    # A malformed newer note must not hide the most recent valid metadata.
    metadata = extract_metadata(
        [
            {"id": 1, "body": good_body},
            {"id": 2, "body": bad_body},
        ]
    )
    assert metadata is not None
    assert metadata.head_sha == "ok"


def test_extract_metadata_returns_none_when_all_notes_have_malformed_json() -> None:
    bad_body = "<!-- code-review-bot:{not valid} -->"
    metadata = extract_metadata([{"id": 1, "body": bad_body}])
    assert metadata is None


def test_extract_metadata_picks_most_recent_when_multiple_notes() -> None:
    old_body = (
        '<!-- code-review-bot:{"head_sha":"old","skill":"default","version":"1",'
        '"fingerprints":[]} -->'
    )
    new_body = (
        '<!-- code-review-bot:{"head_sha":"new","skill":"default","version":"1",'
        '"fingerprints":[]} -->'
    )

    metadata = extract_metadata(
        [
            {"id": 1, "body": old_body},
            {"id": 2, "body": new_body},
        ]
    )

    assert metadata is not None
    assert metadata.head_sha == "new"
