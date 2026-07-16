import pytest

from code_review_bot.agent.presets import (
    is_builtin_acp_agent_type,
    resolve_acp_launcher,
)


def test_is_builtin() -> None:
    assert is_builtin_acp_agent_type("claude")
    assert not is_builtin_acp_agent_type("my-bridge")


def test_builtin_uses_preset() -> None:
    command, args = resolve_acp_launcher("codex")
    assert command == "npx"
    assert args == ["-y", "@zed-industries/codex-acp"]


def test_opencode_is_builtin_and_uses_native_acp() -> None:
    assert is_builtin_acp_agent_type("opencode")

    command, args = resolve_acp_launcher("opencode")
    assert command == "opencode"
    assert args == ["acp"]


def test_builtin_optional_override() -> None:
    command, args = resolve_acp_launcher("claude", command="custom", args=["x"])
    assert command == "custom"
    assert args == ["x"]


def test_custom_requires_command_and_args() -> None:
    with pytest.raises(ValueError, match="not built-in"):
        resolve_acp_launcher("my-bridge")

    with pytest.raises(ValueError, match="ACP_ARGS is required"):
        resolve_acp_launcher("my-bridge", command="npx")


def test_custom_with_command_and_args() -> None:
    command, args = resolve_acp_launcher(
        "my-bridge",
        command="npx",
        args=["-y", "pkg"],
    )
    assert command == "npx"
    assert args == ["-y", "pkg"]
