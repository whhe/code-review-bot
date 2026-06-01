"""Tests for docker/bootstrap.py agent credential setup."""

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
