import logging

import pytest
from pydantic import ValidationError

from code_review_bot.platforms.models import ChangeRequest
from code_review_bot.review.context import compute_fingerprint
from code_review_bot.review.models import ReviewTaskContext
from code_review_bot.skill.loader import load_skill
from code_review_bot.skill.protocol import Finding, SkillResult


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


def make_task_context() -> ReviewTaskContext:
    return ReviewTaskContext(
        change_request=make_change_request(),
        workspace_path="/tmp/review/source",
        source_branch="feature",
        target_branch="main",
        base_sha="base",
        start_sha="start",
        head_sha="head",
    )


def test_skill_result_accepts_valid_findings() -> None:
    result = SkillResult(
        summary="Found one issue",
        findings=[
            Finding(
                severity="high",
                description="Risk",
                file_path="a.py",
                line_range="1",
                anchor_text="print",
                reason="Changed line is unsafe",
                confidence=90,
            )
        ],
    )

    assert result.findings[0].severity == "high"


def test_skill_result_rejects_invalid_severity() -> None:
    with pytest.raises(ValidationError):
        SkillResult(
            summary="bad",
            findings=[
                {
                    "severity": "urgent",
                    "description": "Risk",
                    "file_path": "a.py",
                    "line_range": "1",
                    "anchor_text": "print",
                    "reason": "Changed line is unsafe",
                    "confidence": 90,
                }
            ],
        )


def test_finding_accepts_uppercase_severity() -> None:
    f = Finding(
        severity="MEDIUM",
        description="x",
        file_path="a.py",
        line_range="1",
        reason="y",
        confidence=50,
    )
    assert f.severity == "medium"


def test_finding_accepts_text_confidence_levels() -> None:
    assert (
        Finding(
            severity="low",
            description="x",
            file_path="a.py",
            line_range="1",
            reason="y",
            confidence="HIGH",
        ).confidence
        == 85
    )
    assert (
        Finding(
            severity="high",
            description="x",
            file_path="a.py",
            line_range="1",
            reason="y",
            confidence="67%",
        ).confidence
        == 67
    )


def test_finding_accepts_structured_line_range() -> None:
    finding = Finding(
        severity="high",
        description="x",
        file_path="a.py",
        line_range={"start": 12, "end": 14},
        reason="y",
        confidence=90,
    )

    assert finding.line_range == "12-14"


def test_nested_finding_validation_preserves_legacy_anchor_state() -> None:
    raw_anchor = "first changed line\nsecond changed line"
    finding = Finding(
        severity="high",
        description="Risk",
        file_path="a.py",
        line_range="1-2",
        anchor_text=raw_anchor,
        reason="Changed lines are unsafe",
        confidence=90,
    )
    original_fingerprint = compute_fingerprint("default", "1", finding)
    result = SkillResult(summary="Review", findings=[finding])

    result_finding = result.findings[0]
    assert result_finding.legacy_anchor_text == raw_anchor
    assert finding.legacy_anchor_text == raw_anchor
    assert compute_fingerprint("default", "1", result_finding) == original_fingerprint

    base_context = make_task_context()

    context = ReviewTaskContext.model_validate(
        {
            **base_context.model_dump(mode="json"),
            "previous_unlocated_findings": [finding],
        }
    )

    restored = context.previous_unlocated_findings[0]
    assert restored.legacy_anchor_text == raw_anchor
    assert finding.legacy_anchor_text == raw_anchor
    assert compute_fingerprint("default", "1", restored) == original_fingerprint


@pytest.mark.asyncio
async def test_default_skill_runs_without_gitlab_access(sample_skill_dir: object) -> None:
    skill = load_skill(str(sample_skill_dir))
    context = make_task_context()

    prompt = skill.build_prompt(context)

    assert skill.name == "code-review"
    assert skill.version
    assert "SkillResult" in prompt
    assert "Fix bug" in prompt


def test_native_skill_prompt_has_contract_but_no_skill_section() -> None:
    from code_review_bot.skill.filesystem import NativeKnowledgeSkill

    skill = NativeKnowledgeSkill()
    context = make_task_context()
    prompt = skill.build_prompt(context)

    assert "# System output contract" in prompt
    assert "SkillResult" in prompt
    assert "Review skill" not in prompt


def test_loader_missing_skill_md_falls_back_to_native(
    tmp_path: object, caplog: pytest.LogCaptureFixture
) -> None:
    from pathlib import Path

    from code_review_bot.skill.filesystem import NativeKnowledgeSkill

    empty_dir = Path(str(tmp_path)) / "empty-skill"
    empty_dir.mkdir()
    with caplog.at_level(logging.WARNING, logger="code_review_bot.skill.loader"):
        skill = load_skill(str(empty_dir))
    assert isinstance(skill, NativeKnowledgeSkill)
    assert "REVIEW_SKILL" in caplog.text
    assert "native" in caplog.text.lower() or "falling back" in caplog.text.lower()


def test_loader_nonexistent_path_falls_back_to_native(caplog: pytest.LogCaptureFixture) -> None:
    from code_review_bot.skill.filesystem import NativeKnowledgeSkill

    with caplog.at_level(logging.WARNING, logger="code_review_bot.skill.loader"):
        skill = load_skill("/nonexistent/path/to/skill")
    assert isinstance(skill, NativeKnowledgeSkill)
    assert "REVIEW_SKILL" in caplog.text


def test_loader_empty_path_returns_native_skill() -> None:
    from code_review_bot.skill.filesystem import NativeKnowledgeSkill

    skill = load_skill("")
    assert isinstance(skill, NativeKnowledgeSkill)
    assert skill.name == "native"
    assert skill.additional_directories == []


def test_loader_accepts_skill_md_path_and_uses_parent_dir(tmp_path: object) -> None:
    from pathlib import Path

    from code_review_bot.skill.filesystem import FilesystemMarkdownSkill

    skill_dir = Path(str(tmp_path)) / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: x\n---\n\n# x\n", encoding="utf-8")
    skill = load_skill(str(skill_dir / "SKILL.md"))
    assert isinstance(skill, FilesystemMarkdownSkill)
    assert skill.name == "my-skill"


def test_loader_accepts_absolute_local_path(tmp_path: object) -> None:
    from pathlib import Path

    from code_review_bot.skill.filesystem import FilesystemMarkdownSkill

    skill_dir = Path(str(tmp_path)) / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: x\n---\n\n# x\n", encoding="utf-8")
    skill = load_skill(str(skill_dir))
    assert isinstance(skill, FilesystemMarkdownSkill)
    assert skill.name == "my-skill"


def test_loader_returns_remote_url_skill_for_https_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from code_review_bot.skill import loader
    from code_review_bot.skill.filesystem import RemoteUrlSkill

    monkeypatch.setattr(loader, "_url_reachable", lambda _url: True)
    skill = load_skill("https://github.com/whhe/ai-workshop/blob/main/skills/code-review/SKILL.md")
    assert isinstance(skill, RemoteUrlSkill)
    assert skill.name == "code-review"
    assert skill.version  # non-empty hash
    assert skill.additional_directories == []


def test_loader_url_not_reachable_falls_back_to_native(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from code_review_bot.skill import loader
    from code_review_bot.skill.filesystem import NativeKnowledgeSkill

    monkeypatch.setattr(loader, "_url_reachable", lambda _url: False)
    url = "https://example.com/skills/my-skill/SKILL.md"
    with caplog.at_level(logging.WARNING, logger="code_review_bot.skill.loader"):
        skill = load_skill(url)
    assert isinstance(skill, NativeKnowledgeSkill)
    assert url in caplog.text
    assert "falling back" in caplog.text.lower()


def test_remote_url_skill_prompt_contains_url_and_fetch_instruction() -> None:
    from code_review_bot.skill.filesystem import RemoteUrlSkill

    url = "https://github.com/whhe/ai-workshop/blob/main/skills/code-review/SKILL.md"
    skill = RemoteUrlSkill(url)
    prompt = skill.build_prompt(make_task_context())
    assert url in prompt
    assert "fetch" in prompt.lower()
    assert "SkillResult" in prompt


def test_remote_url_skill_name_strips_skill_md_suffix() -> None:
    from code_review_bot.skill.filesystem import RemoteUrlSkill

    skill = RemoteUrlSkill(
        "https://github.com/whhe/ai-workshop/blob/main/skills/code-review/SKILL.md"
    )
    assert skill.name == "code-review"


def test_remote_url_skill_name_from_directory_url() -> None:
    from code_review_bot.skill.filesystem import RemoteUrlSkill

    skill = RemoteUrlSkill("https://github.com/whhe/ai-workshop/tree/main/skills/code-review")
    assert skill.name == "code-review"


def test_loader_whitespace_only_path_returns_native_skill() -> None:
    from code_review_bot.skill.filesystem import NativeKnowledgeSkill

    skill = load_skill("   ")
    assert isinstance(skill, NativeKnowledgeSkill)


def test_loader_resolves_relative_path_against_project_root(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from code_review_bot.skill.filesystem import FilesystemMarkdownSkill

    root = Path(str(tmp_path))
    skill_dir = root / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: x\n---\n\n# x\n", encoding="utf-8")
    monkeypatch.setattr("code_review_bot.skill.loader.project_root", lambda: root)
    skill = load_skill("my-skill")
    assert isinstance(skill, FilesystemMarkdownSkill)
    assert skill.name == "my-skill"


def test_loader_expands_user_home(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    from code_review_bot.skill.filesystem import FilesystemMarkdownSkill

    home = Path(str(tmp_path))
    skill_dir = home / "my-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text("---\nname: x\n---\n\n# x\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    skill = load_skill("~/my-skill")
    assert isinstance(skill, FilesystemMarkdownSkill)
    assert skill.name == "my-skill"
