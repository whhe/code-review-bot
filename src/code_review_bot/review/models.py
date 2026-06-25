from pydantic import BaseModel, ConfigDict, Field

from code_review_bot.platforms.models import ChangeRequest, InlineThread

__all__ = ["InlineThread", "ReviewTaskContext", "ReviewOutcome"]


class ReviewTaskContext(BaseModel):
    """All information an agent needs to perform a single review run."""

    change_request: ChangeRequest
    workspace_path: str
    source_branch: str
    target_branch: str
    base_sha: str
    start_sha: str
    head_sha: str
    previous_head_sha: str = ""
    output_language: str = "english"
    excluded_patterns: list[str] = Field(default_factory=list)
    included_patterns: list[str] = Field(default_factory=list)
    inline_threads: list[InlineThread] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


class ReviewOutcome(BaseModel):
    summary: str
    published: bool = False
    inline_comments: int = 0
    report_path: str = ""
    approved: bool | None = None
