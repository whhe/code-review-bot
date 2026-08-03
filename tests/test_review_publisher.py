import pytest

from code_review_bot.platforms.models import ChangeRequest, InlinePosition
from code_review_bot.review.context import extract_metadata
from code_review_bot.review.publish.debug import DebugMarkdownPublisher
from code_review_bot.review.publish.formatter import BOT_METADATA_PREFIX, format_review_note
from code_review_bot.review.publish.platform import PlatformPublisher
from code_review_bot.skill.protocol import Finding, RuntimeMetadata, SkillResult


def make_change_request() -> ChangeRequest:
    return ChangeRequest(
        project_ref="1",
        cr_id="5",
        title="Fix bug",
        description="",
        author="alice",
        source_branch="feature",
        target_branch="main",
        state="opened",
        draft=False,
        web_url="https://gitlab.test/mr/5",
        head_sha="head",
        diff_refs={"base_sha": "base", "start_sha": "start", "head_sha": "head"},
    )


def make_finding(**overrides: object) -> Finding:
    data: dict[str, object] = {
        "severity": "high",
        "description": "A risky pattern",
        "file_path": "a.py",
        "line_range": "10",
        "anchor_text": "some_value",
        "reason": "Changed line is unsafe",
        "confidence": 90,
    }
    data.update(overrides)
    return Finding(**data)


def make_runtime(**overrides: object) -> RuntimeMetadata:
    data: dict[str, object] = {
        "agent_type": "opencode",
        "model": "provider/model",
        "input_tokens": 12345,
        "output_tokens": 678,
        "total_tokens": 13023,
    }
    data.update(overrides)
    return RuntimeMetadata(**data)


class FakeAdapter:
    """In-memory PlatformAdapter for tests."""

    def __init__(self) -> None:
        self.summaries_posted: list[str] = []
        self.inline_bodies: list[str] = []
        self.inline_positions: list[InlinePosition] = []

    async def publish_summary(self, project_ref: str, cr_id: str, body: str) -> dict[str, object]:
        self.summaries_posted.append(body)
        return {"id": len(self.summaries_posted)}

    async def publish_inline_comment(
        self,
        project_ref: str,
        cr_id: str,
        body: str,
        position: InlinePosition,
    ) -> dict[str, object]:
        self.inline_bodies.append(body)
        self.inline_positions.append(position)
        return {"id": "discussion"}


class FailingInlineAdapter(FakeAdapter):
    async def publish_inline_comment(
        self,
        project_ref: str,
        cr_id: str,
        body: str,
        position: InlinePosition,
    ) -> dict[str, object]:
        raise RuntimeError("inline comment rejected")


@pytest.mark.asyncio
async def test_publisher_creates_inline_from_agent_line() -> None:
    adapter = FakeAdapter()
    publisher = PlatformPublisher(adapter)

    outcome = await publisher.publish(
        make_change_request(),
        SkillResult(summary="Reviewed", findings=[make_finding()]),
        skill_name="default",
        skill_version="1",
    )

    assert outcome.published is True
    assert outcome.inline_comments == 1
    assert adapter.inline_positions[0].new_line == 10
    assert adapter.inline_positions[0].file_path == "a.py"
    assert adapter.inline_positions[0].base_sha == "base"
    assert "A risky pattern" in adapter.inline_bodies[0]
    assert len(adapter.summaries_posted) == 1


@pytest.mark.asyncio
async def test_publisher_moves_finding_to_unlocated_when_inline_fails() -> None:
    adapter = FailingInlineAdapter()
    publisher = PlatformPublisher(adapter)

    outcome = await publisher.publish(
        make_change_request(),
        SkillResult(summary="Reviewed", findings=[make_finding()]),
        skill_name="default",
        skill_version="1",
    )

    assert outcome.inline_comments == 0
    assert "A risky pattern" in adapter.summaries_posted[0]
    assert '"unlocated_findings":[{"severity":"high"' in adapter.summaries_posted[0]


@pytest.mark.asyncio
async def test_publisher_records_only_current_unlocated_findings_in_each_summary() -> None:
    adapter = FailingInlineAdapter()
    publisher = PlatformPublisher(adapter)

    await publisher.publish(
        make_change_request(),
        SkillResult(summary="First review", findings=[make_finding()]),
        skill_name="default",
        skill_version="1",
    )
    first_summary = adapter.summaries_posted[0]

    await publisher.publish(
        make_change_request(),
        SkillResult(summary="Second review", findings=[]),
        skill_name="default",
        skill_version="1",
        existing_notes=[{"id": 1, "body": first_summary}],
    )

    second_summary = adapter.summaries_posted[1]
    assert '"unlocated_findings":[]' in second_summary
    assert "A risky pattern" not in second_summary

    metadata = extract_metadata(
        [
            {"id": 1, "body": first_summary},
            {"id": 2, "body": second_summary},
        ]
    )
    assert metadata is not None
    assert metadata.unlocated_findings == [make_finding()]


@pytest.mark.asyncio
async def test_publisher_records_metadata_only_findings_without_visible_summary_entry() -> None:
    adapter = FakeAdapter()
    publisher = PlatformPublisher(adapter)
    finding = make_finding()

    await publisher.publish(
        make_change_request(),
        SkillResult(summary="Migration review", findings=[]),
        skill_name="default",
        skill_version="1",
        metadata_findings=[finding],
    )

    body = adapter.summaries_posted[0]
    visible_body = body.split(BOT_METADATA_PREFIX, 1)[0]
    metadata = extract_metadata([{"id": 1, "body": body}])
    assert "A risky pattern" not in visible_body
    assert metadata is not None
    assert metadata.unlocated_findings == [finding]


@pytest.mark.asyncio
async def test_publisher_drops_unlocated_history_from_different_skill_version() -> None:
    adapter = FailingInlineAdapter()
    publisher = PlatformPublisher(adapter)
    previous_summary = format_review_note(
        cr=make_change_request(),
        skill_name="old-skill",
        skill_version="1",
        metadata_findings=[make_finding()],
    )

    await publisher.publish(
        make_change_request(),
        SkillResult(summary="New skill review", findings=[]),
        skill_name="new-skill",
        skill_version="2",
        existing_notes=[{"id": 1, "body": previous_summary}],
    )

    assert "A risky pattern" not in adapter.summaries_posted[0]


@pytest.mark.asyncio
async def test_publisher_always_creates_summary_note() -> None:
    adapter = FakeAdapter()
    publisher = PlatformPublisher(adapter)

    await publisher.publish(
        make_change_request(),
        SkillResult(summary="Reviewed", findings=[]),
        skill_name="default",
        skill_version="1",
    )

    assert len(adapter.summaries_posted) == 1
    assert BOT_METADATA_PREFIX in adapter.summaries_posted[0]


@pytest.mark.asyncio
async def test_publisher_can_defer_summary_to_platform_review() -> None:
    adapter = FakeAdapter()
    publisher = PlatformPublisher(adapter)

    outcome = await publisher.publish(
        make_change_request(),
        SkillResult(summary="Reviewed", findings=[]),
        skill_name="default",
        skill_version="1",
        publish_summary=False,
    )

    assert adapter.summaries_posted == []
    assert "Reviewed" in outcome.review_body
    assert BOT_METADATA_PREFIX in outcome.review_body


@pytest.mark.asyncio
async def test_formatter_includes_review_metadata() -> None:
    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        severity_counts={"critical": 0, "high": 1, "medium": 0, "low": 0},
        located_count=1,
        unlocated_findings=[],
        skill_name="default",
        skill_version="1",
    )

    assert BOT_METADATA_PREFIX in body
    assert '"head_sha":"head"' in body
    assert '"fingerprints"' not in body


def test_formatter_round_trips_legacy_fingerprints_when_provided() -> None:
    body = format_review_note(
        cr=make_change_request(),
        skill_name="default",
        skill_version="1",
        fingerprints=["fp-a", "fp-b"],
    )

    metadata = extract_metadata([{"id": 1, "body": body}])

    assert metadata is not None
    assert metadata.schema_version == 2
    assert metadata.fingerprints == {"fp-a", "fp-b"}


def test_formatter_keeps_agent_text_inside_metadata_comment() -> None:
    finding = make_finding(description="Literal } --> must not terminate metadata")

    body = format_review_note(
        cr=make_change_request(),
        skill_name="default",
        skill_version="1",
        metadata_findings=[finding],
    )

    metadata = extract_metadata([{"id": 1, "body": body}])
    assert body.count("-->") == 1
    assert metadata is not None
    assert metadata.unlocated_findings == [finding]


def test_formatter_bounds_large_review_note_and_preserves_valid_metadata() -> None:
    finding = make_finding(
        line_range="outside diff",
        description="d" * 40_000,
        reason="r" * 40_000,
    )

    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        unlocated_findings=[finding],
        metadata_findings=[finding],
        skill_name="default",
        skill_version="1",
    )

    metadata = extract_metadata([{"id": 1, "body": body}])
    assert len(body) <= 60_000
    assert "truncated sha256:" in body.split(BOT_METADATA_PREFIX, 1)[0]
    assert metadata is not None
    assert len(metadata.unlocated_findings) == 1
    assert metadata.unlocated_findings[0].description.startswith("d" * 100)
    assert "truncated sha256:" in metadata.unlocated_findings[0].description


def test_formatter_keeps_critical_findings_visible_when_output_is_bounded() -> None:
    low = make_finding(
        severity="low",
        line_range="outside diff",
        description="l" * 30_000,
        reason="r" * 30_000,
    )
    critical = make_finding(
        severity="critical",
        line_range="outside diff",
        description="SECOND-CRITICAL",
        reason="Critical behavior is unsafe",
    )

    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        unlocated_findings=[low, critical],
        metadata_findings=[low, critical],
        skill_name="default",
        skill_version="1",
    )

    visible_body = body.split(BOT_METADATA_PREFIX, 1)[0]
    assert len(body) <= 60_000
    assert "SECOND-CRITICAL" in visible_body
    assert "truncated sha256:" in visible_body


def test_formatter_bounds_legacy_fingerprints_within_review_note_limit() -> None:
    fingerprints = [f"{index:064x}" for index in range(2_000)]

    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        fingerprints=fingerprints,
        skill_name="default",
        skill_version="1",
    )

    metadata = extract_metadata([{"id": 1, "body": body}])
    assert len(body) <= 60_000
    assert metadata is not None
    assert len(metadata.fingerprints) < len(fingerprints)
    assert fingerprints[-1] in metadata.fingerprints


@pytest.mark.asyncio
async def test_formatter_includes_unlocated_finding() -> None:
    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        severity_counts={"critical": 0, "high": 1, "medium": 0, "low": 0},
        located_count=0,
        unlocated_findings=[make_finding()],
        skill_name="default",
        skill_version="1",
    )

    assert "A risky pattern" in body


@pytest.mark.asyncio
async def test_formatter_includes_runtime_line() -> None:
    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        severity_counts={"critical": 0, "high": 1, "medium": 0, "low": 0},
        located_count=1,
        unlocated_findings=[],
        skill_name="default",
        skill_version="1",
        runtime=make_runtime(),
    )

    assert "Model: provider/model · Tokens: input 12,345 / output 678 / total 13,023" in body


@pytest.mark.asyncio
async def test_formatter_renders_attribution_before_final_model_line() -> None:
    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        severity_counts={"critical": 0, "high": 1, "medium": 0, "low": 0},
        located_count=1,
        unlocated_findings=[],
        skill_name="default",
        skill_version="43b5df0c",
        runtime=make_runtime(model="qwen3.7-max"),
    )

    visible_lines = [line for line in body.splitlines() if not line.startswith(BOT_METADATA_PREFIX)]
    assert visible_lines[-2] == (
        "*Generated by* "
        "[*whhe/code-review-bot*](https://github.com/whhe/code-review-bot) "
        "*· Agent:* *OpenCode* *· Skill fingerprint:* *43b5df0c*"
    )
    assert visible_lines[-1] == (
        "Model: qwen3.7-max · Tokens: input 12,345 / output 678 / total 13,023"
    )


@pytest.mark.parametrize(
    ("agent_type", "expected"),
    [
        ("claude", "Claude Code"),
        ("codex", "Codex"),
        ("opencode", "OpenCode"),
        ("custom-agent", "custom-agent"),
    ],
)
def test_formatter_uses_agent_display_name(agent_type: str, expected: str) -> None:
    body = format_review_note(
        cr=make_change_request(),
        skill_name="default",
        skill_version="43b5df0c",
        runtime=make_runtime(agent_type=agent_type),
    )

    assert f"*· Agent:* *{expected}*" in body


@pytest.mark.asyncio
async def test_formatter_marks_unavailable_runtime_values() -> None:
    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        severity_counts={"critical": 0, "high": 1, "medium": 0, "low": 0},
        located_count=1,
        unlocated_findings=[],
        skill_name="default",
        skill_version="1",
        runtime=make_runtime(model=None, output_tokens=None),
    )

    assert "Model: unavailable · Tokens: input 12,345 / output unavailable / total 13,023" in body


@pytest.mark.asyncio
async def test_formatter_marks_unavailable_when_runtime_missing() -> None:
    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        severity_counts={"critical": 0, "high": 1, "medium": 0, "low": 0},
        located_count=1,
        unlocated_findings=[],
        skill_name="default",
        skill_version="1",
        runtime=None,
    )

    assert (
        "Model: unavailable · Tokens: input unavailable / output unavailable / total unavailable"
        in body
    )


@pytest.mark.asyncio
async def test_publisher_renders_resolved_findings() -> None:
    adapter = FakeAdapter()
    publisher = PlatformPublisher(adapter)
    resolved_finding = make_finding()

    outcome = await publisher.publish(
        make_change_request(),
        SkillResult(summary="Reviewed", findings=[]),
        skill_name="default",
        skill_version="1",
        resolved_findings=[resolved_finding],
    )

    assert outcome.inline_comments == 0
    assert adapter.inline_bodies == []
    assert "A risky pattern" in adapter.summaries_posted[0]


@pytest.mark.asyncio
async def test_publisher_no_resolved_section_when_empty() -> None:
    adapter = FakeAdapter()
    publisher = PlatformPublisher(adapter)

    outcome = await publisher.publish(
        make_change_request(),
        SkillResult(summary="Reviewed", findings=[make_finding()]),
        skill_name="default",
        skill_version="1",
        resolved_findings=[],
    )

    assert outcome.inline_comments == 1
    assert "Previously resolved" not in adapter.summaries_posted[0]


@pytest.mark.asyncio
async def test_debug_publisher_writes_findings_file(tmp_path: object) -> None:
    from pathlib import Path

    publisher = DebugMarkdownPublisher(output_dir=tmp_path)

    outcome = await publisher.publish(
        make_change_request(),
        SkillResult(summary="Review complete", findings=[make_finding()]),
        skill_name="default",
        skill_version="1",
    )

    out_file = Path(str(tmp_path)) / "project-1_cr-5.md"
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "A risky pattern" in content
    assert outcome.inline_comments == 1
