from code_review_bot.platforms.gitlab.client import GitLabClient
from code_review_bot.platforms.models import ChangeRequest, InlinePosition, InlineThread


class GitLabAdapter:
    """PlatformAdapter implementation for GitLab.

    project_ref is always the numeric GitLab project ID as a string.
    cr_id is always the MR IID as a string.
    """

    platform_name = "gitlab"

    def __init__(self, client: GitLabClient, metadata_author_id: str = "") -> None:
        self._client = client
        self._configured_metadata_author_id = metadata_author_id
        self._resolved_metadata_author_id: str | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    async def resolve_project_ref(self, project_path: str) -> str:
        project_id = await self._client.get_project_id_by_path(project_path)
        return str(project_id)

    async def fetch_change_request(self, project_ref: str, cr_id: str) -> ChangeRequest:
        data = await self._client.get_merge_request(int(project_ref), int(cr_id))
        return _parse_change_request(data, project_ref, cr_id)

    async def list_notes(self, project_ref: str, cr_id: str) -> list[dict[str, object]]:
        notes = await self._client.list_merge_request_notes(int(project_ref), int(cr_id))
        metadata_author_id = await self._get_metadata_author_id()
        return [note for note in notes if _gitlab_note_author_id(note) == metadata_author_id]

    async def _get_metadata_author_id(self) -> str:
        if self._resolved_metadata_author_id is not None:
            return self._resolved_metadata_author_id
        if self._configured_metadata_author_id:
            self._resolved_metadata_author_id = self._configured_metadata_author_id
            return self._resolved_metadata_author_id
        user = await self._client.get_authenticated_user()
        author_id = str(user.get("id") or "")
        if not author_id:
            raise RuntimeError(
                "GitLab authenticated user response is missing id; "
                "set REVIEW_METADATA_AUTHOR_ID explicitly"
            )
        self._resolved_metadata_author_id = author_id
        return author_id

    async def list_inline_threads(self, project_ref: str, cr_id: str) -> list[InlineThread]:
        discussions = await self._client.list_merge_request_discussions(
            int(project_ref), int(cr_id)
        )
        threads: list[InlineThread] = []
        for disc in discussions:
            notes = disc.get("notes") or []
            if not notes:
                continue
            first_note = notes[0]
            if first_note.get("system"):
                continue
            position = first_note.get("position") or {}
            file_path = str(position.get("new_path") or "")
            if not file_path:
                continue
            new_line = position.get("new_line")
            line_range = str(new_line) if new_line is not None else ""
            threads.append(
                InlineThread(
                    file_path=file_path,
                    line_range=line_range,
                    description=str(first_note.get("body") or "").strip(),
                    replies=[str(n.get("body") or "").strip() for n in notes[1:]],
                    is_resolved=bool(disc.get("resolved", first_note.get("resolved"))),
                )
            )
        return threads

    async def publish_summary(self, project_ref: str, cr_id: str, body: str) -> dict[str, object]:
        return await self._client.create_merge_request_note(int(project_ref), int(cr_id), body)

    async def publish_inline_comment(
        self,
        project_ref: str,
        cr_id: str,
        body: str,
        position: InlinePosition,
    ) -> dict[str, object]:
        gitlab_position: dict[str, object] = {
            "base_sha": position.base_sha,
            "start_sha": position.start_sha,
            "head_sha": position.head_sha,
            "position_type": "text",
            "old_path": position.file_path,
            "new_path": position.file_path,
            "new_line": position.new_line,
        }
        return await self._client.create_merge_request_discussion(
            int(project_ref), int(cr_id), body, gitlab_position
        )

    async def approve_change_request(
        self, project_ref: str, cr_id: str, head_sha: str
    ) -> dict[str, object]:
        return await self._client.approve_merge_request(int(project_ref), int(cr_id), head_sha)

    async def revoke_change_request_approval(
        self, project_ref: str, cr_id: str, head_sha: str = ""
    ) -> dict[str, object]:
        return await self._client.unapprove_merge_request(int(project_ref), int(cr_id))


def _parse_change_request(
    data: dict[str, object],
    project_ref: str,
    cr_id: str,
) -> ChangeRequest:
    author = data.get("author") or {}
    diff_refs_raw = data.get("diff_refs") or {}
    diff_refs = {str(k): str(v) for k, v in diff_refs_raw.items() if v is not None}
    head_sha = str(data.get("sha") or diff_refs.get("head_sha") or "")
    return ChangeRequest(
        project_ref=str(data.get("project_id") or project_ref),
        cr_id=str(data.get("iid") or cr_id),
        title=str(data.get("title") or ""),
        description=str(data.get("description") or ""),
        author=str(
            (author.get("username") or author.get("name") or "") if isinstance(author, dict) else ""
        ),
        source_branch=str(data.get("source_branch") or ""),
        target_branch=str(data.get("target_branch") or ""),
        state=str(data.get("state") or ""),
        draft=bool(data.get("draft")),
        web_url=str(data.get("web_url") or ""),
        head_sha=head_sha,
        diff_refs=diff_refs,
    )


def _gitlab_note_author_id(note: dict[str, object]) -> str:
    author = note.get("author")
    if not isinstance(author, dict):
        return ""
    return str(author.get("id") or "")
