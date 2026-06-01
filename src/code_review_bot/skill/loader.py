from pathlib import Path
from urllib.parse import urlparse

from code_review_bot.paths import project_root
from code_review_bot.skill.filesystem import FilesystemMarkdownSkill, RemoteUrlSkill


def load_skill(path: str) -> FilesystemMarkdownSkill | RemoteUrlSkill:
    """Return a skill object for the given local path or remote URL.

    - Local paths (absolute, relative to CODE_REVIEW_BOT_ROOT, or ~/…) are
      resolved to a FilesystemMarkdownSkill backed by a directory on disk.
    - http/https URLs are wrapped in a RemoteUrlSkill; the coding agent fetches
      the skill content on demand — no local materialisation is performed.
    """
    raw = (path or "").strip()
    if not raw:
        raise ValueError(
            "REVIEW_SKILL is empty: set it to a local directory path or an https URL "
            "pointing at a skill containing SKILL.md"
        )
    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https"):
        return RemoteUrlSkill(raw)
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = project_root() / candidate
    resolved = candidate.resolve()
    if resolved.name.lower() == "skill.md":
        resolved = resolved.parent
    return FilesystemMarkdownSkill(resolved, resolved.name)
