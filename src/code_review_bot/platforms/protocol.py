from typing import Protocol, runtime_checkable

from code_review_bot.platforms.models import ChangeRequest, InlinePosition, InlineThread


class PlatformAdapter(Protocol):
    """Abstraction layer over a code hosting platform."""

    platform_name: str

    async def resolve_project_ref(self, project_path: str) -> str: ...

    async def fetch_change_request(self, project_ref: str, cr_id: str) -> ChangeRequest: ...

    async def list_notes(self, project_ref: str, cr_id: str) -> list[dict[str, object]]: ...

    async def list_inline_threads(self, project_ref: str, cr_id: str) -> list[InlineThread]:
        """Return all inline diff comment threads, resolved and open alike."""
        ...

    async def publish_summary(
        self, project_ref: str, cr_id: str, body: str
    ) -> dict[str, object]: ...

    async def publish_inline_comment(
        self,
        project_ref: str,
        cr_id: str,
        body: str,
        position: InlinePosition,
    ) -> dict[str, object]: ...

    async def approve_change_request(
        self, project_ref: str, cr_id: str, head_sha: str
    ) -> dict[str, object]: ...

    async def revoke_change_request_approval(
        self, project_ref: str, cr_id: str, head_sha: str = ""
    ) -> dict[str, object]: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class ReviewBodyApprovalAdapter(Protocol):
    """Optional capability for publishing approval decisions with a full review body."""

    async def approve_change_request_with_body(
        self, project_ref: str, cr_id: str, head_sha: str, body: str
    ) -> dict[str, object]: ...

    async def revoke_change_request_approval_with_body(
        self, project_ref: str, cr_id: str, head_sha: str, body: str
    ) -> dict[str, object]: ...
