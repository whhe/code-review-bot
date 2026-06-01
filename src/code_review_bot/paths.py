"""Utility for locating the project root directory at runtime."""

import os
from pathlib import Path

_ENV_ROOT = "CODE_REVIEW_BOT_ROOT"


def project_root() -> Path:
    raw = os.environ.get(_ENV_ROOT, "").strip()
    if raw:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise RuntimeError(f"{_ENV_ROOT} must be an existing directory: {root}")
        return root

    here = Path(__file__).resolve().parent
    for candidate in [here, *here.parents]:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise RuntimeError(
        f"Cannot find project root: set {_ENV_ROOT} or run from a tree containing pyproject.toml."
    )
