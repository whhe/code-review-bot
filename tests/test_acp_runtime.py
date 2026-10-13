import asyncio
import json
import logging
import sys
import textwrap
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import acp as acp_sdk
import pytest
from acp.client.connection import ClientSideConnection
from acp.connection import StreamDirection, StreamEvent

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


@pytest.fixture
def stdio_agent_script(tmp_path: Path) -> Path:
    script = tmp_path / "agent.py"
    script.write_text(
        textwrap.dedent("""\
            import json
            import os
            import sys
            import time
            from pathlib import Path

            mode, attempts_path = sys.argv[1:]
            attempts_path = Path(attempts_path)

            def send(message):
                print(json.dumps({"jsonrpc": "2.0", **message}), flush=True)

            for line in sys.stdin:
                request = json.loads(line)
                method = request.get("method")
                if method == "initialize":
                    result = {"protocolVersion": 1, "agentCapabilities": {}}
                elif method == "session/new":
                    result = {"sessionId": "session-1"}
                elif method == "session/prompt":
                    attempts = (
                        attempts_path.read_text().splitlines() if attempts_path.exists() else []
                    )
                    with attempts_path.open("a") as handle:
                        attempt = {"pid": os.getpid(), "prompt": request["params"]["prompt"]}
                        handle.write(json.dumps(attempt) + "\\n")
                    send({"method": "_prompt_started", "params": {}})
                    if mode == "silent" or (mode == "retry" and not attempts):
                        # Keep the process alive until the client closes stdin.
                        for ignored in sys.stdin:
                            pass
                        break
                    if mode in ("heartbeat", "events"):
                        for _ in range(25):
                            time.sleep(0.05)
                            if mode == "heartbeat":
                                send({"method": "_heartbeat", "params": {}})
                            else:
                                send({"method": "session/update", "params": {
                                    "sessionId": "session-1",
                                    "update": {
                                        "sessionUpdate": "available_commands_update",
                                        "availableCommands": [],
                                    },
                                }})
                    send({"method": "session/update", "params": {
                        "sessionId": "session-1",
                        "update": {
                            "sessionUpdate": "agent_message_chunk",
                            "content": {"type": "text", "text": "done"},
                        },
                    }})
                    result = {"stopReason": "end_turn"}
                else:
                    continue
                send({"id": request["id"], "result": result})
        """),
        encoding="utf-8",
    )
    return script


@pytest.mark.parametrize("mode", ["heartbeat", "events"])
async def test_acp_long_review_stays_alive_with_incoming_messages(
    tmp_path: Path, stdio_agent_script: Path, mode: str
) -> None:
    agent = AcpCodingAgent(
        AcpAgentConfig(
            command=sys.executable,
            args=["-u", str(stdio_agent_script), mode, str(tmp_path / "attempts")],
            request_timeout=0.5,
            idle_timeout=0.5,
            max_retries=0,
            verbose=False,
        ),
        tmp_path,
    )
    result = await asyncio.wait_for(agent.run("review"), timeout=5)
    assert result.text == "done"


@pytest.mark.parametrize("mode", ["retry", "silent"])
async def test_acp_silence_restarts_and_resends_until_retry_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdio_agent_script: Path, mode: str
) -> None:
    attempts_path = tmp_path / "attempts"
    monkeypatch.setattr("code_review_bot.agent.acp._RETRY_BACKOFF_SECONDS", 0)
    agent = AcpCodingAgent(
        AcpAgentConfig(
            command=sys.executable,
            args=["-u", str(stdio_agent_script), mode, str(attempts_path)],
            idle_timeout=0.5,
            max_retries=1,
            verbose=False,
        ),
        tmp_path,
    )
    if mode == "silent":
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(agent.run("review"), timeout=5)
    else:
        result = await asyncio.wait_for(agent.run("review"), timeout=5)
        assert result.text == "done"
    attempts = [json.loads(line) for line in attempts_path.read_text().splitlines()]
    assert len(attempts) == 2
    assert attempts[0]["pid"] != attempts[1]["pid"]
    assert attempts[0]["prompt"] == attempts[1]["prompt"] == [{"type": "text", "text": "review"}]


@pytest.mark.parametrize("action", ["cancel", "send"])
async def test_acp_silent_connection_cancellation_and_outgoing_traffic(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stdio_agent_script: Path, action: str
) -> None:
    prompt_started = asyncio.Event()
    connections: list[ClientSideConnection] = []
    processes: list[asyncio.subprocess.Process] = []
    real_spawn = acp_sdk.spawn_agent_process

    def observe(event: StreamEvent) -> None:
        if (
            event.direction == StreamDirection.INCOMING
            and event.message.get("method") == "_prompt_started"
        ):
            prompt_started.set()

    @asynccontextmanager
    async def observed_spawn(
        client: object, command: str, *args: str, **kwargs: object
    ) -> AsyncIterator[tuple[ClientSideConnection, asyncio.subprocess.Process]]:
        async with real_spawn(client, command, *args, observers=[observe], **kwargs) as pair:
            connections.append(pair[0])
            processes.append(pair[1])
            yield pair

    monkeypatch.setattr(acp_sdk, "spawn_agent_process", observed_spawn)
    agent = AcpCodingAgent(
        AcpAgentConfig(
            command=sys.executable,
            args=["-u", str(stdio_agent_script), "silent", str(tmp_path / "attempts")],
            idle_timeout=0.5,
            max_retries=1 if action == "cancel" else 0,
            verbose=False,
        ),
        tmp_path,
    )
    task = asyncio.create_task(agent.run("review"))
    try:
        await asyncio.wait_for(prompt_started.wait(), timeout=5)
        if action == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            sent = 0

            async def send_notifications() -> None:
                nonlocal sent
                while True:
                    await connections[0].ext_notification("keepalive", {})
                    sent += 1
                    await asyncio.sleep(0.05)

            sender = asyncio.create_task(send_notifications())
            try:
                with pytest.raises(ConnectionError):
                    await asyncio.wait_for(task, timeout=3)
                assert sent > 1
            finally:
                sender.cancel()
                await asyncio.gather(sender, return_exceptions=True)
        assert len(processes) == 1
        assert processes[0].returncode is not None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
async def test_acp_runtime_does_not_timeout_long_review_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def long_prompt(**kwargs: object) -> SimpleNamespace:
        await asyncio.sleep(0.05)
        return SimpleNamespace(usage=None)

    @asynccontextmanager
    async def fake_spawn_agent_process(
        client: object,
        command: str,
        *args: str,
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> AsyncIterator[tuple[_Connection, SimpleNamespace]]:
        connection = _Connection()
        connection.prompt = long_prompt  # type: ignore[method-assign]
        yield connection, SimpleNamespace(stdin=None, stdout=None, stderr=None, _transport=None)

    monkeypatch.setattr(acp_sdk, "spawn_agent_process", fake_spawn_agent_process)
    agent = AcpCodingAgent(
        AcpAgentConfig(command="agent", request_timeout=0.01, max_retries=0, verbose=False),
        tmp_path,
    )

    await agent.run("review")


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["initialize", "new_session"])
async def test_acp_runtime_retries_timed_out_session_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    method: str,
) -> None:
    connections = [_Connection(), _Connection()]
    spawn_count = 0
    setup_cancelled = asyncio.Event()

    async def stalled_setup(**kwargs: object) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            setup_cancelled.set()

    setattr(connections[0], method, stalled_setup)

    @asynccontextmanager
    async def fake_spawn_agent_process(
        client: object,
        command: str,
        *args: str,
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

    assert setup_cancelled.is_set()
    assert spawn_count == 2


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
