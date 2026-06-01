import fnmatch

from code_review_bot.skill.protocol import Finding

# Always-on exclude patterns. Users can add extra patterns via REVIEW_EXCLUDE;
# these built-in patterns cannot be removed through configuration.
_BUILTIN_EXCLUDE: tuple[str, ...] = (
    "*.lock",
    "*-lock.json",
    "*.min.js",
    "*.min.css",
    "*.map",
    "vendor/**",
    "**/vendor/**",
    "generated/**",
    "**/generated/**",
)


def _match_parts(pattern_parts: list[str], path_parts: list[str]) -> bool:
    """Recursive multi-level glob matcher. ** matches zero or more path components."""
    if not pattern_parts:
        return not path_parts

    head = pattern_parts[0]
    rest = pattern_parts[1:]

    if head == "**":
        # ** can consume zero or more path components.
        for skip in range(len(path_parts) + 1):
            if _match_parts(rest, path_parts[skip:]):
                return True
        return False

    if not path_parts:
        return False

    return fnmatch.fnmatch(path_parts[0], head) and _match_parts(rest, path_parts[1:])


def _matches(pattern: str, file_path: str) -> bool:
    """Return True if file_path matches the glob pattern.

    Patterns without a path separator are matched against the basename only,
    so "*.lock" matches "subdir/yarn.lock". Patterns with ** match across
    directory boundaries: "**/vendor/**" matches both "vendor/x" and "a/vendor/x".
    """
    path = file_path.replace("\\", "/").strip("/")

    if "/" not in pattern and "**" not in pattern:
        # Basename-only pattern: match against the rightmost path component.
        return fnmatch.fnmatch(path.rsplit("/", 1)[-1], pattern)

    pattern_parts = pattern.strip("/").split("/")
    path_parts = path.split("/")
    return _match_parts(pattern_parts, path_parts)


class FileFilter:
    """Filter review findings by file path using glob-style include/exclude rules.

    Exclude logic (evaluated in order):
    1. If include patterns are set, the file must match at least one — otherwise excluded.
    2. If the file matches any exclude pattern (built-in + user-supplied) — excluded.
    """

    def __init__(self, exclude: list[str], include: list[str]) -> None:
        self._exclude: tuple[str, ...] = _BUILTIN_EXCLUDE + tuple(exclude)
        self._include: tuple[str, ...] = tuple(include)

    def is_excluded(self, file_path: str) -> bool:
        if self._include and not any(_matches(p, file_path) for p in self._include):
            return True
        return any(_matches(p, file_path) for p in self._exclude)

    def filter_findings(self, findings: list[Finding]) -> tuple[list[Finding], int]:
        """Return (kept, excluded_count)."""
        kept: list[Finding] = []
        excluded = 0
        for f in findings:
            if self.is_excluded(f.file_path):
                excluded += 1
            else:
                kept.append(f)
        return kept, excluded

    def prompt_section(self) -> str:
        """Return a prompt section describing the active filter rules, or '' if none."""
        lines: list[str] = []
        if self._exclude:
            patterns = ", ".join(f"`{p}`" for p in self._exclude)
            lines.append(f"Exclude these file patterns from your review: {patterns}")
        if self._include:
            patterns = ", ".join(f"`{p}`" for p in self._include)
            lines.append(f"Only review files matching: {patterns}")
        if not lines:
            return ""
        return "## File filter\n" + "\n".join(lines) + "\n\n"
