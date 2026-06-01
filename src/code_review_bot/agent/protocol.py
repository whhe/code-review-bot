from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class AgentRunResult:
    """Structured output from a single agent run, including collected text and token usage."""

    text: str
    parts: list[dict[str, Any]]
    usage: dict[str, Any]


class CodingAgent(Protocol):
    """Protocol for coding agents that execute a single review run."""

    async def run_once(
        self,
        prompt: str,
        *,
        agent: str = "plan",
        system: str | None = None,
        files: list[str] | None = None,
        additional_directories: list[str] | None = None,
    ) -> AgentRunResult: ...
