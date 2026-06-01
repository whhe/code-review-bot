"""Built-in ACP agent type registry and launcher resolution logic."""

from typing import Literal

BuiltinAcpAgentType = Literal["claude", "codex"]

BUILTIN_ACP_AGENT_TYPES: frozenset[str] = frozenset({"claude", "codex"})

ACP_PRESETS: dict[BuiltinAcpAgentType, tuple[str, list[str]]] = {
    "claude": ("npx", ["-y", "@zed-industries/claude-agent-acp"]),
    "codex": ("npx", ["-y", "@zed-industries/codex-acp"]),
}


def is_builtin_acp_agent_type(agent_type: str) -> bool:
    return agent_type.strip().lower() in BUILTIN_ACP_AGENT_TYPES


def acp_preset(agent_type: str) -> tuple[str, list[str]]:
    key = agent_type.strip().lower()
    if key not in BUILTIN_ACP_AGENT_TYPES:
        msg = f"not a built-in ACP_AGENT_TYPE: {agent_type!r}"
        raise ValueError(msg)
    return ACP_PRESETS[key]  # type: ignore[index]


def resolve_acp_launcher(
    agent_type: str,
    *,
    command: str | None = None,
    args: list[str] | None = None,
) -> tuple[str, list[str]]:
    name = agent_type.strip().lower()
    if not name:
        msg = "ACP_AGENT_TYPE must be a non-empty string"
        raise ValueError(msg)

    if is_builtin_acp_agent_type(name):
        if command is not None:
            if not command.strip():
                msg = "ACP_COMMAND must be a non-empty string when set"
                raise ValueError(msg)
            return command.strip(), list(args or [])
        if args is not None:
            msg = "ACP_ARGS requires ACP_COMMAND when overriding a built-in preset"
            raise ValueError(msg)
        return acp_preset(name)

    if command is None or not command.strip():
        msg = (
            f"ACP_AGENT_TYPE={agent_type!r} is not built-in "
            f"({', '.join(sorted(BUILTIN_ACP_AGENT_TYPES))}); "
            "set ACP_COMMAND and ACP_ARGS"
        )
        raise ValueError(msg)
    if args is None:
        msg = f"ACP_ARGS is required when ACP_AGENT_TYPE={agent_type!r} is not built-in"
        raise ValueError(msg)

    return command.strip(), list(args)
