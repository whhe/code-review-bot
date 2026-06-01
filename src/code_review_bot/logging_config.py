"""Logging setup: structured console output and per-review session log files."""

import logging
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from code_review_bot.paths import project_root

_BOT_LOGGER_NAME = "code_review_bot"


def configure_logging(level: int | str = logging.INFO) -> None:
    if isinstance(level, str):
        level_num = getattr(logging, level.upper(), None)
        level = level_num if isinstance(level_num, int) else logging.INFO
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        root.addHandler(handler)
    root.setLevel(level)


@dataclass
class ReviewLogSession:
    handler: logging.Handler
    prev_bot_level: int


def _open_session_log(log_dir: Path, filename: str) -> ReviewLogSession:
    log_dir.mkdir(parents=True, exist_ok=True)
    bot_log = logging.getLogger(_BOT_LOGGER_NAME)
    prev_level = bot_log.level
    if prev_level == logging.NOTSET or prev_level > logging.DEBUG:
        bot_log.setLevel(logging.DEBUG)
    handler = logging.FileHandler(log_dir / filename, encoding="utf-8", mode="a")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
    bot_log.addHandler(handler)
    return ReviewLogSession(handler=handler, prev_bot_level=prev_level)


def _close_session_log(session: ReviewLogSession, footer: str) -> None:
    bot_log = logging.getLogger(_BOT_LOGGER_NAME)
    bot_log.info(footer)
    bot_log.removeHandler(session.handler)
    session.handler.close()
    bot_log.setLevel(session.prev_bot_level)


def attach_review_session_logging(
    project_ref: str,
    cr_id: str,
    *,
    relative_log_dir: str,
) -> ReviewLogSession | None:
    rel = relative_log_dir.strip()
    if not rel:
        return None
    log_dir = (project_root() / rel).resolve()
    # Sanitize project_ref for use in a filename (numeric IDs and paths may contain slashes)
    safe_ref = project_ref.replace("/", "-").replace("\\", "-")[:64]
    filename = f"project-{safe_ref}_cr-{cr_id}.log"
    session = _open_session_log(log_dir, filename)
    utc_now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    logging.getLogger(_BOT_LOGGER_NAME).info(
        "======== Review session start %s project_ref=%s cr_id=%s log_file=%s ========",
        utc_now,
        project_ref,
        cr_id,
        log_dir / filename,
    )
    return session


def detach_review_session_logging(session: ReviewLogSession | None) -> None:
    if session is None:
        return
    utc_now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _close_session_log(session, f"======== Review session end {utc_now} ========")
