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
│   ├── protocol.py         # CodingAgent protocol
│   ├── acp.py              # ACP subprocess implementation (AcpCodingAgent)
│   ├── presets.py          # built-in launcher presets: claude, codex
│   └── factory.py          # build_coding_agent(settings, cwd)
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
│       ├── platform.py     # posts inline comments + summary note to the git platform
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
   change request, and clones the source branch into a temporary workspace.
2. `load_skill(REVIEW_SKILL)` returns a skill object whose `build_prompt()` assembles the full
   agent prompt (system output contract + task context + skill reference + MR metadata).
3. `CodingAgentReviewRunner` passes the prompt to the `CodingAgent` and parses the JSON
   `SkillResult` from the response (with `json-repair` as fallback for malformed output).
4. Findings are filtered by `FileFilter`, then deduplicated by SHA-256 fingerprint against the
   prior review's stored metadata. All inline comment threads (resolved and open) are included in
   the prompt via `<inline_threads>`; the agent applies the embedded rules to decide whether to
   re-report each one.
5. `ReviewPublisher.publish()` posts inline diff comments and a summary note, storing the new
   fingerprint set in a hidden metadata comment for the next run.
6. When `AUTO_APPROVE_ON_CLEAN_REVIEW=true` (and not in `--debug` mode), the orchestrator
   approves the change request if no new findings were published, or revokes approval when new
   findings exist (GitLab: approve/unapprove API; GitHub: `APPROVE` / `REQUEST_CHANGES` review).

## Key protocols

All three are `typing.Protocol` — swap an implementation by updating the corresponding factory.

| Protocol | Module | Factory |
|---|---|---|
| `CodingAgent` | `agent/protocol.py` | `agent/factory.py` — `build_coding_agent()` |
| `PlatformAdapter` | `platforms/protocol.py` | `platforms/factory.py` — `build_platform_adapter()` |
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

`docker/entrypoint.sh` is the container entrypoint: it runs `docker/bootstrap.py` (Claude
setup) then `exec`s the command. `bootstrap.py` only supports `ACP_AGENT_TYPE=claude`; any
other value exits with an error. On first start it writes `~/.claude/settings.json` from
`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` and optional `ANTHROPIC_MODEL` /
`ANTHROPIC_BASE_URL`. If the file already exists it is left untouched.
