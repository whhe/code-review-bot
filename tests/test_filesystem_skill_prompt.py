from pathlib import Path

from code_review_bot.platforms.models import ChangeRequest, InlineThread
from code_review_bot.review.models import ReviewTaskContext
from code_review_bot.skill.filesystem import FilesystemMarkdownSkill
from code_review_bot.skill.loader import load_skill


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


def make_task_context(
    workspace: str = "/tmp/ws",
    output_language: str = "english",
) -> ReviewTaskContext:
    return ReviewTaskContext(
        change_request=make_change_request(),
        workspace_path=workspace,
        source_branch="feature",
        target_branch="main",
        base_sha="base123",
        start_sha="start123",
        head_sha="head123",
        output_language=output_language,
    )


def test_filesystem_skill_prompt_contains_system_contract(sample_skill_dir: Path) -> None:
    skill = load_skill(str(sample_skill_dir))
    context = make_task_context()

    prompt = skill.build_prompt(context)

    assert "# System output contract" in prompt
    assert "SkillResult" in prompt
    assert "summary" in prompt
    assert "findings" in prompt
    assert "confidence" in prompt


def test_filesystem_skill_prompt_contains_output_language(sample_skill_dir: Path) -> None:
    skill = load_skill(str(sample_skill_dir))
    context = make_task_context(output_language="chinese")

    prompt = skill.build_prompt(context)

    assert "chinese" in prompt


def test_filesystem_skill_prompt_default_output_language_is_english(
    sample_skill_dir: Path,
) -> None:
    skill = load_skill(str(sample_skill_dir))
    context = make_task_context()

    prompt = skill.build_prompt(context)

    assert "english" in prompt


def test_filesystem_skill_prompt_contains_workspace_and_refs(sample_skill_dir: Path) -> None:
    skill = load_skill(str(sample_skill_dir))
    context = make_task_context(workspace="/tmp/review/source")

    prompt = skill.build_prompt(context)

    assert "/tmp/review/source" in prompt
    assert "feature" in prompt
    assert "main" in prompt
    assert "base123" in prompt
    assert "head123" in prompt


def test_filesystem_skill_prompt_contains_skill_dir(sample_skill_dir: Path) -> None:
    skill = load_skill(str(sample_skill_dir))
    context = make_task_context()

    prompt = skill.build_prompt(context)

    assert str(skill.skill_dir) in prompt
    assert "SKILL.md" in prompt


def test_filesystem_skill_prompt_requires_readonly(sample_skill_dir: Path) -> None:
    skill = load_skill(str(sample_skill_dir))
    context = make_task_context()

    prompt = skill.build_prompt(context)

    assert "read-only" in prompt.lower() or "DO NOT modify" in prompt


def test_filesystem_skill_prompt_does_not_embed_skill_md_body(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test\ndescription: x\n---\n\nSECRET_METHODOLOGY_BODY",
        encoding="utf-8",
    )
    skill = FilesystemMarkdownSkill(skill_dir, "test-skill")
    context = make_task_context()

    prompt = skill.build_prompt(context)

    assert "SECRET_METHODOLOGY_BODY" not in prompt


def test_filesystem_skill_prompt_lists_local_references_without_inlining(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    references_dir = skill_dir / "references"
    references_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: review\ndescription: test\n---\n\nLoad security-checklist.md when needed.",
        encoding="utf-8",
    )
    reference_path = references_dir / "security-checklist.md"
    reference_path.write_text(
        "SECRET REFERENCE BODY SHOULD NOT BE EMBEDDED",
        encoding="utf-8",
    )
    skill = FilesystemMarkdownSkill(skill_dir, "test-skill")
    context = make_task_context()

    prompt = skill.build_prompt(context)

    assert skill.additional_directories == [str(skill_dir.resolve())]
    assert "# Local skill references" in prompt
    assert "security-checklist.md" in prompt
    assert str(reference_path.resolve()) in prompt
    assert "do not apply the corresponding methodology step without reading it first" in prompt
    assert "SECRET REFERENCE BODY SHOULD NOT BE EMBEDDED" not in prompt


def _make_context_with_threads(threads):
    """Helper: minimal ReviewTaskContext with inline_threads."""
    from unittest.mock import MagicMock

    ctx = MagicMock()
    ctx.change_request.cr_id = "1"
    ctx.change_request.title = "Test PR"
    ctx.change_request.author = "alice"
    ctx.change_request.source_branch = "feature"
    ctx.change_request.target_branch = "main"
    ctx.change_request.state = "open"
    ctx.change_request.web_url = "https://example.com/pr/1"
    ctx.change_request.description = ""
    ctx.workspace_path = "/tmp/repo"
    ctx.source_branch = "feature"
    ctx.target_branch = "main"
    ctx.base_sha = "base"
    ctx.start_sha = "base"
    ctx.head_sha = "head"
    ctx.previous_head_sha = ""
    ctx.output_language = "english"
    ctx.excluded_patterns = []
    ctx.included_patterns = []
    ctx.inline_threads = threads
    return ctx


def test_prompt_omits_thread_section_when_no_threads() -> None:
    from code_review_bot.skill.filesystem import build_review_prompt

    prompt = build_review_prompt(_make_context_with_threads([]), "https://example.com/skill")
    assert "Existing inline review comments" not in prompt
    assert "<inline_threads>" not in prompt


def test_prompt_inline_threads_follows_mr_description() -> None:
    from code_review_bot.skill.filesystem import build_review_prompt

    threads = [
        InlineThread(file_path="a.py", line_range="1", description="issue", is_resolved=False),
    ]
    prompt = build_review_prompt(_make_context_with_threads(threads), "https://example.com/skill")
    mr_desc_end = prompt.index("</mr_description>")
    inline_start = prompt.index("<inline_threads>")
    assert inline_start > mr_desc_end


def test_prompt_includes_rules_header() -> None:
    from code_review_bot.skill.filesystem import build_review_prompt

    threads = [
        InlineThread(
            file_path="src/a.py", line_range="10", description="null deref", is_resolved=True
        ),
    ]
    prompt = build_review_prompt(_make_context_with_threads(threads), "https://example.com/skill")
    assert "<inline_threads>" in prompt
    assert "</inline_threads>" in prompt
    assert "Existing inline review comments" in prompt
    assert "Explicit no-action" in prompt
    assert "Resolved but incomplete fix" in prompt


def test_prompt_shows_resolved_status_inline() -> None:
    from code_review_bot.skill.filesystem import build_review_prompt

    threads = [
        InlineThread(
            file_path="src/a.py", line_range="10", description="null deref", is_resolved=True
        ),
    ]
    prompt = build_review_prompt(_make_context_with_threads(threads), "https://example.com/skill")
    assert "src/a.py:10" in prompt
    assert "*(resolved)*" in prompt
    assert "null deref" in prompt


def test_prompt_shows_open_status_with_replies() -> None:
    from code_review_bot.skill.filesystem import build_review_prompt

    threads = [
        InlineThread(
            file_path="src/b.py",
            line_range="5",
            description="sql injection",
            replies=["we use params now"],
            is_resolved=False,
        ),
    ]
    prompt = build_review_prompt(_make_context_with_threads(threads), "https://example.com/skill")
    assert "src/b.py:5" in prompt
    assert "*(open)*" in prompt
    assert "sql injection" in prompt
    assert "we use params now" in prompt


def test_prompt_shows_mixed_statuses_in_single_list() -> None:
    from code_review_bot.skill.filesystem import build_review_prompt

    threads = [
        InlineThread(file_path="a.py", line_range="1", description="issue A", is_resolved=True),
        InlineThread(file_path="b.py", line_range="2", description="issue B", is_resolved=False),
    ]
    prompt = build_review_prompt(_make_context_with_threads(threads), "https://example.com/skill")
    assert "*(resolved)*" in prompt
    assert "*(open)*" in prompt
    # Single unified section, not split into confirmed/open sub-headers
    assert "Confirmed resolved" not in prompt


def test_prompt_open_thread_without_replies_shows_no_replies_marker() -> None:
    from code_review_bot.skill.filesystem import build_review_prompt

    threads = [
        InlineThread(
            file_path="c.py", line_range="3", description="style issue", is_resolved=False
        ),
    ]
    prompt = build_review_prompt(_make_context_with_threads(threads), "https://example.com/skill")
    assert "*(no replies)*" in prompt
