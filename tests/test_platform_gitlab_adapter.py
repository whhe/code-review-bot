import httpx
import pytest
import respx

from code_review_bot.platforms.gitlab.adapter import GitLabAdapter, _parse_change_request
from code_review_bot.platforms.gitlab.client import GitLabClient
from code_review_bot.platforms.models import InlinePosition


def _make_client() -> GitLabClient:
    return GitLabClient("https://gitlab.test", "test-token")


@pytest.mark.asyncio
@respx.mock
async def test_fetch_change_request_maps_gitlab_payload_to_change_request() -> None:
    respx.get("https://gitlab.test/api/v4/projects/1/merge_requests/5").mock(
        return_value=httpx.Response(
            200,
            json={
                "iid": 5,
                "project_id": 1,
                "title": "Fix bug",
                "description": "Details",
                "author": {"username": "alice"},
                "source_branch": "feature",
                "target_branch": "main",
                "state": "opened",
                "draft": False,
                "web_url": "https://gitlab.test/group/project/-/merge_requests/5",
                "sha": "head",
                "diff_refs": {"base_sha": "base", "head_sha": "head", "start_sha": "start"},
            },
        )
    )

    adapter = GitLabAdapter(_make_client())
    cr = await adapter.fetch_change_request("1", "5")

    assert cr.project_ref == "1"
    assert cr.cr_id == "5"
    assert cr.title == "Fix bug"
    assert cr.author == "alice"
    assert cr.head_sha == "head"
    assert cr.diff_refs["base_sha"] == "base"
    assert cr.source_branch == "feature"
    assert cr.target_branch == "main"

    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_resolve_project_ref_calls_get_project_by_path() -> None:
    respx.get("https://gitlab.test/api/v4/projects/group%2Frepo").mock(
        return_value=httpx.Response(200, json={"id": 42})
    )

    adapter = GitLabAdapter(_make_client())
    ref = await adapter.resolve_project_ref("group/repo")

    assert ref == "42"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_publish_inline_comment_flattens_inline_position() -> None:
    route = respx.post("https://gitlab.test/api/v4/projects/1/merge_requests/5/discussions").mock(
        return_value=httpx.Response(201, json={"id": "d1"})
    )

    adapter = GitLabAdapter(_make_client())
    position = InlinePosition(
        file_path="src/app.py",
        new_line=42,
        base_sha="base",
        start_sha="start",
        head_sha="head",
    )
    result = await adapter.publish_inline_comment("1", "5", "Found an issue", position)

    assert result["id"] == "d1"
    request = route.calls.last.request
    body = request.content.decode()
    assert "position%5Bnew_line%5D=42" in body or "position[new_line]=42" in body
    assert "position%5Bposition_type%5D=text" in body or "position[position_type]=text" in body

    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_publish_summary_calls_create_note() -> None:
    route = respx.post("https://gitlab.test/api/v4/projects/1/merge_requests/5/notes").mock(
        return_value=httpx.Response(201, json={"id": 99})
    )

    adapter = GitLabAdapter(_make_client())
    result = await adapter.publish_summary("1", "5", "Review summary")

    assert result["id"] == 99
    assert route.called

    await adapter.aclose()


def test_parse_change_request_handles_missing_optional_fields() -> None:
    data: dict[str, object] = {
        "iid": 3,
        "project_id": 10,
        "title": "Minimal MR",
        "source_branch": "fix",
        "target_branch": "main",
        "state": "opened",
    }
    cr = _parse_change_request(data, "10", "3")

    assert cr.title == "Minimal MR"
    assert cr.description == ""
    assert cr.author == ""
    assert cr.draft is False
    assert cr.head_sha == ""
    assert cr.diff_refs == {}


@pytest.mark.asyncio
@respx.mock
async def test_gitlab_client_reads_notes() -> None:
    respx.get("https://gitlab.test/api/v4/projects/1/merge_requests/5/notes").mock(
        return_value=httpx.Response(200, json=[{"id": 9, "body": "note"}])
    )

    adapter = GitLabAdapter(_make_client())
    notes = await adapter.list_notes("1", "5")

    assert notes[0]["id"] == 9
    await adapter.aclose()


_DISCUSSIONS_PAGE_1 = [
    {
        "notes": [
            {
                "body": "risky null deref",
                "position": {"new_path": "src/app.py", "new_line": 10},
                "resolved": True,
                "system": False,
            },
            {"body": "fixed it"},
        ]
    },
    {
        "notes": [
            {
                "body": "possible SQL injection",
                "position": {"new_path": "src/db.py", "new_line": 5},
                "resolved": False,
                "system": False,
            }
        ]
    },
    # system note — should be skipped
    {
        "notes": [
            {
                "body": "changed this line",
                "position": {"new_path": "src/app.py", "new_line": 3},
                "resolved": False,
                "system": True,
            }
        ]
    },
    # non-diff general comment — no position, should be skipped
    {"notes": [{"body": "looks good overall", "resolved": False, "system": False}]},
]


def _mock_discussions(page1_json: list) -> None:
    respx.get(
        "https://gitlab.test/api/v4/projects/1/merge_requests/5/discussions",
        params={"per_page": "100", "page": "1"},
    ).mock(return_value=httpx.Response(200, json=page1_json))
    respx.get(
        "https://gitlab.test/api/v4/projects/1/merge_requests/5/discussions",
        params={"per_page": "100", "page": "2"},
    ).mock(return_value=httpx.Response(200, json=[]))


@pytest.mark.asyncio
@respx.mock
async def test_list_inline_threads_returns_all_diff_threads() -> None:
    from code_review_bot.platforms.models import InlineThread

    _mock_discussions(_DISCUSSIONS_PAGE_1)

    adapter = GitLabAdapter(_make_client())
    result = await adapter.list_inline_threads("1", "5")

    assert len(result) == 2
    assert all(isinstance(t, InlineThread) for t in result)
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_list_inline_threads_resolved_flag_and_replies() -> None:
    _mock_discussions(_DISCUSSIONS_PAGE_1)

    adapter = GitLabAdapter(_make_client())
    result = await adapter.list_inline_threads("1", "5")

    resolved = next(t for t in result if t.is_resolved)
    assert resolved.file_path == "src/app.py"
    assert resolved.line_range == "10"
    assert resolved.description == "risky null deref"
    assert resolved.replies == ["fixed it"]

    open_thread = next(t for t in result if not t.is_resolved)
    assert open_thread.file_path == "src/db.py"
    assert open_thread.replies == []
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_list_inline_threads_skips_system_notes() -> None:
    _mock_discussions(_DISCUSSIONS_PAGE_1)

    adapter = GitLabAdapter(_make_client())
    result = await adapter.list_inline_threads("1", "5")

    assert all(t.file_path != "src/app.py" or t.line_range != "3" for t in result)
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_list_inline_threads_skips_non_diff_comments() -> None:
    _mock_discussions(_DISCUSSIONS_PAGE_1)

    adapter = GitLabAdapter(_make_client())
    result = await adapter.list_inline_threads("1", "5")

    assert all(t.description != "looks good overall" for t in result)
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_approve_change_request_sends_sha() -> None:
    route = respx.post("https://gitlab.test/api/v4/projects/1/merge_requests/5/approve").mock(
        return_value=httpx.Response(200, json={"approved": True})
    )

    adapter = GitLabAdapter(_make_client())
    result = await adapter.approve_change_request("1", "5", "abc123")

    assert result["approved"] is True
    body = route.calls.last.request.content.decode()
    assert "sha=abc123" in body
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_revoke_change_request_approval_calls_unapprove() -> None:
    # GitLab unapprove returns 204 No Content (empty body)
    route = respx.post("https://gitlab.test/api/v4/projects/1/merge_requests/5/unapprove").mock(
        return_value=httpx.Response(204)
    )

    adapter = GitLabAdapter(_make_client())
    result = await adapter.revoke_change_request_approval("1", "5")

    assert result == {}
    assert route.called
    await adapter.aclose()
