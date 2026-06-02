# code-review-bot

An extensible AI-powered code review bot with pluggable coding agents, review skills, and git
platform adapters. Ships with GitLab and GitHub adapters and an ACP coding agent backend; more
adapters can be added behind the same interface.

## How it works

The bot clones the change-request branch, then delegates the entire review to a coding agent. The
agent reads the diff, follows the review methodology defined in the `SKILL.md` of the configured
review skill, and produces structured findings. Results are posted back to the platform as inline
review comments and a summary note on the change request.

Review skills live outside this repository. `REVIEW_SKILL` can be a local directory path or an
`https` URL pointing at a skill containing `SKILL.md`; when empty the coding agent reviews using
its own built-in knowledge. The Docker image pre-installs the public code-review skill and sets
`REVIEW_SKILL=~/.agents/skills/code-review` so no network access to the skill is needed at runtime.

## Quick start

### 1. Install dependencies

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/) (see `.python-version`; Docker image uses `python:3.12-slim`).

```bash
uv sync --extra dev
pre-commit install
```

`uv sync` creates `.venv` and installs this project in editable mode. You do not need to activate the venv or re-run install after editing `src/` — use `uv run` (below) so each invocation uses the current tree.

If PyPI is slow from your network, use a mirror for that shell session only (not stored in the repo):

```bash
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
uv sync --extra dev
```

Re-run `uv sync --extra dev` when `pyproject.toml` or `uv.lock` changes (new dependencies, entry points, etc.).

`pre-commit` runs Ruff (lint + format) on each commit. To check the whole tree manually:

```bash
pre-commit run --all-files
```

### 2. Configure

```bash
cp .env.example .env
# Fill in GIT_REPO_URL and GIT_REPO_TOKEN.
```

`REVIEW_SKILL` is optional. Leave it empty to let the agent review with its own knowledge, or
install the public code-review skill for a structured methodology:

```bash
npx skills add whhe/ai-workshop --skill code-review --global --yes
# installs to ~/.agents/skills/code-review — already the default in .env.example
```

Built-in `ACP_AGENT_TYPE` values (`claude`, `codex`) wire the launcher in code — leave
`ACP_COMMAND` and `ACP_ARGS` unset. Any other type name requires both.

### 3. Run a review locally

```bash
# Post results back to the platform (project is resolved from GIT_REPO_URL in .env)
uv run code-review-bot --cr-id <change-request-id>

# Write a Markdown report instead of posting to the platform
uv run code-review-bot --cr-id <change-request-id> --debug
```

### 4. Run tests

```bash
uv run pytest tests -q
```

## Docker and CI integration

See [`docker/README.md`](docker/README.md) for building the image, running the container,
environment variables, CI integration examples, and custom skill overrides.

## Configuration

All settings are read from environment variables (or a `.env` file at `CODE_REVIEW_BOT_ROOT`).
See `.env.example` for the full list with descriptions.

### Git platform & repository

| Variable | Required | Default | Description |
|---|---|---|---|
| `GIT_PLATFORM_TYPE` | no | `gitlab` | Platform adapter to use (`gitlab` or `github`) |
| `GIT_REPO_URL` | yes | — | Full HTTPS clone URL (e.g. `https://gitlab.com/group/project.git`). Binds clone, platform API host, and project identity; no separate project ID env var. |
| `GIT_REPO_TOKEN` | yes | — | Access token with repo read permission and comment-posting rights |
| `GIT_PLATFORM_URL` | no | derived | API base override. GitLab: sub-path host (e.g. `https://example.com/gitlab`). GitHub: GHES REST root (e.g. `https://github.corp.example.com/api/v3`) when auto-derivation is wrong. GitHub.com needs no override (`api.github.com` is derived from `github.com` clone URLs). |
| `CLONE_BASE_DIR` | no | system temp | Parent directory for per-review git workspaces |
| `CLONE_DEPTH` | no | `0` | Shallow clone depth (`0` = full history) |

### Review

| Variable | Required | Default | Description |
|---|---|---|---|
| `REVIEW_SKILL` | no | `~/.agents/skills/code-review` | Local directory path or `https` URL to a skill containing `SKILL.md`. Empty = agent uses its own built-in knowledge. If the path or URL is not accessible, the bot warns and falls back automatically. Relative paths resolve against `CODE_REVIEW_BOT_ROOT`. |
| `REVIEW_EXCLUDE` | no | `[]` | JSON array of glob patterns for files to skip (e.g. `["dist/**", "*.pb.go"]`). Added on top of built-in defaults: `*.lock`, `*-lock.json`, `*.min.js`, `*.min.css`, `*.map`, `**/vendor/**`, `**/generated/**`. |
| `REVIEW_INCLUDE` | no | `[]` | JSON array of glob patterns; when set, only matching files are reviewed. Empty means all files (subject to excludes). Example: `["src/**", "tests/**"]`. |
| `OUTPUT_LANGUAGE` | no | `english` | Language for findings and the change-request summary (`english` or `chinese`). Code, configs, and identifiers stay in English. |

### Coding agent (ACP)

| Variable | Required | Default | Description |
|---|---|---|---|
| `ACP_AGENT_TYPE` | no | `claude` | Built-in: `claude`, `codex` (launcher in code). Other values require `ACP_COMMAND` + `ACP_ARGS` |
| `ACP_COMMAND` | when not built-in | from preset | Subprocess executable; required for custom `ACP_AGENT_TYPE` |
| `ACP_ARGS` | when not built-in | from preset | JSON argv after command; required for custom `ACP_AGENT_TYPE` |
| `ACP_MODEL` | no | agent default | Model override passed to the ACP agent subprocess |
| `ACP_VERBOSE` | no | `true` | Log ACP input prompts, tool calls, and streamed agent messages |
| `ACP_STREAM_LIMIT` | no | `10485760` (10 MB) | Max bytes for one ACP newline-delimited JSON frame |

### Logging

| Variable | Required | Default | Description |
|---|---|---|---|
| `LOG_LEVEL` | no | `INFO` | Bot log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `LOG_DIR` | no | `logs` | Root directory for bot-generated files (relative to `CODE_REVIEW_BOT_ROOT`). Fixed subdirs: `sessions/` (per-review logs), `debug-reports/` (`--debug` Markdown reports). Empty string disables file logging. |

### Runtime paths

| Variable | Required | Default | Description |
|---|---|---|---|
| `CODE_REVIEW_BOT_ROOT` | no | directory containing `pyproject.toml` | Root for resolving relative `LOG_DIR` and `REVIEW_SKILL` paths. Set when running the bot from outside the project tree. |
