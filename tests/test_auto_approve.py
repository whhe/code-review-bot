import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from code_review_bot.config import Settings
from code_review_bot.platforms.models import ChangeRequest, InlinePosition, InlineThread
from code_review_bot.review.context import BotMetadata
from code_review_bot.review.models import ReviewOutcome
from code_review_bot.review.orchestrator import ReviewOrchestrator
from code_review_bot.review.publish.debug import DebugMarkdownPublisher
from code_review_bot.review.publish.platform import PlatformPublisher
from code_review_bot.skill.protocol import Finding, RuntimeMetadata, SkillResult


def _make_change_request(**overrides: object) -> ChangeRequest:
    data: dict[str, object] = {
        "project_ref": "1",
        "cr_id": "5",
        "title": "Fix bug",
        "description": "",
        "author": "alice",
        "source_branch": "feature",
        "target_branch": "main",
        "state": "opened",
        "draft": False,
        "web_url": "https://gitlab.test/mr/5",
        "head_sha": "headsha",
        "diff_refs": {"base_sha": "base", "start_sha": "start", "head_sha": "headsha"},
    }
    data.update(overrides)
    return ChangeRequest(**data)


def test_change_request_is_open_accepts_gitlab_and_github_states() -> None:
    base = _make_change_request()
    assert base.is_open is True

    assert _make_change_request(state="open").is_open is True
    assert _make_change_request(state="merged").is_open is False
    assert _make_change_request(state="closed").is_open is False


class ApprovalTrackingAdapter:
    platform_name = "gitlab"

    def __init__(self) -> None:
        self.approve_calls: list[tuple[str, str, str]] = []
        self.revoke_calls: list[tuple[str, str, str]] = []
        self.approve_bodies: list[str] = []
        self.revoke_bodies: list[str] = []
        self.summary_bodies: list[str] = []

    async def resolve_project_ref(self, project_path: str) -> str:
        return "1"

    async def fetch_change_request(self, project_ref: str, cr_id: str) -> ChangeRequest:
        return _make_change_request()

    async def list_notes(self, project_ref: str, cr_id: str) -> list[dict[str, object]]:
        return []

    async def list_inline_threads(self, project_ref: str, cr_id: str) -> list:
        return []

    async def publish_summary(self, project_ref: str, cr_id: str, body: str) -> dict[str, object]:
        self.summary_bodies.append(body)
        return {"id": 1}

    async def publish_inline_comment(
        self,
        project_ref: str,
        cr_id: str,
        body: str,
        position: InlinePosition,
    ) -> dict[str, object]:
        return {"id": 1}

    async def approve_change_request(
        self, project_ref: str, cr_id: str, head_sha: str, body: str = ""
    ) -> dict[str, object]:
        self.approve_calls.append((project_ref, cr_id, head_sha))
        self.approve_bodies.append(body)
        return {"approved": True}

    async def revoke_change_request_approval(
        self, project_ref: str, cr_id: str, head_sha: str = "", body: str = ""
    ) -> dict[str, object]:
        self.revoke_calls.append((project_ref, cr_id, head_sha))
        self.revoke_bodies.append(body)
        return {"approved": False}

    async def aclose(self) -> None:
        pass


class BasicApprovalAdapter(ApprovalTrackingAdapter):
    async def approve_change_request(
        self, project_ref: str, cr_id: str, head_sha: str
    ) -> dict[str, object]:
        self.approve_calls.append((project_ref, cr_id, head_sha))
        return {"approved": True}

    async def revoke_change_request_approval(
        self, project_ref: str, cr_id: str, head_sha: str = ""
    ) -> dict[str, object]:
        self.revoke_calls.append((project_ref, cr_id, head_sha))
        return {"approved": False}


class ReviewBodyApprovalTrackingAdapter(ApprovalTrackingAdapter):
    async def approve_change_request_with_body(
        self, project_ref: str, cr_id: str, head_sha: str, body: str
    ) -> dict[str, object]:
        return await self.approve_change_request(project_ref, cr_id, head_sha, body=body)

    async def revoke_change_request_approval_with_body(
        self, project_ref: str, cr_id: str, head_sha: str, body: str
    ) -> dict[str, object]:
        return await self.revoke_change_request_approval(project_ref, cr_id, head_sha, body=body)


class FailingSummaryApprovalAdapter(ApprovalTrackingAdapter):
    def __init__(self, fail_on_call: int) -> None:
        super().__init__()
        self.fail_on_call = fail_on_call
        self.summary_attempts = 0

    async def publish_summary(self, project_ref: str, cr_id: str, body: str) -> dict[str, object]:
        self.summary_attempts += 1
        if self.summary_attempts == self.fail_on_call:
            raise RuntimeError("summary comment rejected")
        return await super().publish_summary(project_ref, cr_id, body)


def _make_orchestrator(
    adapter: ApprovalTrackingAdapter,
    *,
    auto_approve: bool = True,
    platform_publish: bool = True,
) -> ReviewOrchestrator:
    settings = Settings(
        git_repo_url="https://gitlab.test/group/project.git",
        git_repo_token="tok",
        auto_approve_on_clean_review=auto_approve,
        _env_file=None,
    )
    publisher = (
        PlatformPublisher(adapter)
        if platform_publish
        else DebugMarkdownPublisher(output_dir="/tmp")
    )
    return ReviewOrchestrator(
        adapter=adapter,
        publisher=publisher,
        skill_path="skills/code-review",
        repo_manager=MagicMock(),
        settings=settings,
        bound_project_path="group/project",
    )


@pytest.mark.asyncio
async def test_maybe_update_approval_approves_when_no_new_findings() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(), "1", new_findings_count=0
    )

    assert approved is True
    assert adapter.approve_calls == [("1", "5", "headsha")]
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_maybe_update_approval_supports_basic_platform_adapter() -> None:
    adapter = BasicApprovalAdapter()
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(), "1", new_findings_count=0
    )

    assert approved is True
    assert adapter.approve_calls == [("1", "5", "headsha")]


@pytest.mark.asyncio
async def test_maybe_update_approval_revokes_when_findings_exist() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(), "1", new_findings_count=2
    )

    assert approved is False
    assert adapter.approve_calls == []
    assert adapter.revoke_calls == [("1", "5", "headsha")]


@pytest.mark.asyncio
async def test_maybe_update_approval_skips_when_disabled() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter, auto_approve=False)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(), "1", new_findings_count=0
    )

    assert approved is None
    assert adapter.approve_calls == []
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_maybe_update_approval_skips_in_debug_mode() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter, platform_publish=False)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(), "1", new_findings_count=0
    )

    assert approved is None
    assert adapter.approve_calls == []
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_maybe_update_approval_skips_draft_change_request() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(draft=True), "1", new_findings_count=0
    )

    assert approved is None
    assert adapter.approve_calls == []
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_maybe_update_approval_skips_closed_change_request() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(state="merged"), "1", new_findings_count=0
    )

    assert approved is None
    assert adapter.approve_calls == []
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_maybe_update_approval_accepts_github_open_state() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(state="open"), "1", new_findings_count=0
    )

    assert approved is True
    assert adapter.approve_calls == [("1", "5", "headsha")]


@pytest.mark.asyncio
async def test_maybe_update_approval_skips_approve_when_head_sha_missing() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(head_sha="", diff_refs={}),
        "1",
        new_findings_count=0,
    )

    assert approved is None
    assert adapter.approve_calls == []
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_maybe_update_approval_revokes_even_when_head_sha_missing() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(head_sha="", diff_refs={}),
        "1",
        new_findings_count=2,
    )

    assert approved is False
    assert adapter.approve_calls == []
    assert adapter.revoke_calls == [("1", "5", "")]


@pytest.mark.asyncio
async def test_maybe_update_approval_returns_none_on_api_failure() -> None:
    adapter = ApprovalTrackingAdapter()

    async def failing_approve(project_ref: str, cr_id: str, head_sha: str) -> dict[str, object]:
        raise RuntimeError("forbidden")

    adapter.approve_change_request = failing_approve  # type: ignore[method-assign]
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(), "1", new_findings_count=0
    )

    assert approved is None


@pytest.mark.asyncio
async def test_maybe_update_approval_returns_none_on_revoke_failure() -> None:
    adapter = ApprovalTrackingAdapter()

    async def failing_revoke(project_ref: str, cr_id: str, head_sha: str = "") -> dict[str, object]:
        raise RuntimeError("forbidden")

    adapter.revoke_change_request_approval = failing_revoke  # type: ignore[method-assign]
    orchestrator = _make_orchestrator(adapter)

    approved = await orchestrator._maybe_update_approval(
        _make_change_request(), "1", new_findings_count=3
    )

    assert approved is None
    assert adapter.approve_calls == []


# ---------------------------------------------------------------------------
# AUTO_APPROVE_IGNORE_LOW_SEVERITY
# ---------------------------------------------------------------------------


def _make_finding(severity: str) -> Finding:
    return Finding(
        severity=severity,
        description="test issue",
        file_path="foo.py",
        line_range="1",
        reason="test",
        confidence=50,
    )


def _make_orchestrator_ignore_low(adapter: ApprovalTrackingAdapter) -> ReviewOrchestrator:
    settings = Settings(
        git_repo_url="https://gitlab.test/group/project.git",
        git_repo_token="tok",
        auto_approve_on_clean_review=True,
        auto_approve_ignore_low_severity=True,
        _env_file=None,
    )
    return ReviewOrchestrator(
        adapter=adapter,
        publisher=PlatformPublisher(adapter),
        skill_path="skills/code-review",
        repo_manager=MagicMock(),
        settings=settings,
        bound_project_path="group/project",
    )


@contextlib.asynccontextmanager
async def _stub_review_internals(
    orchestrator: ReviewOrchestrator,
    skill_result: SkillResult,
    *subsequent_results: SkillResult,
    previous_metadata: BotMetadata | None = None,
    metadata_side_effect: list[BotMetadata | None] | None = None,
    stub_publisher: bool = True,
):
    """Stub all review_change_request dependencies except the approval logic."""
    mock_skill = MagicMock()
    mock_skill.name = "code-review"
    mock_skill.version = "1.0"
    mock_skill.build_prompt.return_value = "prompt"
    mock_skill.additional_directories = []

    with contextlib.ExitStack() as stack:
        # attach returns None (ReviewLogSession | None); detach(None) is a no-op per implementation.
        stack.enter_context(
            patch(
                "code_review_bot.review.orchestrator.attach_review_session_logging",
                return_value=None,
            )
        )
        stack.enter_context(
            patch("code_review_bot.review.orchestrator.detach_review_session_logging")
        )
        stack.enter_context(
            patch("code_review_bot.review.orchestrator.load_skill", return_value=mock_skill)
        )
        stack.enter_context(patch("code_review_bot.review.orchestrator.build_coding_agent"))
        mock_runner_cls = stack.enter_context(
            patch("code_review_bot.review.orchestrator.CodingAgentReviewRunner")
        )
        metadata_patch = patch(
            "code_review_bot.review.orchestrator.extract_metadata",
            side_effect=metadata_side_effect,
        )
        if metadata_side_effect is None:
            metadata_patch = patch(
                "code_review_bot.review.orchestrator.extract_metadata",
                return_value=previous_metadata,
            )
        stack.enter_context(metadata_patch)
        mock_filter_cls = stack.enter_context(
            patch("code_review_bot.review.orchestrator.FileFilter")
        )

        mock_runner_cls.return_value.review = AsyncMock(
            side_effect=[skill_result, *subsequent_results]
        )
        mock_filter_cls.return_value.filter_findings.side_effect = lambda findings: (findings, 0)
        orchestrator.repo_manager.make_review_workspace = AsyncMock(return_value=Path("/tmp/ws"))
        orchestrator.repo_manager.cleanup_review_workspace = MagicMock()
        if stub_publisher:
            orchestrator.publisher.publish = AsyncMock(  # type: ignore[method-assign]
                return_value=ReviewOutcome(summary="ok", review_body="Full review summary")
            )
        yield mock_runner_cls.return_value.review


def _make_thread(description: str) -> InlineThread:
    return InlineThread(
        file_path="foo.py",
        line_range="1",
        description=description,
    )


@pytest.mark.asyncio
async def test_review_restarts_once_when_inline_threads_change() -> None:
    adapter = ApprovalTrackingAdapter()
    initial_cr = _make_change_request(description="Initial requirements")
    latest_cr = _make_change_request(description="Updated requirements")
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[initial_cr, latest_cr, latest_cr]
    )
    adapter.list_inline_threads = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [_make_thread("old comment")],
            [_make_thread("new reply")],
            [_make_thread("new reply")],
        ]
    )
    orchestrator = _make_orchestrator(adapter)
    first = SkillResult(summary="stale", findings=[_make_finding("low")]).with_runtime(
        RuntimeMetadata(
            agent_type="opencode",
            model="model",
            input_tokens=10,
            output_tokens=2,
            total_tokens=12,
        )
    )
    refreshed = SkillResult(summary="fresh", findings=[]).with_runtime(
        RuntimeMetadata(
            agent_type="opencode",
            model="model",
            input_tokens=20,
            output_tokens=3,
            total_tokens=23,
        )
    )

    async with _stub_review_internals(orchestrator, first, refreshed) as review:
        await orchestrator.review_change_request("5")

    assert review.await_count == 2
    refreshed_context = review.await_args_list[1].args[1]
    assert refreshed_context.inline_threads == [_make_thread("new reply")]
    assert refreshed_context.change_request == latest_cr
    published_result = orchestrator.publisher.publish.await_args.args[1]  # type: ignore[union-attr]
    assert published_result.summary == "fresh"
    assert published_result.runtime == RuntimeMetadata(
        agent_type="opencode",
        model="model",
        input_tokens=30,
        output_tokens=5,
        total_tokens=35,
    )


@pytest.mark.asyncio
async def test_review_restarts_when_summary_finding_history_changes() -> None:
    adapter = ApprovalTrackingAdapter()
    adapter.list_notes = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [],
            [{"id": 1, "body": "concurrent summary"}],
            [{"id": 1, "body": "concurrent summary"}],
        ]
    )
    adapter.list_inline_threads = AsyncMock(  # type: ignore[method-assign]
        side_effect=[[], [], []]
    )
    orchestrator = _make_orchestrator(adapter)
    concurrent_finding = _make_finding("high")
    concurrent_metadata = BotMetadata(
        schema_version=2,
        head_sha="headsha",
        skill="code-review",
        version="1.0",
        unlocated_findings=[concurrent_finding],
    )
    first = SkillResult(summary="stale", findings=[concurrent_finding])
    refreshed = SkillResult(summary="fresh", findings=[])

    async with _stub_review_internals(
        orchestrator,
        first,
        refreshed,
        metadata_side_effect=[None, concurrent_metadata, concurrent_metadata],
    ) as review:
        await orchestrator.review_change_request("5")

    assert review.await_count == 2
    refreshed_context = review.await_args_list[1].args[1]
    assert refreshed_context.previous_head_sha == "headsha"
    assert refreshed_context.previous_unlocated_findings == [concurrent_finding]
    publish_call = orchestrator.publisher.publish.await_args  # type: ignore[union-attr]
    assert publish_call.args[1].summary == "fresh"
    assert publish_call.kwargs["existing_notes"] == [{"id": 1, "body": "concurrent summary"}]


@pytest.mark.asyncio
async def test_review_runtime_aggregation_keeps_incomplete_metrics_unavailable() -> None:
    adapter = ApprovalTrackingAdapter()
    adapter.list_inline_threads = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [_make_thread("initial")],
            [_make_thread("updated")],
            [_make_thread("updated")],
        ]
    )
    orchestrator = _make_orchestrator(adapter)
    first = SkillResult(summary="stale", findings=[]).with_runtime(
        RuntimeMetadata(input_tokens=10, output_tokens=2, total_tokens=None)
    )
    refreshed = SkillResult(summary="fresh", findings=[]).with_runtime(
        RuntimeMetadata(input_tokens=20, output_tokens=None, total_tokens=23)
    )

    async with _stub_review_internals(orchestrator, first, refreshed):
        await orchestrator.review_change_request("5")

    runtime = orchestrator.publisher.publish.await_args.args[1].runtime  # type: ignore[union-attr]
    assert runtime is not None
    assert runtime.input_tokens == 30
    assert runtime.output_tokens is None
    assert runtime.total_tokens is None


@pytest.mark.asyncio
async def test_custom_publisher_also_refreshes_inline_threads() -> None:
    adapter = ApprovalTrackingAdapter()
    adapter.list_inline_threads = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [_make_thread("initial")],
            [_make_thread("updated")],
            [_make_thread("updated")],
        ]
    )
    custom_publisher = MagicMock()
    custom_publisher.publish = AsyncMock(return_value=ReviewOutcome(summary="ok"))
    settings = Settings(
        git_repo_url="https://gitlab.test/group/project.git",
        git_repo_token="tok",
        _env_file=None,
    )
    orchestrator = ReviewOrchestrator(
        adapter=adapter,
        publisher=custom_publisher,
        skill_path="skills/code-review",
        repo_manager=MagicMock(),
        settings=settings,
        bound_project_path="group/project",
    )
    first = SkillResult(summary="stale", findings=[])
    refreshed = SkillResult(summary="fresh", findings=[])

    async with _stub_review_internals(orchestrator, first, refreshed) as review:
        await orchestrator.review_change_request("5")

    assert review.await_count == 2


@pytest.mark.asyncio
async def test_review_aborts_when_inline_threads_change_during_refreshed_review() -> None:
    adapter = ApprovalTrackingAdapter()
    adapter.list_inline_threads = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [_make_thread("initial")],
            [_make_thread("first update")],
            [_make_thread("second update")],
        ]
    )
    orchestrator = _make_orchestrator(adapter)
    first = SkillResult(summary="stale", findings=[])
    refreshed = SkillResult(summary="still stale", findings=[])

    with pytest.raises(RuntimeError, match="Prior review context changed during review"):
        async with _stub_review_internals(orchestrator, first, refreshed):
            await orchestrator.review_change_request("5")

    orchestrator.publisher.publish.assert_not_awaited()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_review_aborts_when_change_request_head_changes_during_review() -> None:
    adapter = ApprovalTrackingAdapter()
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _make_change_request(),
            _make_change_request(
                head_sha="new-head",
                diff_refs={
                    "base_sha": "new-base",
                    "start_sha": "new-start",
                    "head_sha": "new-head",
                },
            ),
        ]
    )
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="stale", findings=[])

    with pytest.raises(RuntimeError, match="Change request revision changed during review"):
        async with _stub_review_internals(orchestrator, result):
            await orchestrator.review_change_request("5")

    orchestrator.publisher.publish.assert_not_awaited()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_review_restarts_when_prompt_change_request_metadata_changes() -> None:
    adapter = ApprovalTrackingAdapter()
    initial_cr = _make_change_request(description="Initial requirements")
    latest_cr = _make_change_request(description="Updated requirements")
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[initial_cr, latest_cr, latest_cr]
    )
    orchestrator = _make_orchestrator(adapter)
    stale = SkillResult(summary="stale", findings=[])
    refreshed = SkillResult(summary="fresh", findings=[])

    async with _stub_review_internals(orchestrator, stale, refreshed) as review:
        await orchestrator.review_change_request("5")

    assert review.await_count == 2
    assert review.await_args_list[1].args[1].change_request == latest_cr
    orchestrator.publisher.publish.assert_awaited_once()  # type: ignore[union-attr]
    assert orchestrator.publisher.publish.await_args.args[1].summary == "fresh"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_review_restarts_when_head_repository_changes() -> None:
    adapter = ApprovalTrackingAdapter()
    initial_cr = _make_change_request(head_repo_url="https://github.test/owner-a/repo.git")
    latest_cr = _make_change_request(head_repo_url="https://github.test/owner-b/repo.git")
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[initial_cr, latest_cr, latest_cr]
    )
    orchestrator = _make_orchestrator(adapter)

    async with _stub_review_internals(
        orchestrator,
        SkillResult(summary="stale", findings=[]),
        SkillResult(summary="fresh", findings=[]),
    ) as review:
        await orchestrator.review_change_request("5")

    assert review.await_count == 2
    assert review.await_args_list[1].args[1].change_request == latest_cr
    assert orchestrator.publisher.publish.await_args.args[1].summary == "fresh"  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_review_updates_derived_branch_context_when_source_branch_changes() -> None:
    adapter = ApprovalTrackingAdapter()
    initial_cr = _make_change_request(source_branch="feature-old")
    latest_cr = _make_change_request(source_branch="feature-renamed")
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[initial_cr, latest_cr, latest_cr]
    )
    orchestrator = _make_orchestrator(adapter)

    async with _stub_review_internals(
        orchestrator,
        SkillResult(summary="stale", findings=[]),
        SkillResult(summary="fresh", findings=[]),
    ) as review:
        await orchestrator.review_change_request("5")

    refreshed_context = review.await_args_list[1].args[1]
    assert refreshed_context.change_request == latest_cr
    assert refreshed_context.source_branch == "feature-renamed"
    assert refreshed_context.target_branch == "main"


@pytest.mark.asyncio
async def test_review_aborts_when_prompt_metadata_changes_again_after_rerun() -> None:
    adapter = ApprovalTrackingAdapter()
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _make_change_request(description="Initial requirements"),
            _make_change_request(description="Updated requirements"),
            _make_change_request(description="Final requirements"),
        ]
    )
    orchestrator = _make_orchestrator(adapter)

    with pytest.raises(RuntimeError, match="review context changed during review"):
        async with _stub_review_internals(
            orchestrator,
            SkillResult(summary="stale", findings=[]),
            SkillResult(summary="still stale", findings=[]),
        ):
            await orchestrator.review_change_request("5")

    orchestrator.publisher.publish.assert_not_awaited()  # type: ignore[union-attr]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "latest_change_request",
    [
        _make_change_request(draft=True),
    ],
)
async def test_review_uses_latest_change_request_state_for_approval(
    latest_change_request: ChangeRequest,
) -> None:
    adapter = ApprovalTrackingAdapter()
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[_make_change_request(), latest_change_request]
    )
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="clean", findings=[])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    assert adapter.approve_calls == []
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_review_restarts_when_change_request_state_changes() -> None:
    adapter = ApprovalTrackingAdapter()
    initial_cr = _make_change_request(state="opened")
    closed_cr = _make_change_request(state="closed")
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[initial_cr, closed_cr, closed_cr]
    )
    orchestrator = _make_orchestrator(adapter)

    async with _stub_review_internals(
        orchestrator,
        SkillResult(summary="stale", findings=[]),
        SkillResult(summary="fresh", findings=[]),
    ) as review:
        await orchestrator.review_change_request("5")

    assert review.await_count == 2
    assert review.await_args_list[1].args[1].change_request == closed_cr
    assert orchestrator.publisher.publish.await_args.args[1].summary == "fresh"  # type: ignore[union-attr]
    assert adapter.approve_calls == []
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_review_aborts_when_diff_refs_change_with_same_head() -> None:
    adapter = ApprovalTrackingAdapter()
    adapter.fetch_change_request = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _make_change_request(),
            _make_change_request(
                diff_refs={
                    "base_sha": "new-base",
                    "start_sha": "new-start",
                    "head_sha": "headsha",
                }
            ),
        ]
    )
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="stale", findings=[])

    with pytest.raises(RuntimeError, match="Change request revision changed during review"):
        async with _stub_review_internals(orchestrator, result):
            await orchestrator.review_change_request("5")

    orchestrator.publisher.publish.assert_not_awaited()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_review_aborts_when_head_changes_while_refreshing_review_context() -> None:
    adapter = ApprovalTrackingAdapter()
    current_cr = _make_change_request()
    notes_call_count = 0

    async def fetch_change_request(project_ref: str, cr_id: str) -> ChangeRequest:
        return current_cr

    async def list_notes(project_ref: str, cr_id: str) -> list[dict[str, object]]:
        nonlocal current_cr, notes_call_count
        notes_call_count += 1
        if notes_call_count == 2:
            current_cr = _make_change_request(
                head_sha="new-head",
                diff_refs={
                    "base_sha": "new-base",
                    "start_sha": "new-start",
                    "head_sha": "new-head",
                },
            )
        return []

    adapter.fetch_change_request = AsyncMock(side_effect=fetch_change_request)  # type: ignore[method-assign]
    adapter.list_notes = AsyncMock(side_effect=list_notes)  # type: ignore[method-assign]
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="stale", findings=[])

    with pytest.raises(RuntimeError, match="Change request revision changed during review"):
        async with _stub_review_internals(orchestrator, result):
            await orchestrator.review_change_request("5")

    orchestrator.publisher.publish.assert_not_awaited()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_review_aborts_when_head_changes_while_refreshing_context_after_rerun() -> None:
    adapter = ApprovalTrackingAdapter()
    current_cr = _make_change_request()
    notes_call_count = 0

    async def fetch_change_request(project_ref: str, cr_id: str) -> ChangeRequest:
        return current_cr

    async def list_notes(project_ref: str, cr_id: str) -> list[dict[str, object]]:
        nonlocal current_cr, notes_call_count
        notes_call_count += 1
        if notes_call_count == 3:
            current_cr = _make_change_request(
                head_sha="new-head",
                diff_refs={
                    "base_sha": "new-base",
                    "start_sha": "new-start",
                    "head_sha": "new-head",
                },
            )
        return []

    adapter.fetch_change_request = AsyncMock(side_effect=fetch_change_request)  # type: ignore[method-assign]
    adapter.list_notes = AsyncMock(side_effect=list_notes)  # type: ignore[method-assign]
    adapter.list_inline_threads = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [_make_thread("initial")],
            [_make_thread("updated")],
            [_make_thread("updated")],
        ]
    )
    orchestrator = _make_orchestrator(adapter)
    first = SkillResult(summary="stale", findings=[])
    refreshed = SkillResult(summary="still stale", findings=[])

    with pytest.raises(RuntimeError, match="Change request revision changed during review"):
        async with _stub_review_internals(orchestrator, first, refreshed):
            await orchestrator.review_change_request("5")

    orchestrator.publisher.publish.assert_not_awaited()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_review_revokes_approval_when_supplemental_summary_publication_fails() -> None:
    adapter = FailingSummaryApprovalAdapter(fail_on_call=2)
    orchestrator = _make_orchestrator(adapter)
    findings = [
        _make_finding("critical").model_copy(
            update={"description": f"issue-{index}", "line_range": "outside diff"}
        )
        for index in range(41)
    ]

    with pytest.raises(RuntimeError, match="summary comment rejected"):
        async with _stub_review_internals(
            orchestrator,
            SkillResult(summary="issues", findings=findings),
            stub_publisher=False,
        ):
            await orchestrator.review_change_request("5")

    assert len(adapter.summary_bodies) == 1
    assert adapter.revoke_calls == [("1", "5", "headsha")]
    assert adapter.approve_calls == []


@pytest.mark.asyncio
async def test_github_auto_approval_publishes_summary_as_single_review() -> None:
    adapter = ReviewBodyApprovalTrackingAdapter()
    adapter.platform_name = "github"
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="ok", findings=[_make_finding("high")])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    assert orchestrator.publisher.publish.await_args.kwargs["publish_summary"] is False
    assert adapter.revoke_bodies == ["Full review summary"]
    assert adapter.summary_bodies == []


@pytest.mark.asyncio
async def test_github_basic_adapter_keeps_separate_summary_and_approval() -> None:
    adapter = BasicApprovalAdapter()
    adapter.platform_name = "github"
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="ok", findings=[])

    async with _stub_review_internals(orchestrator, result):
        outcome = await orchestrator.review_change_request("5")

    assert orchestrator.publisher.publish.await_args.kwargs["publish_summary"] is True
    assert adapter.approve_calls == [("1", "5", "headsha")]
    assert outcome.approved is True


@pytest.mark.asyncio
async def test_github_auto_approval_falls_back_to_summary_when_review_fails() -> None:
    adapter = ReviewBodyApprovalTrackingAdapter()
    adapter.platform_name = "github"

    async def failing_approve(
        project_ref: str, cr_id: str, head_sha: str, body: str = ""
    ) -> dict[str, object]:
        raise RuntimeError("forbidden")

    adapter.approve_change_request_with_body = failing_approve  # type: ignore[method-assign]
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="ok", findings=[])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    assert adapter.summary_bodies == ["Full review summary"]


@pytest.mark.asyncio
async def test_ignore_low_approves_when_only_low_findings() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator_ignore_low(adapter)
    result = SkillResult(summary="ok", findings=[_make_finding("low"), _make_finding("low")])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    assert adapter.approve_calls == [("1", "5", "headsha")]
    assert adapter.revoke_calls == []


@pytest.mark.asyncio
async def test_review_pins_workspace_to_change_request_diff_refs() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="ok", findings=[])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    orchestrator.repo_manager.make_review_workspace.assert_awaited_once_with(
        "feature",
        "main",
        head_repo_url="",
        source_sha="headsha",
        target_sha="start",
    )
    publish_call = orchestrator.publisher.publish.await_args  # type: ignore[union-attr]
    assert publish_call.kwargs["existing_notes"] == []


@pytest.mark.asyncio
async def test_review_passes_previous_unlocated_findings_to_agent() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)
    previous_finding = _make_finding("high")
    result = SkillResult(summary="ok", findings=[])

    async with _stub_review_internals(
        orchestrator,
        result,
        previous_metadata=BotMetadata(
            schema_version=2,
            skill="code-review",
            version="1.0",
            unlocated_findings=[previous_finding],
        ),
    ) as review:
        await orchestrator.review_change_request("5")

    task_context = review.await_args.args[1]
    assert task_context.previous_unlocated_findings == [previous_finding]


@pytest.mark.asyncio
async def test_review_drops_previous_unlocated_findings_for_different_skill_version() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator(adapter)
    previous_finding = _make_finding("high")
    result = SkillResult(summary="ok", findings=[])

    async with _stub_review_internals(
        orchestrator,
        result,
        previous_metadata=BotMetadata(
            schema_version=2,
            skill="other-skill",
            version="old",
            unlocated_findings=[previous_finding],
        ),
    ) as review:
        await orchestrator.review_change_request("5")

    task_context = review.await_args.args[1]
    assert task_context.previous_unlocated_findings == []


@pytest.mark.asyncio
async def test_ignore_low_revokes_when_non_low_findings_exist() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator_ignore_low(adapter)
    result = SkillResult(summary="ok", findings=[_make_finding("low"), _make_finding("high")])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    assert adapter.revoke_calls == [("1", "5", "headsha")]
    assert adapter.approve_calls == []


@pytest.mark.asyncio
async def test_ignore_low_disabled_revokes_on_low_only_findings() -> None:
    adapter = ApprovalTrackingAdapter()
    # default: auto_approve_ignore_low_severity=False
    orchestrator = _make_orchestrator(adapter)
    result = SkillResult(summary="ok", findings=[_make_finding("low")])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    assert adapter.revoke_calls == [("1", "5", "headsha")]
    assert adapter.approve_calls == []


@pytest.mark.asyncio
async def test_ignore_low_revokes_on_medium_only_findings() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator_ignore_low(adapter)
    result = SkillResult(summary="ok", findings=[_make_finding("medium")])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    assert adapter.revoke_calls == [("1", "5", "headsha")]
    assert adapter.approve_calls == []


@pytest.mark.asyncio
async def test_ignore_low_approves_when_no_findings() -> None:
    adapter = ApprovalTrackingAdapter()
    orchestrator = _make_orchestrator_ignore_low(adapter)
    result = SkillResult(summary="ok", findings=[])

    async with _stub_review_internals(orchestrator, result):
        await orchestrator.review_change_request("5")

    assert adapter.approve_calls == [("1", "5", "headsha")]
    assert adapter.revoke_calls == []
