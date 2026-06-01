#!/usr/bin/env python3
"""Bootstrap coding-agent settings on first container start, then exec the bot command."""

import json
import os
import sys
from pathlib import Path


def fail(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def acp_agent_type() -> str:
    raw = os.environ.get("ACP_AGENT_TYPE", "claude").strip().lower()
    if raw != "claude":
        fail(f"Docker only supports ACP_AGENT_TYPE=claude; got {raw!r}")
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

    env: dict[str, str] = {
        cred_key: cred_value,
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "claude-opus-4-6").strip(),
    }
    if base_url := os.environ.get("ANTHROPIC_BASE_URL", "").strip():
        env["ANTHROPIC_BASE_URL"] = base_url

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "env": env,
                "permissions": {"defaultMode": "bypassPermissions"},
                "skipDangerousModePermissionPrompt": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    acp_agent_type()
    setup_cc()
    if len(sys.argv) < 2:
        fail("no command specified")
    os.execvp(sys.argv[1], sys.argv[1:])
