import pytest


def test_settings_output_language_defaults_to_english(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OUTPUT_LANGUAGE", raising=False)
    from code_review_bot.config import Settings

    settings = Settings(_env_file=None)
    assert settings.output_language == "english"


def test_settings_output_language_reads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTPUT_LANGUAGE", "chinese")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.output_language == "chinese"


def test_settings_platform_type_defaults_to_gitlab(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GIT_PLATFORM_TYPE", raising=False)
    from code_review_bot.config import Settings

    settings = Settings(_env_file=None)
    assert settings.git_platform_type == "gitlab"


def test_settings_platform_type_rejects_unknown_value() -> None:
    from pydantic import ValidationError

    from code_review_bot.config import Settings

    with pytest.raises((ValidationError, ValueError)):
        Settings(
            git_repo_url="https://gitlab.test/group/project.git",
            git_repo_token="tok",
            git_platform_type="bitbucket",
        )


def test_settings_platform_url_derived_from_repo_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_PLATFORM_URL", raising=False)
    monkeypatch.setenv("GIT_REPO_URL", "https://gitlab.test/group/project.git")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.git_platform_url == "https://gitlab.test"


def test_settings_platform_url_derived_keeps_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_PLATFORM_URL", raising=False)
    monkeypatch.setenv("GIT_REPO_URL", "https://gitlab.test:8443/group/project.git")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.git_platform_url == "https://gitlab.test:8443"


def test_settings_platform_url_override_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_REPO_URL", "https://example.com/gitlab/group/project.git")
    monkeypatch.setenv("GIT_PLATFORM_URL", "https://example.com/gitlab/")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.git_platform_url == "https://example.com/gitlab"


def test_settings_project_path_parsed_from_repo_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_PLATFORM_URL", raising=False)
    monkeypatch.setenv("GIT_REPO_URL", "https://gitlab.test/group/subgroup/project.git")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.git_project_path == "group/subgroup/project"


def test_settings_project_path_handles_missing_git_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GIT_PLATFORM_URL", raising=False)
    monkeypatch.setenv("GIT_REPO_URL", "https://gitlab.test/group/project")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.git_project_path == "group/project"


def test_settings_project_path_respects_platform_url_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_REPO_URL", "https://example.com/gitlab/group/project.git")
    monkeypatch.setenv("GIT_PLATFORM_URL", "https://example.com/gitlab")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.git_project_path == "group/project"


def test_settings_acp_agent_type_defaults_to_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACP_AGENT_TYPE", raising=False)
    monkeypatch.delenv("ACP_COMMAND", raising=False)
    monkeypatch.delenv("ACP_ARGS", raising=False)
    from code_review_bot.config import Settings

    settings = Settings(_env_file=None)
    assert settings.acp_agent_type == "claude"
    assert settings.resolved_acp_command == "npx"
    assert settings.resolved_acp_args == ["-y", "@zed-industries/claude-agent-acp"]


def test_settings_acp_agent_type_codex_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_AGENT_TYPE", "codex")
    monkeypatch.delenv("ACP_COMMAND", raising=False)
    monkeypatch.delenv("ACP_ARGS", raising=False)
    from code_review_bot.config import Settings

    settings = Settings(_env_file=None)
    assert settings.acp_agent_type == "codex"
    assert settings.resolved_acp_args == ["-y", "@zed-industries/codex-acp"]


def test_settings_acp_agent_type_opencode_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_AGENT_TYPE", "opencode")
    monkeypatch.delenv("ACP_COMMAND", raising=False)
    monkeypatch.delenv("ACP_ARGS", raising=False)
    from code_review_bot.config import Settings

    settings = Settings(_env_file=None)
    assert settings.acp_agent_type == "opencode"
    assert settings.resolved_acp_command == "opencode"
    assert settings.resolved_acp_args == ["acp"]


def test_settings_acp_command_and_args_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_COMMAND", "custom-cmd")
    monkeypatch.setenv("ACP_ARGS", '["--flag", "pkg"]')
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.resolved_acp_command == "custom-cmd"
    assert settings.resolved_acp_args == ["--flag", "pkg"]


def test_settings_custom_acp_agent_type_requires_launcher() -> None:
    from pydantic import ValidationError

    from code_review_bot.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            git_repo_url="https://gitlab.test/group/project.git",
            git_repo_token="tok",
            acp_agent_type="my-bridge",
            _env_file=None,
        )


def test_settings_custom_acp_agent_type_with_launcher() -> None:
    from code_review_bot.config import Settings

    settings = Settings(
        git_repo_url="https://gitlab.test/group/project.git",
        git_repo_token="tok",
        acp_agent_type="my-bridge",
        acp_command="npx",
        acp_args=["-y", "pkg"],
    )
    assert settings.acp_agent_type == "my-bridge"
    assert settings.resolved_acp_command == "npx"
    assert settings.resolved_acp_args == ["-y", "pkg"]


def test_settings_acp_model_blank_string_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACP_MODEL", "   ")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.acp_model is None


def test_settings_log_dir_derives_fixed_subdirs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_DIR", "var/logs")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.review_session_log_dir == "var/logs/sessions"
    assert settings.debug_review_output_dir == "var/logs/debug-reports"


def test_settings_log_dir_default_is_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_DIR", raising=False)
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.log_dir == "logs"
    assert settings.review_session_log_dir == "logs/sessions"
    assert settings.debug_review_output_dir == "logs/debug-reports"


def test_settings_empty_log_dir_disables_file_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_DIR", "")
    from code_review_bot.config import Settings

    settings = Settings()
    assert settings.review_session_log_dir == ""
    assert settings.debug_review_output_dir == ""


def test_settings_auto_approve_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTO_APPROVE_ON_CLEAN_REVIEW", raising=False)
    from code_review_bot.config import Settings

    settings = Settings(_env_file=None)
    assert settings.auto_approve_on_clean_review is False


def test_settings_auto_approve_reads_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTO_APPROVE_ON_CLEAN_REVIEW", "true")
    from code_review_bot.config import Settings

    settings = Settings(_env_file=None)
    assert settings.auto_approve_on_clean_review is True
