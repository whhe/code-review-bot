import asyncio
import logging
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
        self.initialize_response = SimpleNamespace(agent_info=None, protocol_version=1)

    async def initialize(self, **kwargs: object) -> SimpleNamespace:
        return self.initialize_response

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


@pytest.mark.asyncio
async def test_acp_runtime_restarts_agent_after_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    connections = [_Connection(), _Connection()]
    spawn_count = 0

    async def timed_out_prompt(**kwargs: object) -> SimpleNamespace:
        await asyncio.sleep(1)
        return SimpleNamespace(usage=None)

    connections[0].prompt = timed_out_prompt  # type: ignore[method-assign]

    @asynccontextmanager
    async def fake_spawn_agent_process(
        client: object,
        command: str,
        *args: str,
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> AsyncIterator[tuple[_Connection, SimpleNamespace]]:
        nonlocal spawn_count
        connection = connections[spawn_count]
        spawn_count += 1
        yield connection, SimpleNamespace(stdin=None, stdout=None, stderr=None, _transport=None)

    monkeypatch.setattr(acp_sdk, "spawn_agent_process", fake_spawn_agent_process)
    monkeypatch.setattr("code_review_bot.agent.acp._RETRY_BACKOFF_SECONDS", 0)
    agent = AcpCodingAgent(
        AcpAgentConfig(command="agent", request_timeout=0.01, max_retries=1, verbose=False),
        tmp_path,
    )

    await agent.run("review")

    assert spawn_count == 2


@pytest.mark.asyncio
async def test_acp_runtime_extends_prompt_timeout_when_session_is_active(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    client_ref: object | None = None

    async def active_prompt(**kwargs: object) -> SimpleNamespace:
        assert client_ref is not None
        await getattr(client_ref, "session_update")("session-1", object())
        await asyncio.sleep(0.02)
        return SimpleNamespace(usage=None)

    @asynccontextmanager
    async def fake_spawn_agent_process(
        client: object,
        command: str,
        *args: str,
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> AsyncIterator[tuple[_Connection, SimpleNamespace]]:
        nonlocal client_ref
        client_ref = client
        connection = _Connection()
        connection.prompt = active_prompt  # type: ignore[method-assign]
        yield connection, SimpleNamespace(stdin=None, stdout=None, stderr=None, _transport=None)

    monkeypatch.setattr(acp_sdk, "spawn_agent_process", fake_spawn_agent_process)
    agent = AcpCodingAgent(
        AcpAgentConfig(command="agent", request_timeout=0.05, max_retries=0, verbose=False),
        tmp_path,
    )

    await agent.run("review")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [TimeoutError("timeout"), ConnectionError("closed"), EOFError()]
)
async def test_acp_runtime_propagates_transient_failure_after_retries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: Exception,
) -> None:
    spawn_count = 0

    async def failing_prompt(**kwargs: object) -> SimpleNamespace:
        raise failure

    @asynccontextmanager
    async def fake_spawn_agent_process(
        client: object,
        command: str,
        *args: str,
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> AsyncIterator[tuple[_Connection, SimpleNamespace]]:
        nonlocal spawn_count
        spawn_count += 1
        connection = _Connection()
        connection.prompt = failing_prompt  # type: ignore[method-assign]
        yield connection, SimpleNamespace(stdin=None, stdout=None, stderr=None, _transport=None)

    monkeypatch.setattr(acp_sdk, "spawn_agent_process", fake_spawn_agent_process)
    monkeypatch.setattr("code_review_bot.agent.acp._RETRY_BACKOFF_SECONDS", 0)
    agent = AcpCodingAgent(
        AcpAgentConfig(command="agent", request_timeout=0.01, max_retries=1, verbose=False),
        tmp_path,
    )

    with pytest.raises(type(failure)):
        await agent.run("review")

    assert spawn_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("agent_info", "command", "expected_log"),
    [
        (
            SimpleNamespace(name="claude-agent-acp", version="0.60.0"),
            "npx",
            "[agent runtime] name=claude-agent-acp version=0.60.0 protocol=1 command=npx",
        ),
        (
            None,
            "agent",
            "[agent runtime] name=(unknown) version=(unknown) protocol=1 command=agent",
        ),
    ],
)
async def test_acp_runtime_logs_agent_identity_from_initialize_response(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    agent_info: SimpleNamespace | None,
    command: str,
    expected_log: str,
) -> None:
    connection = _Connection()
    connection.initialize_response = SimpleNamespace(
        agent_info=agent_info,
        protocol_version=1,
    )

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
    agent = AcpCodingAgent(AcpAgentConfig(command=command, verbose=False), tmp_path)

    with caplog.at_level(logging.INFO, logger="code_review_bot.agent.acp"):
        await agent.run_once("review")

    assert expected_log in caplog.messages
