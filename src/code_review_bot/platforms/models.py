from dataclasses import dataclass, field


@dataclass
class InlineThread:
    """One inline diff comment thread, with any replies and platform resolution status."""

    file_path: str
    line_range: str
    description: str
    replies: list[str] = field(default_factory=list)
    is_resolved: bool = False


@dataclass
class ChangeRequest:
    """Platform-agnostic descriptor for a merge/pull request."""

    project_ref: str
    cr_id: str
    title: str
    description: str
    author: str
    source_branch: str
    target_branch: str
    state: str
    draft: bool
    web_url: str
    head_sha: str
    diff_refs: dict[str, str] = field(default_factory=dict)
    head_repo_url: str = ""


@dataclass
class InlinePosition:
    """Position descriptor for an inline comment in a diff."""

    file_path: str
    new_line: int
    base_sha: str
    start_sha: str
    head_sha: str
