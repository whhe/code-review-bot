import json

import httpx
import pytest
import respx

from code_review_bot.platforms.github.adapter import (
    GitHubAdapter,
    _parse_change_request,
    _split_project_ref,
)
from code_review_bot.platforms.github.client import GitHubClient
from code_review_bot.platforms.models import InlinePosition, InlineThread

_BASE = "https://api.github.com"


def _make_client() -> GitHubClient:
    return GitHubClient("test-token")


def _make_adapter() -> GitHubAdapter:
    return GitHubAdapter(_make_client())


@pytest.mark.asyncio
@respx.mock
async def test_list_pull_reviews_fetches_all_pages() -> None:
    route = respx.get(f"{_BASE}/repos/alice/myrepo/pulls/7/reviews").mock(
        side_effect=[
            httpx.Response(200, json=[{"id": item} for item in range(100)]),
            httpx.Response(200, json=[{"id": 100}]),
        ]
    )

    client = _make_client()
    reviews = await client.list_pull_reviews("alice", "myrepo", 7)

    assert [review["id"] for review in reviews] == list(range(101))
    assert route.call_count == 2
    assert dict(route.calls[0].request.url.params) == {"per_page": "100", "page": "1"}
    assert dict(route.calls[1].request.url.params) == {"per_page": "100", "page": "2"}
    await client.aclose()


# --- _split_project_ref ---


def test_split_project_ref_returns_owner_and_repo() -> None:
    assert _split_project_ref("octocat/hello-world") == ("octocat", "hello-world")


def test_split_project_ref_rejects_missing_slash() -> None:
    with pytest.raises(ValueError, match="owner/repo"):
        _split_project_ref("no-slash")


def test_split_project_ref_rejects_empty_owner() -> None:
    with pytest.raises(ValueError):
        _split_project_ref("/repo")


# --- resolve_project_ref ---


@pytest.mark.asyncio
async def test_resolve_project_ref_returns_input_unchanged() -> None:
    adapter = _make_adapter()
    ref = await adapter.resolve_project_ref("octocat/hello-world")
    assert ref == "octocat/hello-world"
    await adapter.aclose()


# --- fetch_change_request ---


@pytest.mark.asyncio
@respx.mock
async def test_fetch_pull_request_maps_to_change_request() -> None:
    respx.get(f"{_BASE}/repos/alice/myrepo/pulls/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 7,
                "title": "Add feature",
                "body": "Description",
                "user": {"login": "alice"},
                "head": {"ref": "feature-branch", "sha": "headsha"},
                "base": {"ref": "main", "sha": "basesha"},
                "state": "open",
                "draft": False,
                "html_url": "https://github.com/alice/myrepo/pull/7",
            },
        )
    )

    adapter = _make_adapter()
    cr = await adapter.fetch_change_request("alice/myrepo", "7")

    assert cr.project_ref == "alice/myrepo"
    assert cr.cr_id == "7"
    assert cr.title == "Add feature"
    assert cr.author == "alice"
    assert cr.source_branch == "feature-branch"
    assert cr.target_branch == "main"
    assert cr.head_sha == "headsha"
    assert cr.diff_refs["base_sha"] == "basesha"
    assert cr.diff_refs["start_sha"] == "basesha"
    assert cr.diff_refs["head_sha"] == "headsha"

    await adapter.aclose()


# --- list_notes ---


@pytest.mark.asyncio
@respx.mock
async def test_list_notes_fetches_issue_comments_and_pull_reviews() -> None:
    respx.get(f"{_BASE}/repos/alice/myrepo/issues/7/comments").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 1, "body": "comment", "created_at": "2026-07-16T02:00:00Z"}],
        )
    )
    respx.get(f"{_BASE}/repos/alice/myrepo/pulls/7/reviews").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": 2, "body": "review", "submitted_at": "2026-07-16T01:00:00Z"}],
        )
    )

    adapter = _make_adapter()
    notes = await adapter.list_notes("alice/myrepo", "7")

    assert [note["id"] for note in notes] == [2, 1]

    await adapter.aclose()


# --- publish_summary ---


@pytest.mark.asyncio
@respx.mock
async def test_publish_summary_posts_issue_comment() -> None:
    route = respx.post(f"{_BASE}/repos/alice/myrepo/issues/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 99, "body": "Review summary"})
    )

    adapter = _make_adapter()
    result = await adapter.publish_summary("alice/myrepo", "7", "Review summary")

    assert result["id"] == 99
    assert route.called

    await adapter.aclose()


# --- publish_inline_comment ---


@pytest.mark.asyncio
@respx.mock
async def test_publish_inline_comment_uses_line_and_side() -> None:
    route = respx.post(f"{_BASE}/repos/alice/myrepo/pulls/7/comments").mock(
        return_value=httpx.Response(201, json={"id": 42})
    )

    adapter = _make_adapter()
    position = InlinePosition(
        file_path="src/app.py",
        new_line=15,
        base_sha="basesha",
        start_sha="startsha",
        head_sha="headsha",
    )
    result = await adapter.publish_inline_comment("alice/myrepo", "7", "Found an issue", position)

    assert result["id"] == 42
    body = json.loads(route.calls.last.request.content)
    assert body["commit_id"] == "headsha"
    assert body["path"] == "src/app.py"
    assert body["line"] == 15
    assert body["side"] == "RIGHT"

    await adapter.aclose()


# --- _parse_change_request ---


def test_parse_pull_request_handles_missing_optional_fields() -> None:
    data: dict[str, object] = {
        "number": 3,
        "title": "Minimal PR",
        "head": {"ref": "fix", "sha": ""},
        "base": {"ref": "main", "sha": ""},
        "state": "open",
    }
    cr = _parse_change_request(data, "owner/repo", "3")

    assert cr.title == "Minimal PR"
    assert cr.description == ""
    assert cr.author == ""
    assert cr.draft is False
    assert cr.head_sha == ""
    assert cr.diff_refs == {}


def test_parse_change_request_sets_head_repo_url_for_fork() -> None:
    data: dict[str, object] = {
        "number": 5,
        "title": "Fork PR",
        "body": "",
        "user": {"login": "forker"},
        "head": {
            "ref": "feature",
            "sha": "headsha",
            "repo": {
                "full_name": "forker/repo",
                "clone_url": "https://github.com/forker/repo.git",
            },
        },
        "base": {
            "ref": "main",
            "sha": "basesha",
            "repo": {
                "full_name": "owner/repo",
                "clone_url": "https://github.com/owner/repo.git",
            },
        },
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/owner/repo/pull/5",
    }
    cr = _parse_change_request(data, "owner/repo", "5")
    assert cr.head_repo_url == "https://github.com/forker/repo.git"


def test_parse_change_request_head_repo_url_empty_for_same_repo() -> None:
    data: dict[str, object] = {
        "number": 6,
        "title": "Same-repo PR",
        "body": "",
        "user": {"login": "alice"},
        "head": {
            "ref": "feature",
            "sha": "headsha",
            "repo": {
                "full_name": "owner/repo",
                "clone_url": "https://github.com/owner/repo.git",
            },
        },
        "base": {
            "ref": "main",
            "sha": "basesha",
            "repo": {
                "full_name": "owner/repo",
                "clone_url": "https://github.com/owner/repo.git",
            },
        },
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/owner/repo/pull/6",
    }
    cr = _parse_change_request(data, "owner/repo", "6")
    assert cr.head_repo_url == ""


# --- list_inline_threads ---

_THREADS_RESPONSE = {
    "data": {
        "repository": {
            "pullRequest": {
                "reviewThreads": {
                    "nodes": [
                        {
                            "isResolved": True,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "risky null deref",
                                        "path": "src/app.py",
                                        "line": 10,
                                        "originalLine": 10,
                                    },
                                    {
                                        "body": "fixed in latest commit",
                                        "path": "src/app.py",
                                        "line": 10,
                                        "originalLine": 10,
                                    },
                                ]
                            },
                        },
                        {
                            "isResolved": False,
                            "comments": {
                                "nodes": [
                                    {
                                        "body": "possible SQL injection",
                                        "path": "src/db.py",
                                        "line": 5,
                                        "originalLine": 5,
                                    },
                                ]
                            },
                        },
                    ]
                }
            }
        }
    }
}


@pytest.mark.asyncio
@respx.mock
async def test_list_inline_threads_returns_all_threads() -> None:
    respx.post(f"{_BASE}/graphql").mock(return_value=httpx.Response(200, json=_THREADS_RESPONSE))

    adapter = _make_adapter()
    result = await adapter.list_inline_threads("alice/myrepo", "7")

    assert len(result) == 2
    assert all(isinstance(t, InlineThread) for t in result)
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_list_inline_threads_resolved_flag_and_replies() -> None:
    respx.post(f"{_BASE}/graphql").mock(return_value=httpx.Response(200, json=_THREADS_RESPONSE))

    adapter = _make_adapter()
    result = await adapter.list_inline_threads("alice/myrepo", "7")

    resolved = next(t for t in result if t.is_resolved)
    assert resolved.file_path == "src/app.py"
    assert resolved.line_range == "10"
    assert resolved.description == "risky null deref"
    assert resolved.replies == ["fixed in latest commit"]

    open_thread = next(t for t in result if not t.is_resolved)
    assert open_thread.file_path == "src/db.py"
    assert open_thread.replies == []
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_list_inline_threads_skips_threads_without_path() -> None:
    respx.post(f"{_BASE}/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [
                                    {
                                        "isResolved": True,
                                        "comments": {
                                            "nodes": [
                                                {
                                                    "body": "general comment",
                                                    "path": "",
                                                    "line": None,
                                                    "originalLine": None,
                                                }
                                            ]
                                        },
                                    }
                                ]
                            }
                        }
                    }
                }
            },
        )
    )

    adapter = _make_adapter()
    result = await adapter.list_inline_threads("alice/myrepo", "7")

    assert result == []
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_list_inline_threads_empty_when_no_threads() -> None:
    respx.post(f"{_BASE}/graphql").mock(
        return_value=httpx.Response(
            200,
            json={"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": []}}}}},
        )
    )

    adapter = _make_adapter()
    result = await adapter.list_inline_threads("alice/myrepo", "7")

    assert result == []
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_approve_change_request_with_body_submits_approve_review() -> None:
    route = respx.post(f"{_BASE}/repos/alice/myrepo/pulls/7/reviews").mock(
        return_value=httpx.Response(200, json={"id": 1, "state": "APPROVED"})
    )

    adapter = _make_adapter()
    result = await adapter.approve_change_request_with_body(
        "alice/myrepo", "7", "deadbeef", body="Full review summary"
    )

    assert result["state"] == "APPROVED"
    payload = json.loads(route.calls.last.request.content)
    assert payload == {
        "event": "APPROVE",
        "commit_id": "deadbeef",
        "body": "Full review summary",
    }
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_revoke_change_request_approval_with_body_submits_request_changes() -> None:
    route = respx.post(f"{_BASE}/repos/alice/myrepo/pulls/7/reviews").mock(
        return_value=httpx.Response(200, json={"id": 2, "state": "CHANGES_REQUESTED"})
    )

    adapter = _make_adapter()
    result = await adapter.revoke_change_request_approval_with_body(
        "alice/myrepo", "7", head_sha="deadbeef", body="Full review summary"
    )

    assert result["state"] == "CHANGES_REQUESTED"
    payload = json.loads(route.calls.last.request.content)
    assert payload["event"] == "REQUEST_CHANGES"
    assert payload["commit_id"] == "deadbeef"
    assert payload["body"] == "Full review summary"
    await adapter.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_revoke_change_request_approval_falls_back_to_fetch_when_no_sha() -> None:
    """When head_sha is not supplied, the adapter re-fetches the PR to obtain it."""
    respx.get(f"{_BASE}/repos/alice/myrepo/pulls/7").mock(
        return_value=httpx.Response(
            200,
            json={
                "number": 7,
                "title": "Fix",
                "body": "",
                "state": "open",
                "draft": False,
                "user": {"login": "alice"},
                "head": {"ref": "feature", "sha": "deadbeef"},
                "base": {"ref": "main", "sha": "base"},
                "html_url": "https://github.com/alice/myrepo/pull/7",
            },
        )
    )
    route = respx.post(f"{_BASE}/repos/alice/myrepo/pulls/7/reviews").mock(
        return_value=httpx.Response(200, json={"id": 2, "state": "CHANGES_REQUESTED"})
    )

    adapter = _make_adapter()
    result = await adapter.revoke_change_request_approval("alice/myrepo", "7")

    assert result["state"] == "CHANGES_REQUESTED"
    payload = json.loads(route.calls.last.request.content)
    assert payload["event"] == "REQUEST_CHANGES"
    assert payload["commit_id"] == "deadbeef"
    assert payload["body"] == "Code review found new issues."
    await adapter.aclose()
