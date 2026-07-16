import os
from pathlib import Path

from code_review_bot.agent.acp import AcpAgentConfig, AcpCodingAgent
from code_review_bot.agent.protocol import CodingAgent
from code_review_bot.config import Settings

_OPENCODE_RUNTIME_ENV = (
    "OPENCODE_UPSTREAM_ENDPOINT",
    "OPENCODE_UPSTREAM_API_KEY",
    "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX",
    "NPM_CONFIG_REGISTRY",
)


def build_coding_agent(
    settings: Settings,
    cwd: str | Path,
) -> CodingAgent:
    """Instantiate the appropriate CodingAgent based on application settings."""
    env = (
        {name: f"${name}" for name in _OPENCODE_RUNTIME_ENV if name in os.environ}
        if settings.acp_agent_type == "opencode"
        else {}
    )
    config = AcpAgentConfig(
        command=settings.resolved_acp_command,
        args=settings.resolved_acp_args,
        env=env,
        model=settings.acp_model,
        model_via_acp=settings.acp_agent_type == "opencode",
        model_config_option="model" if settings.acp_agent_type == "codex" else None,
        stream_limit=settings.acp_stream_limit,
        verbose=settings.acp_verbose,
    )
    return AcpCodingAgent(config, cwd)
