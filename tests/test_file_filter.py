from code_review_bot.review.file_filter import FileFilter
from code_review_bot.skill.protocol import Finding


def _finding(file_path: str) -> Finding:
    return Finding(
        severity="low",
        description="test",
        file_path=file_path,
        line_range="1",
        reason="r",
        confidence=50,
    )


# --- is_excluded ---


def test_builtin_defaults_exclude_lock_files() -> None:
    f = FileFilter([], [])
    assert f.is_excluded("yarn.lock")
    assert f.is_excluded("package-lock.json")


def test_builtin_defaults_exclude_nested_lock_file() -> None:
    f = FileFilter([], [])
    assert f.is_excluded("subdir/yarn.lock")


def test_builtin_defaults_exclude_minified_assets() -> None:
    f = FileFilter([], [])
    assert f.is_excluded("bundle.min.js")
    assert f.is_excluded("app.min.css")
    assert f.is_excluded("app.js.map")


def test_builtin_defaults_exclude_vendor_tree() -> None:
    f = FileFilter([], [])
    assert f.is_excluded("vendor/lodash/index.js")
    assert f.is_excluded("src/vendor/lib.js")


def test_builtin_defaults_exclude_generated_tree() -> None:
    f = FileFilter([], [])
    assert f.is_excluded("generated/api_client.py")


def test_regular_source_file_not_excluded_by_default() -> None:
    f = FileFilter([], [])
    assert not f.is_excluded("src/main.py")
    assert not f.is_excluded("tests/test_app.py")


def test_custom_exclude_pattern() -> None:
    f = FileFilter(["dist/**"], [])
    assert f.is_excluded("dist/bundle.js")
    assert not f.is_excluded("src/app.js")


def test_include_restricts_to_matching_files() -> None:
    f = FileFilter([], ["src/**"])
    assert not f.is_excluded("src/app.py")
    assert f.is_excluded("tests/test_app.py")


def test_include_and_exclude_combined() -> None:
    f = FileFilter(["*.lock"], ["src/**"])
    # fails include
    assert f.is_excluded("tests/test_app.py")
    # passes include but excluded
    assert f.is_excluded("src/yarn.lock")
    # passes both checks
    assert not f.is_excluded("src/app.py")


def test_no_rules_passes_all_files() -> None:
    f = FileFilter([], [])
    assert not f.is_excluded("any/path/file.py")


# --- filter_findings ---


def test_filter_findings_removes_excluded() -> None:
    f = FileFilter([], [])
    findings = [_finding("src/app.py"), _finding("yarn.lock")]
    kept, excluded_count = f.filter_findings(findings)
    assert len(kept) == 1
    assert kept[0].file_path == "src/app.py"
    assert excluded_count == 1


def test_filter_findings_keeps_all_when_no_match() -> None:
    f = FileFilter([], [])
    findings = [_finding("src/a.py"), _finding("tests/b.py")]
    kept, excluded_count = f.filter_findings(findings)
    assert len(kept) == 2
    assert excluded_count == 0


def test_filter_findings_empty_input() -> None:
    f = FileFilter([], [])
    kept, excluded_count = f.filter_findings([])
    assert kept == []
    assert excluded_count == 0


# --- prompt_section ---


def test_prompt_section_empty_when_only_builtins() -> None:
    # FileFilter with default builtins but no user-supplied patterns produces a section
    # listing the built-in patterns (always-on).
    f = FileFilter([], [])
    section = f.prompt_section()
    assert "*.lock" in section
    assert section.startswith("## File filter")


def test_prompt_section_includes_custom_exclude() -> None:
    f = FileFilter(["dist/**"], [])
    section = f.prompt_section()
    assert "dist/**" in section


def test_prompt_section_includes_include_patterns() -> None:
    f = FileFilter([], ["src/**"])
    section = f.prompt_section()
    assert "src/**" in section
    assert "Only review files matching" in section
