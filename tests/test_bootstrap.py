"""Tests for docker/bootstrap.py agent credential setup."""

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "docker"))
import bootstrap  # noqa: E402


def test_acp_agent_type_defaults_to_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACP_AGENT_TYPE", raising=False)
    assert bootstrap.acp_agent_type() == "claude"


def test_acp_agent_type_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_AGENT_TYPE", "unknown")
    with pytest.raises(SystemExit):
        bootstrap.acp_agent_type()


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
