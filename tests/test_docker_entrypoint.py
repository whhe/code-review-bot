"""Tests for docker/entrypoint.sh command dispatch (bootstrap is stubbed)."""

import os
import subprocess
import textwrap
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENTRYPOINT_SRC = _REPO_ROOT / "docker" / "entrypoint.sh"


def _materialize_entrypoint(tmp_path: Path) -> Path:
    """Copy entrypoint.sh with bootstrap and PATH wired for subprocess tests."""
    bootstrap = tmp_path / "bootstrap.py"
    bootstrap.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    bootstrap.chmod(0o755)

    bot = tmp_path / "code-review-bot"
    bot.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            printf 'BOT:%s\\n' "$*"
            """
        ),
        encoding="utf-8",
    )
    bot.chmod(0o755)

    sh_stub = tmp_path / "sh"
    sh_stub.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            printf 'SH:%s\\n' "$*"
            """
        ),
        encoding="utf-8",
    )
    sh_stub.chmod(0o755)

    script = tmp_path / "entrypoint.sh"
    script.write_text(
        _ENTRYPOINT_SRC.read_text(encoding="utf-8").replace(
            "python3 /usr/local/bin/bootstrap.py",
            f"python3 {bootstrap}",
        ),
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _run(script: Path, tmp_path: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *argv],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ.get('PATH', '')}"},
    )


def test_entrypoint_prepends_bot_for_bare_flags(tmp_path: Path) -> None:
    script = _materialize_entrypoint(tmp_path)
    result = _run(script, tmp_path, "--cr-id", "42")
    assert result.returncode == 0
    assert result.stdout.strip() == "BOT:--cr-id 42"


def test_entrypoint_passes_through_explicit_bot(tmp_path: Path) -> None:
    script = _materialize_entrypoint(tmp_path)
    result = _run(script, tmp_path, "code-review-bot", "--cr-id", "7")
    assert result.returncode == 0
    assert result.stdout.strip() == "BOT:--cr-id 7"


def test_entrypoint_passes_through_sh(tmp_path: Path) -> None:
    script = _materialize_entrypoint(tmp_path)
    result = _run(script, tmp_path, "sh", "-c", "echo hi")
    assert result.returncode == 0
    assert result.stdout.strip() == "SH:-c echo hi"


def test_entrypoint_defaults_when_no_args(tmp_path: Path) -> None:
    script = _materialize_entrypoint(tmp_path)
    result = _run(script, tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == "BOT:--help"
