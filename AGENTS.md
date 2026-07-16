# AGENTS.md

## Commands

```bash
uv sync --extra dev          # install / sync deps (first time and after lockfile changes)
uv run pytest tests -q       # run all tests
uv run ruff check src        # lint
uv run ruff format src       # format
pre-commit run --all-files   # lint + format all files

uv run code-review-bot --cr-id <id>          # run review, post to platform
uv run code-review-bot --cr-id <id> --debug  # write Markdown report, do not post
```

## Architecture

```
src/code_review_bot/
├── config.py               # pydantic-settings; Settings singleton via get_settings()
├── cli.py                  # typer CLI entry point
├── agent/
│   ├── acp.py              # ACP subprocess implementation (AcpCodingAgent)
│   ├── factory.py          # build_coding_agent(settings, cwd)
│   ├── presets.py          # built-in launcher presets: claude, codex, opencode
│   └── protocol.py         # CodingAgent protocol
├── platforms/
│   ├── protocol.py         # PlatformAdapter protocol
│   ├── factory.py          # build_platform_adapter(settings)
│   ├── models.py           # shared models: ChangeRequest, InlineThread
│   ├── gitlab/             # GitLab adapter
│   └── github/             # GitHub adapter
├── review/
│   ├── orchestrator.py     # ReviewOrchestrator — top-level review flow
│   ├── runner.py           # CodingAgentReviewRunner — calls agent, parses JSON result
│   ├── context.py          # fingerprinting and metadata parsing
│   ├── file_filter.py      # FileFilter — include/exclude glob filtering
│   ├── models.py           # ReviewTaskContext, ReviewOutcome
│   └── publish/
│       ├── protocol.py     # ReviewPublisher protocol
│       ├── platform.py     # posts inline comments + summary to the git platform
│       └── debug.py        # writes Markdown report to disk (--debug mode)
├── skill/
│   ├── protocol.py         # ReviewSkill protocol; Finding and SkillResult pydantic models
│   ├── loader.py           # load_skill(path) → FilesystemMarkdownSkill | RemoteUrlSkill
│   └── filesystem.py       # skill implementations + build_review_prompt()
└── repo/
    └── manager.py          # git clone, workspace creation and cleanup
```

## Review flow

1. `ReviewOrchestrator.review_change_request(cr_id)` resolves the project ref, fetches the
   change request, and creates a temporary workspace with the target branch checked out. The
   source and target commits remain available as `refs/code-review/source` and
   `refs/code-review/target`, pinned to the platform's diff SHAs when provided. Shallow clones are
   automatically deepened when the two review refs do not expose a merge base.
2. `load_skill(REVIEW_SKILL)` returns a skill object whose `build_prompt()` assembles the full
   agent prompt (system output contract + task context + skill reference + MR metadata).
3. `CodingAgentReviewRunner` passes the prompt to the `CodingAgent` and parses the JSON
   `SkillResult` from the response (with `json-repair` as fallback for malformed output).
4. Findings are filtered by `FileFilter`, then deduplicated by SHA-256 fingerprint against the
   prior review's stored metadata. All inline comment threads (resolved and open) are included in
   the prompt via `<inline_threads>`; the agent applies the embedded rules to decide whether to
   re-report each one.
5. `ReviewPublisher.publish()` posts inline diff comments and formats the summary, storing the new
   fingerprint set in hidden metadata for the next run. GitLab and GitHub without automatic
   approval post it as a note; GitHub with automatic approval defers it to the final review body.
6. When `AUTO_APPROVE_ON_CLEAN_REVIEW=true` (and not in `--debug` mode), the orchestrator
   approves the change request if no new findings were published, or revokes approval when new
   findings exist (GitLab: approve/unapprove API; GitHub: `APPROVE` / `REQUEST_CHANGES` review
   containing the full summary). If the GitHub review cannot be submitted, the summary falls back
   to an issue comment.
   When `AUTO_APPROVE_IGNORE_LOW_SEVERITY=true`, low-severity findings are excluded from this
   decision: a review with only low-severity findings is still treated as clean.

## Key protocols

The core interfaces are `typing.Protocol` types; swap an implementation by updating the
corresponding factory. `ReviewBodyApprovalAdapter` is an optional runtime-checkable capability used
to consolidate a GitHub summary into the final review decision.

| Protocol | Module | Factory |
|---|---|---|
| `CodingAgent` | `agent/protocol.py` | `agent/factory.py` — `build_coding_agent()` |
| `PlatformAdapter` | `platforms/protocol.py` | `platforms/factory.py` — `build_platform_adapter()` |
| `ReviewBodyApprovalAdapter` | `platforms/protocol.py` | implemented by the GitHub adapter |
| `ReviewPublisher` | `review/publish/protocol.py` | instantiated in `orchestrator.py` |

## Extension points

### Add a platform adapter

1. Create `src/code_review_bot/platforms/<name>/adapter.py` implementing `PlatformAdapter`.
2. Register it in `src/code_review_bot/platforms/factory.py`.
3. Extend `git_platform_type: Literal[..., "<name>"]` in `config.py`.

### Swap the coding agent

1. Implement `CodingAgent` (see `agent/protocol.py`).
2. Update `agent/factory.py` to instantiate it.

## Testing conventions

- **Isolate from `.env`**: pass `_env_file=None` to `Settings()` in tests; pydantic-settings
  otherwise reads the developer's local `.env` file, which breaks assertions on defaults.
- `pytest-asyncio` is configured with `asyncio_mode = auto`; mark async tests with
  `@pytest.mark.asyncio` only if you need per-test mode overrides.
- `respx` is used for mocking `httpx.AsyncClient` calls in platform adapter tests.
- `conftest.py` provides a `sample_skill_dir` fixture (a minimal local skill directory).

```python
# Correct — ignores any .env file:
settings = Settings(git_repo_url="...", git_repo_token="...", _env_file=None)
```

## Settings singleton

`get_settings()` is decorated with `@lru_cache` — it is a per-process singleton. Tests must
**not** call `get_settings()`; construct `Settings(...)` directly with `_env_file=None`.

## Skill system

`load_skill(path)` accepts:
- An absolute or relative local path to a directory containing `SKILL.md` (or the `SKILL.md`
  file itself — the parent directory is used automatically).
- An `https://` URL — wrapped in `RemoteUrlSkill`; the coding agent fetches the skill on demand.

`build_review_prompt()` in `skill/filesystem.py` assembles the full prompt. The system output
contract (JSON-only response, `SkillResult` schema) is injected here and **overrides** any output
format the skill itself may specify.

## Docker bootstrap

`.github/workflows/docker-publish.yml` builds two Docker Hub tags on pushes to `main` from the
shared `docker/Dockerfile`: `whhe/code-review-bot:claude-code` and `:latest` both select the Claude
stage, while `whhe/code-review-bot:opencode` selects the OpenCode stage. Agent-specific stages
install only the selected package, keep OpenCode-only environment defaults out of Claude images,
and leave package versions unpinned. The public image retains the `python:3.12-slim` base and
installs the review skill into `~/.agents/skills/code-review`.

`docker/entrypoint.sh` runs the shared `docker/bootstrap.py`, then `exec`s the command. Bootstrap
supports `ACP_AGENT_TYPE=claude` and `ACP_AGENT_TYPE=opencode`; other values fail. The image sets
neither `ACP_COMMAND` nor `ACP_ARGS`; built-in presets resolve Claude to its `npx` command and
OpenCode to `opencode acp`. Claude setup retains the existing `~/.claude/settings.json` behavior.
OpenCode setup generates `~/.config/opencode/opencode.json` from `OPENCODE_UPSTREAM_ENDPOINT`,
`OPENCODE_UPSTREAM_API_KEY`, and `OPENCODE_MODEL`, references the key through an env placeholder,
and writes required context/output model limits. It does not override OpenCode tool permissions;
the shared review prompt supplies the read-only behavioral constraint. The workspace has the
target branch checked out, so each ACP agent discovers target-branch project instructions and
configuration through its own native rules. The source branch is available only through stable
review refs pinned to the platform's change-request version and is inspected with read-only Git
commands; the bot does not enumerate agent instruction files. Git clone and fetch use an ephemeral
credential helper so tokens are never embedded in workspace URLs or Git metadata. The agent
factory explicitly forwards only the required OpenCode runtime environment through ACP's filtered
subprocess environment. Existing global config files are left untouched.

`.github/workflows/code-review.yml` runs `whhe/code-review-bot:opencode` for non-draft,
same-repository pull requests targeting `main` and skips fork pull requests. Dependabot pull
requests are allowed to run, but GitHub does not provide Actions secrets and grants the built-in
`GITHUB_TOKEN` read-only permissions for those runs. The workflow therefore prefers an optional
`CODE_REVIEW_GITHUB_TOKEN` secret and falls back to the built-in token; Dependabot reviews require
same-named Dependabot secrets for that write-capable token and `OPENCODE_UPSTREAM_API_KEY`. The
workflow uses repository variables for `OPENCODE_UPSTREAM_ENDPOINT` and `OPENCODE_MODEL`.
It enables clean-review approval with `AUTO_APPROVE_ON_CLEAN_REVIEW=true` and debug logging with
`LOG_LEVEL=DEBUG`; the repository must allow GitHub Actions to create and approve pull requests for
the built-in token to approve a clean review.
