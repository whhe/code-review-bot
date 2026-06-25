import httpx


class GitHubClient:
    """Thin async wrapper around the GitHub REST API v3.

    Returns raw dicts; conversion to domain models is the adapter's responsibility.
    """

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        graphql_url: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.graphql_url = graphql_url or f"{self.base_url}/graphql"
        self._owned_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30,
            trust_env=False,
        )

    async def aclose(self) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> dict[str, object]:
        response = await self._client.get(f"/repos/{owner}/{repo}/pulls/{pr_number}")
        response.raise_for_status()
        return dict(response.json())

    async def list_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> list[dict[str, object]]:
        response = await self._client.get(f"/repos/{owner}/{repo}/issues/{issue_number}/comments")
        response.raise_for_status()
        return list(response.json())

    async def list_pull_review_comments(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, object]]:
        """Return all inline review comments on a pull request (paginated)."""
        items: list[dict[str, object]] = []
        page = 1
        while True:
            response = await self._client.get(
                f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
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

    async def create_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> dict[str, object]:
        response = await self._client.post(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        response.raise_for_status()
        return dict(response.json())

    async def query_resolved_threads(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, object]]:
        """Return review threads from GitHub GraphQL API, each with isResolved and comments."""
        query = """
        query($owner: String!, $repo: String!, $number: Int!) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              reviewThreads(first: 100) {
                nodes {
                  isResolved
                  comments(first: 100) {
                    nodes { body path line originalLine }
                  }
                }
              }
            }
          }
        }
        """
        response = await self._client.post(
            self.graphql_url,
            json={
                "query": query,
                "variables": {"owner": owner, "repo": repo, "number": pr_number},
            },
        )
        response.raise_for_status()
        data = response.json()
        threads = (
            (data.get("data") or {})
            .get("repository", {})
            .get("pullRequest", {})
            .get("reviewThreads", {})
            .get("nodes", [])
        )
        return list(threads)

    async def create_pull_comment(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        commit_id: str,
        path: str,
        line: int,
    ) -> dict[str, object]:
        response = await self._client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            json={
                "body": body,
                "commit_id": commit_id,
                "path": path,
                "line": line,
                "side": "RIGHT",
            },
        )
        response.raise_for_status()
        return dict(response.json())

    async def create_pull_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        event: str,
        commit_id: str,
        body: str = "",
    ) -> dict[str, object]:
        payload: dict[str, object] = {"event": event, "commit_id": commit_id}
        if body:
            payload["body"] = body
        response = await self._client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            json=payload,
        )
        response.raise_for_status()
        return dict(response.json())
