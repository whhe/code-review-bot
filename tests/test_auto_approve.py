from unittest.mock import MagicMock

import pytest

from code_review_bot.config import Settings
from code_review_bot.platforms.models import ChangeRequest, InlinePosition
from code_review_bot.review.orchestrator import ReviewOrchestrator
from code_review_bot.review.publish.debug import DebugMarkdownPublisher
from code_review_bot.review.publish.platform import PlatformPublisher


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

    async def resolve_project_ref(self, project_path: str) -> str:
        return "1"

    async def fetch_change_request(self, project_ref: str, cr_id: str) -> ChangeRequest:
        return _make_change_request()

    async def list_notes(self, project_ref: str, cr_id: str) -> list[dict[str, object]]:
        return []

    async def list_inline_threads(self, project_ref: str, cr_id: str) -> list:
        return []

    async def publish_summary(self, project_ref: str, cr_id: str, body: str) -> dict[str, object]:
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
        self, project_ref: str, cr_id: str, head_sha: str
    ) -> dict[str, object]:
        self.approve_calls.append((project_ref, cr_id, head_sha))
        return {"approved": True}

    async def revoke_change_request_approval(
        self, project_ref: str, cr_id: str, head_sha: str = ""
    ) -> dict[str, object]:
        self.revoke_calls.append((project_ref, cr_id, head_sha))
        return {"approved": False}

    async def aclose(self) -> None:
        pass


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
