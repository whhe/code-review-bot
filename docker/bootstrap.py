#!/usr/bin/env python3
"""Bootstrap coding-agent settings on first container start (invoked from docker/entrypoint.sh)."""

import json
import os
import sys
from pathlib import Path

_DEFAULT_OPENCODE_CONTEXT_LENGTH = 1_000_000
_DEFAULT_OPENCODE_OUTPUT_TOKEN_MAX = 65_536


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def acp_agent_type() -> str:
    raw = os.environ.get("ACP_AGENT_TYPE", "claude").strip().lower()
    if raw not in {"claude", "opencode"}:
        fail(f"Docker only supports ACP_AGENT_TYPE=claude or opencode; got {raw!r}")
    return raw


def setup_cc() -> None:
    """Write ~/.claude/settings.json from env on first container start; skip if the file exists."""
    settings_path = Path.home() / ".claude" / "settings.json"
    if settings_path.is_file():
        print(f"note: skipping CC setup; {settings_path} already exists", file=sys.stderr)
        return

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    auth_token = os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
    if api_key and auth_token:
        fail("set only one of ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN, not both")
    if auth_token:
        cred_key, cred_value = "ANTHROPIC_AUTH_TOKEN", auth_token
    elif api_key:
        cred_key, cred_value = "ANTHROPIC_API_KEY", api_key
    else:
        fail("ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is required on first container start")

    model = os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-6").strip()
    env: dict[str, str] = {
        cred_key: cred_value,
        "ANTHROPIC_MODEL": model,
    }
    if base_url := os.environ.get("ANTHROPIC_BASE_URL", "").strip():
        env["ANTHROPIC_BASE_URL"] = base_url

    # Top-level "model" is required: claude-agent-acp reads settings.model directly for
    # model selection. env.ANTHROPIC_MODEL only reaches the Claude Code CLI subprocess.
    settings = {
        "model": model,
        "env": env,
        "permissions": {"defaultMode": "bypassPermissions"},
        "skipDangerousModePermissionPrompt": True,
    }
    payload = json.dumps(settings, indent=2) + "\n"

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(payload, encoding="utf-8")


def setup_opencode() -> None:
    """Write OpenCode settings from env on first container start."""
    settings_path = Path.home() / ".config" / "opencode" / "opencode.json"
    if settings_path.is_file():
        print(
            f"note: skipping OpenCode setup; {settings_path} already exists",
            file=sys.stderr,
        )
        return

    required_env("OPENCODE_UPSTREAM_ENDPOINT")
    required_env("OPENCODE_UPSTREAM_API_KEY")
    model = required_env("OPENCODE_MODEL")
    context_length = positive_int_env("OPENCODE_CONTEXT_LENGTH", _DEFAULT_OPENCODE_CONTEXT_LENGTH)
    output_token_max = positive_int_env(
        "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX",
        _DEFAULT_OPENCODE_OUTPUT_TOKEN_MAX,
    )
    if output_token_max >= context_length:
        fail("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX must be smaller than OPENCODE_CONTEXT_LENGTH")

    settings = {
        "$schema": "https://opencode.ai/config.json",
        "model": f"code-review/{model}",
        "provider": {
            "code-review": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Code Review Provider",
                "options": {
                    "baseURL": "{env:OPENCODE_UPSTREAM_ENDPOINT}",
                    "apiKey": "{env:OPENCODE_UPSTREAM_API_KEY}",
                },
                "models": {
                    model: {
                        "name": model,
                        "limit": {
                            "context": context_length,
                            "output": output_token_max,
                        },
                    }
                },
            }
        },
    }
    payload = json.dumps(settings, indent=2) + "\n"

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(payload, encoding="utf-8")
    print(f"note: wrote {settings_path}", file=sys.stderr)


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        fail(f"{name} is required on first OpenCode container start")
    return value


def positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        fail(f"{name} must be a positive integer; got {raw!r}")
    if value <= 0:
        fail(f"{name} must be a positive integer; got {raw!r}")
    return value


def main() -> None:
    agent_type = acp_agent_type()
    if agent_type == "claude":
        setup_cc()
    else:
        setup_opencode()


if __name__ == "__main__":
    main()
