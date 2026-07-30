from types import SimpleNamespace

from code_review_bot.review.context import compute_fingerprint, extract_metadata
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
