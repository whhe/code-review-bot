"""ACP subprocess coding agent: launches an external process and streams its output."""

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from code_review_bot.agent.protocol import AgentRunResult

logger = logging.getLogger(__name__)

_MESSAGE_PREVIEW_CHARS = 200


class _LogLineBuffer:
    """Accumulates streaming text chunks and flushes complete lines to the logger."""

    def __init__(self, tag: str) -> None:
        self._buf: str = ""
        self._tag = tag

    def append(self, text: str) -> None:
        self._buf += text
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip()
            if line:
                logger.info("[%s] %s", self._tag, _truncate_for_log(line))

    def flush(self) -> None:
        text = self._buf.strip()
        if text:
            logger.info("[%s] %s", self._tag, _truncate_for_log(text))
        self._buf = ""


@dataclass(frozen=True)
class AcpAgentConfig:
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    model: str | None = None
    model_via_acp: bool = False
    model_config_option: str | None = None
    stream_limit: int | None = None
    verbose: bool = True


def handle_session_update(
    update: object,
    *,
    verbose: bool,
    message_buffer: _LogLineBuffer | None = None,
    thought_buffer: _LogLineBuffer | None = None,
) -> str | None:
    """Route ACP session_update events to logs; return agent message text to collect."""
    from acp.schema import AgentThoughtChunk, TextContentBlock, ToolCallStart, ToolCallUpdate

    try:
        from acp.schema import UsageUpdate as _UsageUpdateCls
    except ImportError:
        _UsageUpdateCls = None  # type: ignore[assignment]

    if _UsageUpdateCls is not None and isinstance(update, _UsageUpdateCls):
        if verbose:
            pct = f"{update.used / update.size * 100:.1f}%" if update.size else "n/a"
            logger.info("[agent context] used=%d size=%d (%s)", update.used, update.size, pct)
        return None

    if verbose:
        if isinstance(update, ToolCallStart):
            if thought_buffer is not None:
                thought_buffer.flush()
            if message_buffer is not None:
                message_buffer.flush()
            logger.info(
                "[agent tool] start id=%s kind=%s title=%s",
                update.tool_call_id,
                update.kind,
                update.title,
            )
        elif isinstance(update, ToolCallUpdate):
            raw_input = getattr(update, "raw_input", None)
            raw_output = getattr(update, "raw_output", None)
            if raw_input is not None:
                logger.info(
                    "[agent tool] params id=%s\n%s",
                    update.tool_call_id,
                    _format_raw(raw_input),
                )
            if raw_output is not None:
                logger.info(
                    "[agent tool] result id=%s status=%s\n%s",
                    update.tool_call_id,
                    update.status,
                    _format_raw(raw_output),
                )
            if raw_input is None and raw_output is None:
                logger.info(
                    "[agent tool] update id=%s status=%s title=%s",
                    update.tool_call_id,
                    update.status,
                    update.title,
                )
        elif not _update_has_text_content(update):
            logger.debug("[agent event] %s", type(update).__name__)

    content = getattr(update, "content", None)
    text = content.text if isinstance(content, TextContentBlock) else getattr(content, "text", None)
    if not isinstance(text, str):
        return None

    if isinstance(update, AgentThoughtChunk):
        if message_buffer is not None:
            message_buffer.flush()
        if thought_buffer is not None:
            thought_buffer.append(text)
        else:
            logger.info("[agent thought] %s", text)
        return None

    if verbose:
        if thought_buffer is not None:
            thought_buffer.flush()
        if message_buffer is not None:
            message_buffer.append(text)
        else:
            logger.info("[agent message] %s", _truncate_for_log(text))

    return text


def _update_has_text_content(update: object) -> bool:
    from acp.schema import AgentThoughtChunk, TextContentBlock

    if isinstance(update, AgentThoughtChunk):
        return True
    content = getattr(update, "content", None)
    if isinstance(content, TextContentBlock):
        return True
    return isinstance(getattr(content, "text", None), str)


def _truncate_for_log(text: str, limit: int = _MESSAGE_PREVIEW_CHARS) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


_RAW_PREVIEW_CHARS = 2000


def _format_raw(value: object) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, indent=2)
    except Exception:
        text = repr(value)
    if len(text) > _RAW_PREVIEW_CHARS:
        return text[:_RAW_PREVIEW_CHARS] + "..."
    return text


class AcpCodingAgent:
    """CodingAgent implementation backed by an ACP subprocess."""

    def __init__(self, config: AcpAgentConfig, cwd: str | Path) -> None:
        self.config = config
        self.cwd = Path(cwd)

    async def run_once(
        self,
        prompt: str,
        *,
        agent: str = "plan",
        system: str | None = None,
        files: list[str] | None = None,
        additional_directories: list[str] | None = None,
    ) -> AgentRunResult:
        from acp import PROTOCOL_VERSION, Client, spawn_agent_process, text_block
        from acp.schema import ClientCapabilities, Implementation

        config = self.config

        class CollectingClient(Client):
            def __init__(self) -> None:
                self._chunks: list[str] = []
                self.message_buffer = _LogLineBuffer("agent message")
                self.thought_buffer = _LogLineBuffer("agent thought")

            @property
            def collected_text(self) -> str:
                return "".join(self._chunks)

            def flush_buffers(self) -> None:
                self.thought_buffer.flush()
                self.message_buffer.flush()

            async def session_update(
                self, session_id: str, update: object, **kwargs: object
            ) -> None:
                message_text = handle_session_update(
                    update,
                    verbose=config.verbose,
                    message_buffer=self.message_buffer,
                    thought_buffer=self.thought_buffer,
                )
                if message_text is not None:
                    self._chunks.append(message_text)

            async def request_permission(
                self,
                options: list[object],
                session_id: str,
                tool_call: object,
                **kwargs: object,
            ) -> object:
                return approve_permission_response(options)

        client = CollectingClient()
        cwd = str(self.cwd)
        env = _resolve_env(config.env)
        if config.model and not config.model_via_acp and config.model_config_option is None:
            # claude-agent-acp selects the model from process.env.ANTHROPIC_MODEL (Priority 1)
            # or settings.json top-level "model" (Priority 2). new_session() extra kwargs land
            # in _meta which the server ignores for model selection.
            env = {**(env or {}), "ANTHROPIC_MODEL": config.model}
        usage_dict: dict[str, Any] = {}
        async with spawn_agent_process(
            client,
            config.command,
            *config.args,
            env=env,
            cwd=cwd,
            transport_kwargs=(
                {"limit": config.stream_limit} if config.stream_limit is not None else None
            ),
        ) as (conn, _proc):
            try:
                await conn.initialize(
                    protocol_version=PROTOCOL_VERSION,
                    client_capabilities=ClientCapabilities(),
                    client_info=Implementation(
                        name="code-review-bot",
                        title="code-review-bot",
                        version="0.1.0",
                    ),
                )
                session_kwargs: dict[str, Any] = {"cwd": cwd}
                if additional_directories:
                    session_kwargs["additional_directories"] = additional_directories
                session = await conn.new_session(**session_kwargs)
                if config.model and config.model_via_acp:
                    await conn.set_session_model(config.model, session.session_id)
                elif config.model and config.model_config_option is not None:
                    await conn.set_config_option(
                        config.model_config_option,
                        session.session_id,
                        config.model,
                    )
                combined_prompt = _combine_prompt(prompt, system, files)
                _log_acp_prompt(
                    combined_prompt,
                    verbose=config.verbose,
                    agent=agent,
                    model=config.model,
                    cwd=cwd,
                    additional_directories=additional_directories or [],
                )
                response = await conn.prompt(
                    session_id=session.session_id,
                    prompt=[text_block(combined_prompt)],
                )
                if response is not None and response.usage is not None:
                    usage_dict = _usage_to_dict(response.usage)
            except Exception:
                _log_acp_error(
                    _proc,
                    cwd=cwd,
                    model=config.model,
                    command=config.command,
                    args=config.args,
                )
                raise
            finally:
                _drain_subprocess_pipes(_proc)

        client.flush_buffers()
        _log_usage(usage_dict)
        text = client.collected_text.strip()
        parts: list[dict[str, Any]] = [{"type": "text", "text": text}] if text else []
        return AgentRunResult(text=text, parts=parts, usage=usage_dict)


def _log_acp_error(proc: object, **context: object) -> None:
    pid = getattr(proc, "pid", None)
    returncode = getattr(proc, "returncode", None)
    logger.error(
        "ACP agent subprocess failed: pid=%s returncode=%s context=%s",
        pid,
        returncode,
        context,
    )
    stderr_text = _read_subprocess_buffer(proc, "stderr")
    if stderr_text:
        logger.error("[agent stderr]\n%s", stderr_text[:4000])
    stdout_text = _read_subprocess_buffer(proc, "stdout")
    if stdout_text:
        logger.error("[agent stdout]\n%s", stdout_text[:4000])


def _read_subprocess_buffer(proc: object, attr: str) -> str | None:
    pipe = getattr(proc, attr, None)
    if pipe is None:
        return None
    buf = getattr(pipe, "_buffer", None)
    if not buf:
        return None
    try:
        return bytes(buf).decode("utf-8", errors="replace").strip() or None
    except Exception:
        return None


def _drain_subprocess_pipes(proc: object) -> None:
    """Close subprocess transports before the event loop shuts down.

    spawn_stdio_transport opens pipes as PIPE but never closes them.
    If a transport's __del__ fires after the event loop is closed,
    Python raises RuntimeError: Event loop is closed.
    """
    for attr in ("stderr", "stdout", "stdin"):
        pipe = getattr(proc, attr, None)
        if pipe is None:
            continue
        transport = getattr(pipe, "_transport", None)
        if transport is not None and hasattr(transport, "close"):
            try:
                transport.close()
            except Exception:
                logger.debug("Failed to close subprocess pipe transport %s", attr, exc_info=True)
    transport = getattr(proc, "_transport", None)
    if transport is not None and hasattr(transport, "close"):
        try:
            transport.close()
        except Exception:
            logger.debug("Failed to close subprocess transport", exc_info=True)


def approve_permission_response(options: list[object]) -> object:
    """Auto-approve tool permission requests for headless CI use.

    All tool calls are approved unconditionally; read-only enforcement relies
    on the prompt contract (IMPORTANT — read-only), not this permission layer.
    """
    from acp import RequestPermissionResponse
    from acp.schema import AllowedOutcome, DeniedOutcome

    for opt in options:
        option_id = getattr(opt, "option_id", None)
        if option_id is not None:
            return RequestPermissionResponse(
                outcome=AllowedOutcome(outcome="selected", optionId=option_id),
            )
    return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))


def _resolve_env(raw_env: dict[str, str]) -> dict[str, str] | None:
    if not raw_env:
        return None
    resolved: dict[str, str] = {}
    for key, value in raw_env.items():
        resolved[key] = os.environ.get(value[1:], "") if value.startswith("$") else value
    return resolved


def _usage_to_dict(usage: object) -> dict[str, Any]:
    return {
        "input_tokens": getattr(usage, "input_tokens", 0),
        "output_tokens": getattr(usage, "output_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
        "cached_read_tokens": getattr(usage, "cached_read_tokens", None),
        "cached_write_tokens": getattr(usage, "cached_write_tokens", None),
        "thought_tokens": getattr(usage, "thought_tokens", None),
    }


def _log_usage(usage: dict[str, Any]) -> None:
    if not usage:
        return
    parts = [
        f"input={usage.get('input_tokens', 0)}",
        f"output={usage.get('output_tokens', 0)}",
        f"total={usage.get('total_tokens', 0)}",
    ]
    if usage.get("cached_read_tokens") is not None:
        parts.append(f"cache_read={usage['cached_read_tokens']}")
    if usage.get("cached_write_tokens") is not None:
        parts.append(f"cache_write={usage['cached_write_tokens']}")
    if usage.get("thought_tokens") is not None:
        parts.append(f"thoughts={usage['thought_tokens']}")
    logger.info("[agent usage] %s", " ".join(parts))


def _combine_prompt(prompt: str, system: str | None, files: list[str] | None) -> str:
    chunks: list[str] = []
    if system:
        chunks.append(system)
    chunks.append(prompt)
    if files:
        chunks.append("Files:\n" + "\n".join(files))
    return "\n\n".join(chunks)


def _log_acp_prompt(
    combined_prompt: str,
    *,
    verbose: bool,
    agent: str,
    model: str | None,
    cwd: str,
    additional_directories: list[str],
) -> None:
    if not verbose:
        return
    extra_dirs = ", ".join(additional_directories) if additional_directories else "(none)"
    logger.info(
        "[agent prompt] agent=%s model=%s cwd=%s additional_directories=%s chars=%d\n%s",
        agent,
        model or "(default)",
        cwd,
        extra_dirs,
        len(combined_prompt),
        combined_prompt,
    )
