from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, computed_field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from code_review_bot.agent.presets import resolve_acp_launcher


class Settings(BaseSettings):
    """Application configuration loaded from environment variables and .env files."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # extend the Literal when adding a new platform adapter
    git_platform_type: Literal["gitlab", "github"] = "gitlab"

    git_repo_url: str
    git_repo_token: str

    # Optional override for the platform API base URL. When empty, the value
    # is derived as "scheme://host[:port]" from git_repo_url. Set this only
    # when the platform is hosted under a sub-path (rare).
    git_platform_url_override: str = Field(default="", alias="GIT_PLATFORM_URL")

    repo_default_branch: str = "main"

    output_language: str = Field(
        default="english",
        description=(
            "Natural language for review findings (description/reason) and the "
            "change-request summary markdown. Examples: english, chinese. "
            "Code, configs, logs and prompt templates stay English."
        ),
    )

    acp_agent_type: str = Field(
        default="claude",
        description=(
            "Built-in: claude, codex (launcher from presets; omit ACP_COMMAND/ACP_ARGS). "
            "Any other value requires ACP_COMMAND and ACP_ARGS."
        ),
    )
    acp_command: str | None = Field(
        default=None,
        description="Required when ACP_AGENT_TYPE is not built-in. Optional override for built-in.",
    )
    acp_args: list[str] | None = Field(
        default=None,
        description="Required when ACP_AGENT_TYPE is not built-in. Optional override for built-in.",
    )
    acp_model: str | None = None
    acp_stream_limit: int = Field(
        default=10 * 1024 * 1024,
        description="Max bytes for one ACP newline-delimited JSON frame from the coding agent.",
    )
    acp_verbose: bool = Field(
        default=True,
        description=(
            "Log ACP input prompts, tool calls, and agent message chunks during review runs."
        ),
    )

    log_level: str = "INFO"
    log_dir: str = Field(
        default="logs",
        description=(
            "Root directory for bot-generated files, relative to CODE_REVIEW_BOT_ROOT. "
            "Subdirectories are fixed: '<log_dir>/sessions/' for per-review session "
            "logs, '<log_dir>/debug-reports/' for --debug Markdown reports. "
            "Empty disables file logging."
        ),
    )
    review_skill: str = Field(
        default="",
        description=(
            "Review skill: local directory path or http(s) URL. "
            "Local paths resolve against CODE_REVIEW_BOT_ROOT. "
            "Remote URLs are passed to the coding agent to fetch on demand."
        ),
    )
    review_exclude: list[str] = Field(
        default_factory=list,
        description=(
            "Extra glob patterns for files to exclude from review (JSON array). "
            "Added on top of built-in defaults (*.lock, *.min.js, etc.). "
            'Example: REVIEW_EXCLUDE=["dist/**", "*.pb.go"]'
        ),
    )
    review_include: list[str] = Field(
        default_factory=list,
        description=(
            "Glob patterns restricting review to matching files only (JSON array). "
            "Empty list means all files (subject to excludes). "
            'Example: REVIEW_INCLUDE=["src/**", "tests/**"]'
        ),
    )
    clone_base_dir: str | None = None
    clone_depth: int = 0

    @model_validator(mode="after")
    def validate_acp_launcher(self) -> "Settings":
        resolve_acp_launcher(
            self.acp_agent_type,
            command=self.acp_command,
            args=self.acp_args,
        )
        return self

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_acp_command(self) -> str:
        return resolve_acp_launcher(
            self.acp_agent_type,
            command=self.acp_command,
            args=self.acp_args,
        )[0]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def resolved_acp_args(self) -> list[str]:
        return resolve_acp_launcher(
            self.acp_agent_type,
            command=self.acp_command,
            args=self.acp_args,
        )[1]

    @field_validator("acp_agent_type", mode="before")
    @classmethod
    def normalize_acp_agent_type(cls, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            msg = "ACP_AGENT_TYPE must be a non-empty string"
            raise ValueError(msg)
        return value.strip().lower()

    @field_validator("acp_command", mode="before")
    @classmethod
    def blank_command_is_none(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value).strip() if isinstance(value, str) else value

    @field_validator("acp_model", mode="before")
    @classmethod
    def blank_model_is_none(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value).strip() if isinstance(value, str) else value

    @property
    def git_platform_url(self) -> str:
        """Platform API base URL.

        Returns the GIT_PLATFORM_URL override when set, otherwise derives
        "scheme://host[:port]" from GIT_REPO_URL.
        """
        if self.git_platform_url_override:
            return self.git_platform_url_override.rstrip("/")
        return _parse_platform_base(self.git_repo_url)

    @property
    def git_project_path(self) -> str:
        """Project path (e.g. 'group/project') parsed from GIT_REPO_URL.

        If GIT_PLATFORM_URL override is set and is a prefix of GIT_REPO_URL,
        the project path is taken as the remainder after that prefix; this
        keeps sub-path installs (e.g. https://example.com/gitlab/group/proj.git)
        working correctly.
        """
        override = self.git_platform_url_override.rstrip("/")
        if override and self.git_repo_url.startswith(override + "/"):
            remainder = self.git_repo_url[len(override) + 1 :]
            return _strip_git_suffix(remainder)
        return _parse_project_path(self.git_repo_url)

    @property
    def github_rest_api_base(self) -> str:
        """GitHub REST API v3 root (github.com → api.github.com; GHES → host/api/v3)."""
        return _github_rest_api_base(self.git_repo_url, self.git_platform_url_override)

    @property
    def github_graphql_url(self) -> str:
        """GitHub GraphQL endpoint paired with github_rest_api_base."""
        return _github_graphql_url(self.github_rest_api_base)

    @property
    def repo_clone_url(self) -> str:
        """Clone URL with the repo token injected as HTTP basic auth."""
        if not self.git_repo_url or not self.git_repo_token:
            return self.git_repo_url
        return _inject_https_token(self.git_repo_url, self.git_repo_token)

    @property
    def review_session_log_dir(self) -> str:
        """Subdir under log_dir for per-review session logs; empty disables it."""
        return self._log_subdir("sessions")

    @property
    def debug_review_output_dir(self) -> str:
        """Subdir under log_dir for --debug Markdown reports; empty disables it."""
        return self._log_subdir("debug-reports")

    def _log_subdir(self, name: str) -> str:
        root = self.log_dir.strip()
        if not root:
            return ""
        return f"{root.rstrip('/')}/{name}"


@lru_cache
def get_settings() -> Settings:
    # Singleton per process. Tests should instantiate Settings() directly
    # (with _env_file=None when isolating from a local .env file).
    return Settings()


_GITHUB_COM_HOSTS = frozenset({"github.com", "www.github.com"})


def _github_rest_api_base(git_repo_url: str, platform_url_override: str = "") -> str:
    if platform_url_override:
        return _normalize_github_rest_base(platform_url_override)
    parsed = urlparse(git_repo_url)
    if not parsed.scheme or not parsed.netloc:
        return "https://api.github.com"
    hostname = (parsed.hostname or "").lower()
    if hostname in _GITHUB_COM_HOSTS:
        return "https://api.github.com"
    return f"{parsed.scheme}://{parsed.netloc}/api/v3"


def _normalize_github_rest_base(override: str) -> str:
    base = override.rstrip("/")
    if not base:
        return "https://api.github.com"
    hostname = (urlparse(base).hostname or "").lower()
    if hostname == "api.github.com" or hostname in _GITHUB_COM_HOSTS:
        return "https://api.github.com"
    if base.endswith("/api/v3"):
        return base
    return f"{base}/api/v3"


def _github_graphql_url(rest_api_base: str) -> str:
    rest = rest_api_base.rstrip("/")
    if rest == "https://api.github.com" or urlparse(rest).hostname == "api.github.com":
        return f"{rest}/graphql"
    if rest.endswith("/api/v3"):
        return f"{rest[: -len('/api/v3')]}/api/graphql"
    return f"{rest}/graphql"


def _parse_platform_base(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_project_path(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    return _strip_git_suffix(parsed.path.lstrip("/"))


def _strip_git_suffix(path: str) -> str:
    path = path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    return path


def _inject_https_token(url: str, token: str) -> str:
    if "://" not in url:
        return url
    protocol, rest = url.split("://", 1)
    if "@" in rest:
        rest = rest.split("@", 1)[1]
    return f"{protocol}://oauth2:{token}@{rest}"
