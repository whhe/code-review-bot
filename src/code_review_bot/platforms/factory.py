from code_review_bot.config import Settings
from code_review_bot.platforms.protocol import PlatformAdapter


def build_platform_adapter(settings: Settings) -> PlatformAdapter:
    """Instantiate the PlatformAdapter for the configured git platform.

    Only "gitlab" is implemented today. Extend the dispatch table when a new
    adapter is added.
    """
    if settings.git_platform_type == "gitlab":
        from code_review_bot.platforms.gitlab.adapter import GitLabAdapter
        from code_review_bot.platforms.gitlab.client import GitLabClient

        client = GitLabClient(settings.git_platform_url, settings.git_repo_token)
        return GitLabAdapter(
            client,
            metadata_author_id=settings.review_metadata_author_id,
        )

    if settings.git_platform_type == "github":
        from code_review_bot.platforms.github.adapter import GitHubAdapter
        from code_review_bot.platforms.github.client import GitHubClient

        client = GitHubClient(
            settings.git_repo_token,
            base_url=settings.github_rest_api_base,
            graphql_url=settings.github_graphql_url,
        )
        return GitHubAdapter(
            client,
            metadata_author_id=settings.review_metadata_author_id,
        )

    raise ValueError(
        f"Unsupported git platform: {settings.git_platform_type!r}. "
        "Implement a PlatformAdapter and register it here."
    )
