import os
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

os.environ.setdefault("CODE_REVIEW_BOT_ROOT", str(_REPO_ROOT))

os.environ.setdefault("GIT_REPO_URL", "https://gitlab.test/group/project.git")
os.environ.setdefault("GIT_REPO_TOKEN", "test-token")


_DUMMY_SKILL_BODY = (
    "---\n"
    "name: code-review\n"
    "description: Test stub skill (not the real methodology)\n"
    "---\n\n"
    "# Code review (test stub)\n\n"
    "Follow the system contract.\n"
)


@pytest.fixture
def sample_skill_dir(tmp_path: Path) -> Path:
    """Create a throwaway skill directory (with SKILL.md) under tmp_path.

    The directory name "code-review" is asserted on as skill.name in tests
    that exercise the skill-loader contract.
    """
    skill_dir = tmp_path / "code-review"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_DUMMY_SKILL_BODY, encoding="utf-8")
    return skill_dir
