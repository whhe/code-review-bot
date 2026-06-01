from code_review_bot.paths import project_root


def test_project_root_returns_directory_with_pyproject_toml() -> None:
    root = project_root()
    assert root.is_dir()
    assert (root / "pyproject.toml").is_file()
