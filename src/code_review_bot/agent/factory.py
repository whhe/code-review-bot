from pathlib import Path

from code_review_bot.agent.acp import AcpAgentConfig, AcpCodingAgent
from code_review_bot.agent.protocol import CodingAgent
from code_review_bot.config import Settings


def build_coding_agent(settings: Settings, cwd: str | Path) -> CodingAgent:
    """Instantiate the appropriate CodingAgent based on application settings."""
    config = AcpAgentConfig(
        command=settings.resolved_acp_command,
        args=settings.resolved_acp_args,
        model=settings.acp_model,
        stream_limit=settings.acp_stream_limit,
        verbose=settings.acp_verbose,
    )
    return AcpCodingAgent(config, cwd)
