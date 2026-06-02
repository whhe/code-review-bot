import logging
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from code_review_bot.paths import project_root
from code_review_bot.skill.filesystem import (
    FilesystemMarkdownSkill,
    NativeKnowledgeSkill,
    RemoteUrlSkill,
)

logger = logging.getLogger(__name__)

_URL_CHECK_TIMEOUT = 5  # seconds


def load_skill(path: str) -> FilesystemMarkdownSkill | NativeKnowledgeSkill | RemoteUrlSkill:
    """Return a skill object for the given local path or remote URL.

    - Empty path: returns NativeKnowledgeSkill; the agent reviews using its own knowledge.
    - Local paths: resolved to FilesystemMarkdownSkill. If the directory or SKILL.md is
      missing, logs a warning and falls back to NativeKnowledgeSkill.
    - http/https URLs: wrapped in RemoteUrlSkill after a reachability check.
      If the URL is not accessible, logs a warning and falls back to NativeKnowledgeSkill.
    """
    raw = (path or "").strip()
    if not raw:
        return NativeKnowledgeSkill()

    parsed = urlparse(raw)
    if parsed.scheme in ("http", "https"):
        if not _url_reachable(raw):
            logger.warning(
                "REVIEW_SKILL URL not accessible: %s — falling back to native knowledge", raw
            )
            return NativeKnowledgeSkill()
        return RemoteUrlSkill(raw)

    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = project_root() / candidate
    resolved = candidate.resolve()
    if resolved.name.lower() == "skill.md":
        resolved = resolved.parent

    if not resolved.is_dir():
        logger.warning(
            "REVIEW_SKILL directory not found: %s — falling back to native knowledge", raw
        )
        return NativeKnowledgeSkill()

    try:
        return FilesystemMarkdownSkill(resolved, resolved.name)
    except ValueError as exc:
        logger.warning(
            "REVIEW_SKILL not usable: %s — falling back to native knowledge (%s)", raw, exc
        )
        return NativeKnowledgeSkill()


def _url_reachable(url: str) -> bool:
    # Makes a blocking network call. load_skill is synchronous and safe to call
    # from a CLI context; if ever called from an async path, wrap in run_in_executor.
    try:
        req = Request(url, method="HEAD")
        with urlopen(req, timeout=_URL_CHECK_TIMEOUT) as resp:
            return resp.status < 400
    except Exception:
        return False
