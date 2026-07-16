"""Tests for docker/bootstrap.py agent credential setup."""

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "docker"))
import bootstrap  # noqa: E402


def _set_opencode_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENCODE_UPSTREAM_ENDPOINT", "https://api.example.test/v1")
    monkeypatch.setenv("OPENCODE_UPSTREAM_API_KEY", "test-secret")
    monkeypatch.setenv("OPENCODE_MODEL", "qwen3.7-plus")
    monkeypatch.delenv("OPENCODE_CONTEXT_LENGTH", raising=False)
    monkeypatch.delenv("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", raising=False)


def test_acp_agent_type_defaults_to_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACP_AGENT_TYPE", raising=False)
    assert bootstrap.acp_agent_type() == "claude"


def test_acp_agent_type_accepts_opencode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_AGENT_TYPE", " OpenCode ")
    assert bootstrap.acp_agent_type() == "opencode"


def test_acp_agent_type_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_AGENT_TYPE", "unknown")
    with pytest.raises(SystemExit):
        bootstrap.acp_agent_type()


@pytest.mark.parametrize(
    ("agent_type", "expected"),
    [("claude", ["claude"]), ("opencode", ["opencode"])],
)
def test_main_dispatches_agent_setup(
    monkeypatch: pytest.MonkeyPatch,
    agent_type: str,
    expected: list[str],
) -> None:
    calls: list[str] = []
    monkeypatch.setenv("ACP_AGENT_TYPE", agent_type)
    monkeypatch.setattr(bootstrap, "setup_cc", lambda: calls.append("claude"))
    monkeypatch.setattr(
        bootstrap,
        "setup_opencode",
        lambda: calls.append("opencode"),
        raising=False,
    )

    bootstrap.main()

    assert calls == expected


def test_setup_cc_writes_settings_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-6")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    bootstrap.setup_cc()

    settings_path = home / ".claude" / "settings.json"
    assert settings_path.is_file()
    payload = settings_path.read_text(encoding="utf-8")
    assert json.loads(payload) == {
        "model": "claude-opus-4-6",
        "env": {
            "ANTHROPIC_API_KEY": "test-key",
            "ANTHROPIC_MODEL": "claude-opus-4-6",
        },
        "permissions": {"defaultMode": "bypassPermissions"},
        "skipDangerousModePermissionPrompt": True,
    }


def test_setup_cc_writes_model_and_base_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "test-token")
    monkeypatch.setenv("ANTHROPIC_MODEL", "qwen3.7-max")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://proxy.example/v1")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    bootstrap.setup_cc()

    settings = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["model"] == "qwen3.7-max"
    assert settings["env"]["ANTHROPIC_MODEL"] == "qwen3.7-max"
    assert settings["env"]["ANTHROPIC_BASE_URL"] == "https://proxy.example/v1"


@pytest.mark.parametrize(
    "missing",
    ["OPENCODE_UPSTREAM_ENDPOINT", "OPENCODE_UPSTREAM_API_KEY", "OPENCODE_MODEL"],
)
def test_setup_opencode_requires_runtime_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    missing: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _set_opencode_env(monkeypatch)
    monkeypatch.delenv(missing)

    with pytest.raises(SystemExit):
        bootstrap.setup_opencode()

    captured = capsys.readouterr()
    assert missing in captured.err
    assert "test-secret" not in captured.err


def test_setup_opencode_rejects_legacy_upstream_api_key(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _set_opencode_env(monkeypatch)
    monkeypatch.delenv("OPENCODE_UPSTREAM_API_KEY")
    monkeypatch.setenv("UPSTREAM_API_KEY", "legacy-secret")

    with pytest.raises(SystemExit):
        bootstrap.setup_opencode()

    captured = capsys.readouterr()
    assert "OPENCODE_UPSTREAM_API_KEY" in captured.err
    assert "legacy-secret" not in captured.err


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OPENCODE_CONTEXT_LENGTH", "not-a-number"),
        ("OPENCODE_CONTEXT_LENGTH", "0"),
        ("OPENCODE_CONTEXT_LENGTH", "-1"),
        ("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", "not-a-number"),
        ("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", "0"),
        ("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", "-1"),
    ],
)
def test_setup_opencode_rejects_invalid_token_limits(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _set_opencode_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(SystemExit):
        bootstrap.setup_opencode()

    assert name in capsys.readouterr().err


@pytest.mark.parametrize(("context", "output"), [("32769", "32769"), ("32768", "32769")])
def test_setup_opencode_requires_output_smaller_than_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    context: str,
    output: str,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _set_opencode_env(monkeypatch)
    monkeypatch.setenv("OPENCODE_CONTEXT_LENGTH", context)
    monkeypatch.setenv("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", output)

    with pytest.raises(SystemExit):
        bootstrap.setup_opencode()

    captured = capsys.readouterr()
    assert "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX" in captured.err
    assert "smaller than OPENCODE_CONTEXT_LENGTH" in captured.err


def test_setup_opencode_writes_config_without_secret_or_permission_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    _set_opencode_env(monkeypatch)

    bootstrap.setup_opencode()

    settings_path = tmp_path / ".config" / "opencode" / "opencode.json"
    payload = settings_path.read_text(encoding="utf-8")
    assert json.loads(payload) == {
        "$schema": "https://opencode.ai/config.json",
        "model": "code-review/qwen3.7-plus",
        "provider": {
            "code-review": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Code Review Provider",
                "options": {
                    "baseURL": "{env:OPENCODE_UPSTREAM_ENDPOINT}",
                    "apiKey": "{env:OPENCODE_UPSTREAM_API_KEY}",
                },
                "models": {
                    "qwen3.7-plus": {
                        "name": "qwen3.7-plus",
                        "limit": {
                            "context": 1_000_000,
                            "output": 65_536,
                        },
                    }
                },
            }
        },
    }
    captured = capsys.readouterr()
    assert f"note: wrote {settings_path}" in captured.err
    assert "test-secret" not in payload
    assert "test-secret" not in captured.err
    assert "permission" not in json.loads(payload)


def test_setup_opencode_preserves_existing_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    settings_path = tmp_path / ".config" / "opencode" / "opencode.json"
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text('{"sentinel": true}\n', encoding="utf-8")

    bootstrap.setup_opencode()

    assert settings_path.read_text(encoding="utf-8") == '{"sentinel": true}\n'
    assert f"note: skipping OpenCode setup; {settings_path} already exists" in (
        capsys.readouterr().err
    )
