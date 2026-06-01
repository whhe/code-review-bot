from code_review_bot.review.context import (
    compute_fingerprint,
    extract_metadata,
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
    assert metadata.fingerprints == {"abc"}


def test_extract_metadata_returns_none_when_no_comment() -> None:
    metadata = extract_metadata([{"id": 1, "body": "no metadata here"}])
    assert metadata is None


def test_extract_metadata_tolerates_malformed_json_and_continues() -> None:
    bad_body = "<!-- code-review-bot:{invalid json} -->"
    good_body = (
        '<!-- code-review-bot:{"head_sha":"ok","skill":"s","version":"1","fingerprints":[]} -->'
    )
    # The bad note is iterated last (reversed), so the good note should be returned.
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


def test_fingerprint_is_stable_for_equivalent_finding_text() -> None:
    finding = Finding(
        severity="medium",
        description="  Same   issue ",
        file_path="a.py",
        line_range="3",
        anchor_text=" value.name ",
        reason="reason",
        confidence=80,
    )

    assert compute_fingerprint("default", "1", finding) == compute_fingerprint(
        "default", "1", finding
    )


def test_fingerprint_differs_across_skills() -> None:
    finding = Finding(
        severity="high",
        description="issue",
        file_path="a.py",
        line_range="1",
        reason="r",
        confidence=90,
    )

    assert compute_fingerprint("skill-a", "1", finding) != compute_fingerprint(
        "skill-b", "1", finding
    )
