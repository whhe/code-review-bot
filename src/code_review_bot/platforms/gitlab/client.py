from urllib.parse import quote

import httpx


class GitLabClient:
    """Thin async wrapper around the GitLab REST API v4.

    Returns raw dicts; conversion to domain models is the adapter's responsibility.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{self.base_url}/api/v4",
            headers={"PRIVATE-TOKEN": token},
            timeout=30,
            trust_env=False,
        )

    async def __aenter__(self) -> "GitLabClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def get_merge_request(self, project_id: int, mr_iid: int) -> dict[str, object]:
        response = await self._client.get(f"/projects/{project_id}/merge_requests/{mr_iid}")
        response.raise_for_status()
        return dict(response.json())

    async def list_merge_request_notes(
        self, project_id: int, mr_iid: int
    ) -> list[dict[str, object]]:
        response = await self._client.get(f"/projects/{project_id}/merge_requests/{mr_iid}/notes")
        response.raise_for_status()
        return list(response.json())

    async def list_merge_request_discussions(
        self, project_id: int, mr_iid: int
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        page = 1
        while True:
            response = await self._client.get(
                f"/projects/{project_id}/merge_requests/{mr_iid}/discussions",
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = response.json()
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items

    async def get_project_id_by_path(self, project_path: str) -> int:
        encoded_path = quote(project_path, safe="")
        response = await self._client.get(f"/projects/{encoded_path}")
        response.raise_for_status()
        return int(response.json()["id"])

    async def create_merge_request_note(
        self, project_id: int, mr_iid: int, body: str
    ) -> dict[str, object]:
        response = await self._client.post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/notes",
            data={"body": body},
        )
        response.raise_for_status()
        return dict(response.json())

    async def create_merge_request_discussion(
        self,
        project_id: int,
        mr_iid: int,
        body: str,
        position: dict[str, object],
    ) -> dict[str, object]:
        response = await self._client.post(
            f"/projects/{project_id}/merge_requests/{mr_iid}/discussions",
            data={"body": body, **{f"position[{key}]": value for key, value in position.items()}},
        )
        response.raise_for_status()
        return dict(response.json())
