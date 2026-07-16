from pathlib import Path

import pytest

from code_review_bot.agent.acp import AcpCodingAgent
from code_review_bot.agent.factory import build_coding_agent
from code_review_bot.config import Settings


def _settings(agent_type: str) -> Settings:
    return Settings(
        git_repo_url="https://git.example.test/group/project.git",
        git_repo_token="token",
        acp_agent_type=agent_type,
        _env_file=None,
    )


def test_opencode_agent_forwards_only_configured_runtime_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENCODE_UPSTREAM_ENDPOINT", "https://api.example.test/v1")
    monkeypatch.setenv("OPENCODE_UPSTREAM_API_KEY", "secret")
    monkeypatch.setenv("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", "65536")
    monkeypatch.delenv("NPM_CONFIG_REGISTRY", raising=False)

    agent = build_coding_agent(_settings("opencode"), tmp_path)

    assert isinstance(agent, AcpCodingAgent)
    assert agent.config.env == {
        "OPENCODE_UPSTREAM_ENDPOINT": "$OPENCODE_UPSTREAM_ENDPOINT",
        "OPENCODE_UPSTREAM_API_KEY": "$OPENCODE_UPSTREAM_API_KEY",
        "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": "$OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX",
    }
    assert "OPENCODE_DISABLE_PROJECT_CONFIG" not in agent.config.env
    assert "UPSTREAM_API_KEY" not in agent.config.env
    assert agent.config.model_via_acp is True


def test_non_opencode_agent_does_not_forward_opencode_runtime_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("OPENCODE_UPSTREAM_API_KEY", "secret")

    agent = build_coding_agent(_settings("claude"), tmp_path)

    assert isinstance(agent, AcpCodingAgent)
    assert agent.config.env == {}
    assert agent.config.model_via_acp is False


def test_codex_agent_uses_acp_model_config_option(tmp_path: Path) -> None:
    agent = build_coding_agent(_settings("codex"), tmp_path)

    assert isinstance(agent, AcpCodingAgent)
    assert agent.config.model_config_option == "model"
