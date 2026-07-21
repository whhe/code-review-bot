from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import acp as acp_sdk
import pytest

from code_review_bot.agent.acp import AcpAgentConfig, AcpCodingAgent


class _Connection:
    def __init__(self) -> None:
        self.model_calls: list[tuple[str, str]] = []
        self.config_calls: list[tuple[str, str, str]] = []

    async def initialize(self, **kwargs: object) -> None:
        return None

    async def new_session(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(session_id="session-1")

    async def set_session_model(self, model_id: str, session_id: str) -> None:
        self.model_calls.append((model_id, session_id))

    async def set_config_option(self, config_id: str, session_id: str, value: str) -> None:
        self.config_calls.append((config_id, session_id, value))

    async def prompt(self, **kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(usage=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "model_via_acp",
        "model_config_option",
        "expected_model_calls",
        "expected_config_calls",
        "expected_env_model",
    ),
    [
        (True, None, [("code-review/qwen-max", "session-1")], [], None),
        (False, "model", [], [("model", "session-1", "code-review/qwen-max")], None),
        (False, None, [], [], "code-review/qwen-max"),
    ],
)
async def test_acp_runtime_applies_agent_specific_model_selection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    model_via_acp: bool,
    model_config_option: str | None,
    expected_model_calls: list[tuple[str, str]],
    expected_config_calls: list[tuple[str, str, str]],
    expected_env_model: str | None,
) -> None:
    connection = _Connection()
    captured_env: dict[str, str] = {}

    @asynccontextmanager
    async def fake_spawn_agent_process(
        client: object,
        command: str,
        *args: str,
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> AsyncIterator[tuple[_Connection, SimpleNamespace]]:
        captured_env.update(env or {})
        yield connection, SimpleNamespace(stdin=None, stdout=None, stderr=None, _transport=None)

    monkeypatch.setattr(acp_sdk, "spawn_agent_process", fake_spawn_agent_process)
    agent = AcpCodingAgent(
        AcpAgentConfig(
            command="agent",
            model="code-review/qwen-max",
            model_via_acp=model_via_acp,
            model_config_option=model_config_option,
            verbose=False,
        ),
        tmp_path,
    )

    await agent.run_once("review")

    assert connection.model_calls == expected_model_calls
    assert connection.config_calls == expected_config_calls
    assert captured_env.get("ANTHROPIC_MODEL") == expected_env_model


@pytest.mark.asyncio
async def test_acp_runtime_returns_runtime_model_from_agent_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    connection = _Connection()

    async def prompt_with_model(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(usage=None, model="provider/runtime-model")

    connection.prompt = prompt_with_model  # type: ignore[method-assign]

    @asynccontextmanager
    async def fake_spawn_agent_process(
        client: object,
        command: str,
        *args: str,
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> AsyncIterator[tuple[_Connection, SimpleNamespace]]:
        yield connection, SimpleNamespace(stdin=None, stdout=None, stderr=None, _transport=None)

    monkeypatch.setattr(acp_sdk, "spawn_agent_process", fake_spawn_agent_process)
    agent = AcpCodingAgent(AcpAgentConfig(command="agent", verbose=False), tmp_path)

    run = await agent.run_once("review")

    assert run.model == "provider/runtime-model"
