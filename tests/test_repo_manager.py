from code_review_bot.repo.manager import RepoManager, _scrub_clone_url


def test_scrub_clone_url_redacts_credentials() -> None:
    url = "https://oauth2:secret-token@gitlab.test/group/repo.git"
    assert _scrub_clone_url(url) == "https://<credentials>@gitlab.test/group/repo.git"


def test_scrub_clone_url_leaves_url_without_credentials_unchanged() -> None:
    url = "https://gitlab.test/group/repo.git"
    assert _scrub_clone_url(url) == url


def test_scrub_clone_url_leaves_non_url_argument_unchanged() -> None:
    assert _scrub_clone_url("/tmp/clone/dest") == "/tmp/clone/dest"
    assert _scrub_clone_url("--depth") == "--depth"


def test_inject_token_injects_credentials() -> None:
    manager = RepoManager(clone_url="https://github.com/owner/repo.git", token="mytoken")
    assert manager._inject_token("https://github.com/fork/repo.git") == (
        "https://oauth2:mytoken@github.com/fork/repo.git"
    )


def test_inject_token_strips_existing_credentials() -> None:
    manager = RepoManager(clone_url="https://github.com/owner/repo.git", token="newtoken")
    assert manager._inject_token("https://user:old@github.com/fork/repo.git") == (
        "https://oauth2:newtoken@github.com/fork/repo.git"
    )


def test_inject_token_returns_url_unchanged_when_no_token() -> None:
    manager = RepoManager(clone_url="https://github.com/owner/repo.git")
    url = "https://github.com/fork/repo.git"
    assert manager._inject_token(url) == url
