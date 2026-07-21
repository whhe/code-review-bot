import pytest

from code_review_bot.platforms.models import ChangeRequest, InlinePosition
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
        fingerprints=["fp1"],
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
        fingerprints=["fp1"],
    )

    assert outcome.inline_comments == 0
    assert "A risky pattern" in adapter.summaries_posted[0]


@pytest.mark.asyncio
async def test_publisher_always_creates_summary_note() -> None:
    adapter = FakeAdapter()
    publisher = PlatformPublisher(adapter)

    await publisher.publish(
        make_change_request(),
        SkillResult(summary="Reviewed", findings=[]),
        skill_name="default",
        skill_version="1",
        fingerprints=[],
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
        fingerprints=["fp1"],
        publish_summary=False,
    )

    assert adapter.summaries_posted == []
    assert "Reviewed" in outcome.review_body
    assert BOT_METADATA_PREFIX in outcome.review_body


@pytest.mark.asyncio
async def test_formatter_includes_metadata_for_incremental_dedupe() -> None:
    body = format_review_note(
        cr=make_change_request(),
        summary="Reviewed",
        severity_counts={"critical": 0, "high": 1, "medium": 0, "low": 0},
        located_count=1,
        unlocated_findings=[],
        skill_name="default",
        skill_version="1",
        fingerprints=["fp1"],
    )

    assert BOT_METADATA_PREFIX in body
    assert '"head_sha":"head"' in body
    assert '"fingerprints":["fp1"]' in body


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
        fingerprints=["fp1"],
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
        fingerprints=["fp1"],
        runtime=make_runtime(),
    )

    assert "Model: provider/model · Tokens: input 12,345 / output 678 / total 13,023" in body


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
        fingerprints=["fp1"],
        runtime=make_runtime(model=None, output_tokens=None),
    )

    assert (
        "Model: unavailable · Tokens: input 12,345 / output unavailable / total 13,023" in body
    )


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
        fingerprints=["fp1"],
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
        fingerprints=["fp1"],
        resolved_findings=[resolved_finding],
    )

    assert outcome.inline_comments == 0
    assert adapter.inline_bodies == []
    assert "A risky pattern" in adapter.summaries_posted[0]


@pytest.mark.asyncio
async def test_debug_publisher_writes_findings_file(tmp_path: object) -> None:
    from pathlib import Path

    publisher = DebugMarkdownPublisher(output_dir=tmp_path)

    outcome = await publisher.publish(
        make_change_request(),
        SkillResult(summary="Review complete", findings=[make_finding()]),
        skill_name="default",
        skill_version="1",
        fingerprints=["fp1"],
    )

    out_file = Path(str(tmp_path)) / "project-1_cr-5.md"
    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "A risky pattern" in content
    assert outcome.inline_comments == 1
