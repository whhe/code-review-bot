"""Static contracts for the GitHub-built coding-agent images."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]


def test_shared_dockerfile_installs_only_selected_agent() -> None:
    path = _REPO_ROOT / "docker" / "Dockerfile"
    assert path.is_file()
    assert not (_REPO_ROOT / "docker" / "Dockerfile.opencode").exists()
    assert not (_REPO_ROOT / "docker" / "acp-launcher.sh").exists()

    dockerfile = path.read_text(encoding="utf-8")
    assert "FROM python:3.12-slim AS base" in dockerfile
    assert "https://deb.nodesource.com/setup_22.x" in dockerfile
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
    assert "npm install -g @agentclientprotocol/claude-agent-acp" in claude_stage
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


def test_env_example_defaults_to_claude_agent() -> None:
    env_example = (_REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    active_settings = dict(
        line.split("=", 1)
        for line in env_example.splitlines()
        if line and not line.startswith("#") and "=" in line
    )

    assert active_settings["ACP_AGENT_TYPE"] == "claude"
