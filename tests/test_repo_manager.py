import os
import subprocess
from pathlib import Path

import pytest

from code_review_bot.repo.manager import RepoManager, _scrub_clone_url


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def test_scrub_clone_url_redacts_credentials() -> None:
    url = "https://oauth2:secret-token@gitlab.test/group/repo.git"
    assert _scrub_clone_url(url) == "https://<credentials>@gitlab.test/group/repo.git"


def test_scrub_clone_url_leaves_url_without_credentials_unchanged() -> None:
    url = "https://gitlab.test/group/repo.git"
    assert _scrub_clone_url(url) == url


def test_scrub_clone_url_leaves_non_url_argument_unchanged() -> None:
    assert _scrub_clone_url("/tmp/clone/dest") == "/tmp/clone/dest"
    assert _scrub_clone_url("--depth") == "--depth"


def test_repo_manager_never_stores_credentials_in_clone_url() -> None:
    manager = RepoManager(
        clone_url="https://oauth2:secret@gitlab.test/group/repo.git",
        token="secret",
    )

    assert manager.clone_url == "https://gitlab.test/group/repo.git"


def test_clone_uses_ephemeral_credential_helper_with_clean_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = RepoManager(clone_url="https://gitlab.test/group/repo.git", token="secret")
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        stdout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(manager, "_run", fake_run)
    destination = tmp_path / "source"
    manager._clone_ref("https://gitlab.test/group/repo.git", "feature", destination)

    assert len(commands) == 1
    command = commands[0]
    assert command[:4] == ["git", "-c", "credential.helper=", "-c"]
    assert command[4].startswith("credential.helper=")
    assert command[5:] == [
        "clone",
        "--no-checkout",
        "--branch",
        "feature",
        "--single-branch",
        "https://gitlab.test/group/repo.git",
        str(destination),
    ]
    assert "secret" not in repr(command)


def test_fetch_uses_ephemeral_credential_helper_with_clean_url(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = RepoManager(clone_url="https://gitlab.test/group/repo.git", token="secret")
    commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        *,
        stdout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(manager, "_run", fake_run)
    manager._fetch_ref(
        tmp_path,
        "https://gitlab.test/group/repo.git",
        "main",
        "refs/remotes/origin/main",
    )

    assert len(commands) == 1
    command = commands[0]
    assert command[:4] == ["git", "-c", "credential.helper=", "-c"]
    assert command[4].startswith("credential.helper=")
    assert command[5:] == [
        "-C",
        str(tmp_path),
        "fetch",
        "https://gitlab.test/group/repo.git",
        "main:refs/remotes/origin/main",
    ]
    assert "secret" not in repr(command)


def test_git_token_is_only_exposed_to_git_subprocess_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = RepoManager(clone_url="https://gitlab.test/group/repo.git", token="secret")
    captured_env: dict[str, str] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured_env.update(kwargs["env"])  # type: ignore[arg-type]
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    manager._run(["git", "status"])

    assert captured_env["CODE_REVIEW_BOT_GIT_TOKEN"] == "secret"


def test_git_command_ignores_preconfigured_credential_helpers(tmp_path: Path) -> None:
    manager = RepoManager(clone_url="https://gitlab.test/group/repo.git", token="secret")
    global_config = tmp_path / "gitconfig"
    marker = tmp_path / "legacy-helper-called"
    legacy_helper = (
        '!f() { printf called > "$LEGACY_HELPER_MARKER"; '
        'printf "%s\\n" "username=legacy" "password=legacy"; }; f'
    )
    subprocess.run(
        [
            "git",
            "config",
            "--file",
            str(global_config),
            "credential.helper",
            legacy_helper,
        ],
        check=True,
    )

    result = subprocess.run(
        manager._git_command("credential", "fill"),
        input="protocol=https\nhost=gitlab.test\n\n",
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "CODE_REVIEW_BOT_GIT_TOKEN": "secret",
            "GIT_CONFIG_GLOBAL": str(global_config),
            "GIT_CONFIG_NOSYSTEM": "1",
            "LEGACY_HELPER_MARKER": str(marker),
        },
    )

    assert not marker.exists()
    assert "username=oauth2" in result.stdout
    assert "password=secret" in result.stdout


def test_review_workspace_checks_out_target_and_preserves_source_ref(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "test@example.com")
    _git(remote, "config", "user.name", "Test User")
    (remote / "AGENTS.md").write_text("target instructions\n")
    (remote / "app.py").write_text("print('target')\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "target")
    _git(remote, "switch", "-qc", "feature")
    (remote / "AGENTS.md").write_text("source instructions\n")
    (remote / "app.py").write_text("print('source')\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "source")

    manager = RepoManager(clone_url=str(remote), review_base_dir=tmp_path / "reviews")
    root = manager._make_review_workspace_sync("feature", "main")
    workspace = root / "source"
    try:
        assert (workspace / "AGENTS.md").read_text() == "target instructions\n"
        assert (workspace / "app.py").read_text() == "print('target')\n"
        assert (
            _git(workspace, "rev-parse", "HEAD").strip()
            == _git(workspace, "rev-parse", "refs/code-review/target").strip()
        )
        assert (
            _git(workspace, "show", "refs/code-review/source:AGENTS.md") == "source instructions\n"
        )
        assert "print('source')" in _git(
            workspace,
            "diff",
            "refs/code-review/target...refs/code-review/source",
            "--",
            "app.py",
        )
        assert _git(workspace, "status", "--porcelain") == ""
    finally:
        manager.cleanup_review_workspace(root)


def test_review_workspace_pins_platform_commits_when_branches_advance(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "test@example.com")
    _git(remote, "config", "user.name", "Test User")
    (remote / "AGENTS.md").write_text("review target instructions\n")
    (remote / "app.py").write_text("print('target')\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "review target")
    target_sha = _git(remote, "rev-parse", "HEAD").strip()

    _git(remote, "switch", "-qc", "feature")
    (remote / "AGENTS.md").write_text("review source instructions\n")
    (remote / "app.py").write_text("print('review source')\n")
    _git(remote, "commit", "-qam", "review source")
    source_sha = _git(remote, "rev-parse", "HEAD").strip()
    (remote / "AGENTS.md").write_text("later source instructions\n")
    (remote / "app.py").write_text("print('later source')\n")
    _git(remote, "commit", "-qam", "advance source")

    _git(remote, "switch", "-q", "main")
    (remote / "AGENTS.md").write_text("later target instructions\n")
    _git(remote, "commit", "-qam", "advance target")

    manager = RepoManager(clone_url=str(remote), review_base_dir=tmp_path / "reviews")
    root = manager._make_review_workspace_sync(
        "feature",
        "main",
        source_sha=source_sha,
        target_sha=target_sha,
    )
    workspace = root / "source"
    try:
        assert _git(workspace, "rev-parse", "refs/code-review/source").strip() == source_sha
        assert _git(workspace, "rev-parse", "refs/code-review/target").strip() == target_sha
        assert (workspace / "AGENTS.md").read_text() == "review target instructions\n"
        assert (
            _git(workspace, "show", "refs/code-review/source:AGENTS.md")
            == "review source instructions\n"
        )
    finally:
        manager.cleanup_review_workspace(root)


def test_review_workspace_rejects_unavailable_platform_commit(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "test@example.com")
    _git(remote, "config", "user.name", "Test User")
    (remote / "app.py").write_text("print('target')\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "target")
    _git(remote, "switch", "-qc", "feature")
    (remote / "app.py").write_text("print('source')\n")
    _git(remote, "commit", "-qam", "source")

    reviews = tmp_path / "reviews"
    manager = RepoManager(clone_url=str(remote), review_base_dir=reviews)

    with pytest.raises(subprocess.CalledProcessError):
        manager._make_review_workspace_sync(
            "feature",
            "main",
            source_sha="f" * 40,
        )

    assert list(reviews.iterdir()) == []


def test_review_workspace_deepens_shallow_history_for_three_dot_diff(tmp_path: Path) -> None:
    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "main")
    _git(remote, "config", "user.email", "test@example.com")
    _git(remote, "config", "user.name", "Test User")
    (remote / "app.py").write_text("print('base')\n")
    _git(remote, "add", ".")
    _git(remote, "commit", "-qm", "base")
    target_sha = _git(remote, "rev-parse", "HEAD").strip()
    _git(remote, "switch", "-qc", "feature")
    (remote / "app.py").write_text("print('source')\n")
    _git(remote, "commit", "-qam", "source")
    source_sha = _git(remote, "rev-parse", "HEAD").strip()

    manager = RepoManager(
        clone_url=f"file://{remote}",
        review_base_dir=tmp_path / "reviews",
        clone_depth=1,
    )
    root = manager._make_review_workspace_sync(
        "feature",
        "main",
        source_sha=source_sha,
        target_sha=target_sha,
    )
    workspace = root / "source"
    try:
        diff = _git(
            workspace,
            "diff",
            "refs/code-review/target...refs/code-review/source",
            "--",
            "app.py",
        )
        assert "print('source')" in diff
    finally:
        manager.cleanup_review_workspace(root)
