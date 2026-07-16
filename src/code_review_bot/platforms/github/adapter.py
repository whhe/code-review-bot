from code_review_bot.platforms.github.client import GitHubClient
from code_review_bot.platforms.models import ChangeRequest, InlinePosition, InlineThread


class GitHubAdapter:
    """PlatformAdapter implementation for GitHub.

    project_ref is always "owner/repo" (e.g. "octocat/hello-world").
    cr_id is always the pull request number as a string.
    """

    platform_name = "github"

    def __init__(self, client: GitHubClient) -> None:
        self._client = client

    async def aclose(self) -> None:
        await self._client.aclose()

    async def resolve_project_ref(self, project_path: str) -> str:
        return project_path

    async def fetch_change_request(self, project_ref: str, cr_id: str) -> ChangeRequest:
        owner, repo = _split_project_ref(project_ref)
        data = await self._client.get_pull_request(owner, repo, int(cr_id))
        return _parse_change_request(data, project_ref, cr_id)

    async def list_notes(self, project_ref: str, cr_id: str) -> list[dict[str, object]]:
        owner, repo = _split_project_ref(project_ref)
        issue_comments = await self._client.list_issue_comments(owner, repo, int(cr_id))
        reviews = await self._client.list_pull_reviews(owner, repo, int(cr_id))
        return sorted(
            [*issue_comments, *reviews],
            key=lambda note: str(note.get("submitted_at") or note.get("created_at") or ""),
        )

    async def list_inline_threads(self, project_ref: str, cr_id: str) -> list[InlineThread]:
        owner, repo = _split_project_ref(project_ref)
        raw_threads = await self._client.query_resolved_threads(owner, repo, int(cr_id))
        threads: list[InlineThread] = []
        for thread in raw_threads:
            comments = (thread.get("comments") or {}).get("nodes") or []
            if not comments:
                continue
            first = comments[0]
            file_path = str(first.get("path") or "")
            if not file_path:
                continue
            raw_line = first.get("line") or first.get("originalLine")
            line_range = str(raw_line) if raw_line is not None else ""
            threads.append(
                InlineThread(
                    file_path=file_path,
                    line_range=line_range,
                    description=str(first.get("body") or "").strip(),
                    replies=[str(c.get("body") or "").strip() for c in comments[1:]],
                    is_resolved=bool(thread.get("isResolved")),
                )
            )
        return threads

    async def publish_summary(self, project_ref: str, cr_id: str, body: str) -> dict[str, object]:
        owner, repo = _split_project_ref(project_ref)
        return await self._client.create_issue_comment(owner, repo, int(cr_id), body)

    async def publish_inline_comment(
        self,
        project_ref: str,
        cr_id: str,
        body: str,
        position: InlinePosition,
    ) -> dict[str, object]:
        owner, repo = _split_project_ref(project_ref)
        return await self._client.create_pull_comment(
            owner,
            repo,
            int(cr_id),
            body,
            commit_id=position.head_sha,
            path=position.file_path,
            line=position.new_line,
        )

    async def approve_change_request(
        self, project_ref: str, cr_id: str, head_sha: str
    ) -> dict[str, object]:
        owner, repo = _split_project_ref(project_ref)
        return await self._client.create_pull_review(owner, repo, int(cr_id), "APPROVE", head_sha)

    async def approve_change_request_with_body(
        self, project_ref: str, cr_id: str, head_sha: str, body: str
    ) -> dict[str, object]:
        owner, repo = _split_project_ref(project_ref)
        return await self._client.create_pull_review(
            owner, repo, int(cr_id), "APPROVE", head_sha, body=body
        )

    async def revoke_change_request_approval(
        self,
        project_ref: str,
        cr_id: str,
        head_sha: str = "",
    ) -> dict[str, object]:
        return await self.revoke_change_request_approval_with_body(
            project_ref,
            cr_id,
            head_sha,
            body="Code review found new issues.",
        )

    async def revoke_change_request_approval_with_body(
        self, project_ref: str, cr_id: str, head_sha: str, body: str
    ) -> dict[str, object]:
        owner, repo = _split_project_ref(project_ref)
        if not head_sha:
            cr = await self.fetch_change_request(project_ref, cr_id)
            head_sha = cr.diff_refs.get("head_sha", cr.head_sha)
        return await self._client.create_pull_review(
            owner,
            repo,
            int(cr_id),
            "REQUEST_CHANGES",
            head_sha,
            body=body,
        )


def _split_project_ref(project_ref: str) -> tuple[str, str]:
    parts = project_ref.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid GitHub project_ref {project_ref!r}: expected 'owner/repo'")
    return parts[0], parts[1]


def _parse_change_request(
    data: dict[str, object],
    project_ref: str,
    cr_id: str,
) -> ChangeRequest:
    user = data.get("user") or {}
    head = data.get("head") or {}
    base = data.get("base") or {}
    head_sha = str(head.get("sha") or "")
    base_sha = str(base.get("sha") or "")

    head_repo = head.get("repo") if isinstance(head, dict) else None
    base_repo = base.get("repo") if isinstance(base, dict) else None
    head_full_name = str(head_repo.get("full_name") or "") if isinstance(head_repo, dict) else ""
    base_full_name = str(base_repo.get("full_name") or "") if isinstance(base_repo, dict) else ""
    head_repo_url = ""
    if head_full_name and base_full_name and head_full_name != base_full_name:
        head_repo_url = str(head_repo.get("clone_url") or "") if isinstance(head_repo, dict) else ""

    return ChangeRequest(
        project_ref=project_ref,
        cr_id=str(data.get("number") or cr_id),
        title=str(data.get("title") or ""),
        description=str(data.get("body") or ""),
        author=str(user.get("login") or "") if isinstance(user, dict) else "",
        source_branch=str(head.get("ref") or "") if isinstance(head, dict) else "",
        target_branch=str(base.get("ref") or "") if isinstance(base, dict) else "",
        state=str(data.get("state") or ""),
        draft=bool(data.get("draft")),
        web_url=str(data.get("html_url") or ""),
        head_sha=head_sha,
        diff_refs={
            "base_sha": base_sha,
            "start_sha": base_sha,
            "head_sha": head_sha,
        }
        if head_sha and base_sha
        else {},
        head_repo_url=head_repo_url,
    )
