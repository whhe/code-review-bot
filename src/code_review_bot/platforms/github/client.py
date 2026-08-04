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

    async def get_authenticated_user(self) -> dict[str, object]:
        response = await self._client.get("/user")
        response.raise_for_status()
        return dict(response.json())

    async def list_issue_comments(
        self, owner: str, repo: str, issue_number: int
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        page = 1
        while True:
            response = await self._client.get(
                f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = response.json()
            items.extend(batch)
            if len(batch) < 100:
                return items
            page += 1

    async def list_pull_reviews(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        page = 1
        while True:
            response = await self._client.get(
                f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            batch = response.json()
            items.extend(batch)
            if len(batch) < 100:
                break
            page += 1
        return items

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
        threads_query = """
        query($owner: String!, $repo: String!, $number: Int!, $threadsCursor: String) {
          repository(owner: $owner, name: $repo) {
            pullRequest(number: $number) {
              reviewThreads(first: 100, after: $threadsCursor) {
                nodes {
                  id
                  isResolved
                  comments(first: 100) {
                    nodes { body path line originalLine }
                    pageInfo { hasNextPage endCursor }
                  }
                }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        comments_query = """
        query($threadId: ID!, $commentsCursor: String) {
          node(id: $threadId) {
            ... on PullRequestReviewThread {
              comments(first: 100, after: $commentsCursor) {
                nodes { body path line originalLine }
                pageInfo { hasNextPage endCursor }
              }
            }
          }
        }
        """
        threads: list[dict[str, object]] = []
        threads_cursor: str | None = None
        while True:
            response = await self._client.post(
                self.graphql_url,
                json={
                    "query": threads_query,
                    "variables": {
                        "owner": owner,
                        "repo": repo,
                        "number": pr_number,
                        "threadsCursor": threads_cursor,
                    },
                },
            )
            response.raise_for_status()
            repository = _graphql_data(response).get("repository") or {}
            pull_request = repository.get("pullRequest") or {}
            connection = pull_request.get("reviewThreads") or {}
            page_threads = list(connection.get("nodes") or [])
            for thread in page_threads:
                await self._load_remaining_thread_comments(thread, comments_query)
            threads.extend(page_threads)

            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                return threads
            threads_cursor = _next_cursor(page_info, "review threads")

    async def _load_remaining_thread_comments(
        self,
        thread: dict[str, object],
        query: str,
    ) -> None:
        comments = thread.get("comments") or {}
        page_info = comments.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return

        thread_id = thread.get("id")
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("GitHub review thread pagination response is missing the thread id")

        nodes = list(comments.get("nodes") or [])
        comments_cursor = _next_cursor(page_info, "review thread comments")
        while True:
            response = await self._client.post(
                self.graphql_url,
                json={
                    "query": query,
                    "variables": {
                        "threadId": thread_id,
                        "commentsCursor": comments_cursor,
                    },
                },
            )
            response.raise_for_status()
            node = _graphql_data(response).get("node") or {}
            connection = node.get("comments") or {}
            nodes.extend(connection.get("nodes") or [])
            page_info = connection.get("pageInfo") or {}
            if not page_info.get("hasNextPage"):
                comments["nodes"] = nodes
                comments["pageInfo"] = {"hasNextPage": False, "endCursor": None}
                return
            comments_cursor = _next_cursor(page_info, "review thread comments")

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


def _graphql_data(response: httpx.Response) -> dict[str, object]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("GitHub GraphQL response is not a JSON object")

    errors = payload.get("errors")
    if errors:
        messages = [
            str(error.get("message") or error) for error in errors if isinstance(error, dict)
        ]
        detail = "; ".join(messages) if messages else str(errors)
        raise RuntimeError(f"GitHub GraphQL query failed: {detail}")

    data = payload.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub GraphQL response is missing data")
    return data


def _next_cursor(page_info: dict[str, object], connection_name: str) -> str:
    cursor = page_info.get("endCursor")
    if not isinstance(cursor, str) or not cursor:
        raise ValueError(f"GitHub {connection_name} pagination response is missing endCursor")
    return cursor
