"""Static contracts for the GitHub-built coding-agent images."""

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shared_dockerfile_installs_only_selected_agent() -> None:
    path = _REPO_ROOT / "docker" / "Dockerfile"
    assert path.is_file()
    assert not (_REPO_ROOT / "docker" / "Dockerfile.opencode").exists()
    assert not (_REPO_ROOT / "docker" / "acp-launcher.sh").exists()

    dockerfile = path.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim AS base" in dockerfile
    assert "FROM base AS opencode" in dockerfile
    assert "FROM base AS claude" in dockerfile
    assert "FROM agent-${ACP_AGENT_TYPE}" not in dockerfile
    assert "OPENCODE_VERSION" not in dockerfile
    assert "CLAUDE_VERSION" not in dockerfile
    assert "ACP_COMMAND" not in dockerfile
    assert "ACP_ARGS" not in dockerfile
    assert "skills add whhe/ai-workshop --skill code-review" in dockerfile
    assert "REVIEW_SKILL=~/.agents/skills/code-review" in dockerfile
    assert "COPY skills/ ./skills/" not in dockerfile

    opencode_stage = dockerfile.split("FROM base AS opencode", 1)[1].split(
        "FROM base AS claude", 1
    )[0]
    claude_stage = dockerfile.split("FROM base AS claude", 1)[1]
    assert "npm install -g @zed-industries/claude-agent-acp" in claude_stage
    assert "ENV ACP_AGENT_TYPE=claude" in claude_stage
    assert "OPENCODE_" not in claude_stage
    assert "npm install -g opencode-ai" in opencode_stage
    assert "ENV ACP_AGENT_TYPE=opencode" in opencode_stage
    assert "OPENCODE_DISABLE_PROJECT_CONFIG=1" not in opencode_stage
    assert "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=65536" in opencode_stage


def test_github_actions_publishes_agent_tags_from_shared_dockerfile() -> None:
    workflow = (_REPO_ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(
        encoding="utf-8"
    )

    assert "file: docker/Dockerfile" in workflow
    assert "agent_type: claude" in workflow
    assert "tag: claude-code" in workflow
    assert "agent_type: opencode" in workflow
    assert "tag: opencode" in workflow
    assert "target: ${{ matrix.agent_type }}" in workflow
    assert "build-args: ACP_AGENT_TYPE=${{ matrix.agent_type }}" not in workflow
    assert "uses: docker/metadata-action@v5" in workflow
    assert "id: sha" in workflow
    assert 'echo "value=$(git rev-parse --short HEAD)" >> $GITHUB_OUTPUT' in workflow
    assert "type=raw,value=${{ matrix.tag }}" in workflow
    assert "type=raw,value=latest,enable=${{ matrix.agent_type == 'claude' }}" in workflow
    assert (
        "type=raw,value=${{ steps.sha.outputs.value }},enable=${{ matrix.agent_type == 'claude' }}"
    ) in workflow
    assert "tags: ${{ steps.meta.outputs.tags }}" in workflow
    assert "cache-from: type=gha,scope=${{ matrix.tag }}" in workflow
    assert "cache-to: type=gha,mode=max,scope=${{ matrix.tag }}" in workflow


def test_env_example_scopes_agent_credentials_to_image_variants() -> None:
    env_example = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    docker_heading = "# Docker only — image setup and runtime"
    before_docker, docker_section = env_example.split(docker_heading, 1)
    active_settings = dict(
        line.split("=", 1)
        for line in env_example.splitlines()
        if line and not line.startswith("#") and "=" in line
    )

    assert "docker" not in before_docker.lower()
    section_divider = "# " + "=" * 77
    assert docker_section.count(section_divider) == 1
    assert "Claude Code image only: whhe/code-review-bot:claude-code (also :latest)" in env_example
    assert (
        "[docker, required on first start for Claude Code image] Set exactly one credential"
        in env_example
    )
    assert (
        "[docker, optional on first start for Claude Code image] Anthropic API base URL"
        in env_example
    )
    assert "OpenCode image only: whhe/code-review-bot:opencode" in env_example
    assert "[docker, required at runtime for :opencode] Upstream API credential" in env_example
    assert "[docker, required on first start for :opencode] Upstream model ID" in env_example
    assert active_settings["ACP_AGENT_TYPE"] == "claude"

    docker_readme = (_REPO_ROOT / "docker" / "README.md").read_text(encoding="utf-8")
    claude_section = docker_readme.split("## Claude Code image environment variables", 1)[1].split(
        "## CI integration", 1
    )[0]
    assert "| Variable | Required when | Default | Description |" in claude_section
    assert "| `ANTHROPIC_API_KEY` | first start (one of two) |" in claude_section
    assert "endpoint and API key remain required at runtime" in docker_readme
    assert "target branch" in docker_readme
    assert re.search(r"source\s+branch", docker_readme)
