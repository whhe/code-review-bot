# Docker

## Build

Build from the repository root:

```bash
docker build -f docker/Dockerfile \
  --target claude \
  -t whhe/code-review-bot:claude-code .

docker build -f docker/Dockerfile \
  --target opencode \
  -t whhe/code-review-bot:opencode .
```

If PyPI, npm, or apt is slow from your network, pass mirrors at build time:

```bash
docker build -f docker/Dockerfile \
  --target opencode \
  --build-arg APT_MIRROR=mirrors.aliyun.com \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  --build-arg NPM_CONFIG_REGISTRY=https://registry.npmmirror.com \
  -t whhe/code-review-bot:opencode .
```

| Build arg | Default | Description |
|---|---|---|
| `APT_MIRROR` | — | Debian apt mirror host (replaces `deb.debian.org` and `security.debian.org` in apt sources) |
| `PIP_INDEX_URL` | — | PyPI mirror used during `pip install` only |
| `NPM_CONFIG_REGISTRY` | — | npm registry for build-time package installs; persisted as the image's npm registry default |

Select the `claude` or `opencode` target to install only that coding agent. A build without
`--target` uses the final `claude` stage. Package versions are intentionally unpinned, so a rebuilt
npm layer resolves the current registry version; `npm list -g --depth=0` records the resolved
version in the build log. A cached layer retains its installed version.

## Coding-agent image tags

GitHub Actions builds both variants from the shared `docker/Dockerfile`:

```text
whhe/code-review-bot:claude-code
whhe/code-review-bot:latest
whhe/code-review-bot:opencode
```

Each target persists its matching `ACP_AGENT_TYPE`. Both variants leave `ACP_COMMAND` and
`ACP_ARGS` unset. Built-in presets start Claude through
`npx -y @zed-industries/claude-agent-acp` and OpenCode through `opencode acp`. Agent-specific
Docker stages ensure Claude images do not contain OpenCode-only environment defaults.

`:latest` and `:claude-code` are aliases for the same Claude image. OpenCode is published only as
`:opencode`.

## OpenCode image configuration

The OpenCode image uses these variables during setup and runtime. For the generated config, the
endpoint and API key remain required at runtime because OpenCode resolves their environment
placeholders for every agent process. The model and context limit are read only when bootstrap
creates the config; the output limit is used during both setup and runtime.

| Variable | Required when | Default | Description |
|---|---|---|---|
| `OPENCODE_UPSTREAM_ENDPOINT` | runtime | — | OpenAI-compatible API base URL |
| `OPENCODE_UPSTREAM_API_KEY` | runtime | — | OpenCode upstream API credential |
| `OPENCODE_MODEL` | first setup | — | Upstream model ID |
| `OPENCODE_CONTEXT_LENGTH` | first setup (optional) | `1000000` | Model context limit; must be a positive integer |
| `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX` | setup and runtime (optional) | `65536` | Model and global output limit; must be positive and smaller than the context limit |

The limit defaults match `qwen3.7-max`, but `OPENCODE_MODEL` remains runtime-configurable. When
selecting another fixed model, override both limits to match it. The generated model entry declares
both `limit.context` and `limit.output`; the image also exports the output value through
`OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX`.

Bootstrap writes `~/.config/opencode/opencode.json` only when it does not already exist. The
generated provider references `{env:OPENCODE_UPSTREAM_API_KEY}` instead of storing the credential,
and the OpenCode setup path does not print the generated configuration. Mount a pre-reviewed
configuration at that path to bypass generation. The ACP launcher forwards only the OpenCode
endpoint, key, output limit, and npm-registry variables.

The generated config does not override OpenCode's tool permissions, matching the existing Claude
image. The shared review prompt explicitly requires a read-only review and forbids file changes,
commits, pushes, and comment publication. This is a behavioral instruction, not a container
security boundary.

The review workspace checks out the target branch, allowing Claude Code, Codex, OpenCode, and
future ACP agents to discover project instructions through their own native rules. The source
branch is available only through stable Git refs pinned to the platform's change-request diff
version and is inspected with read-only `git diff` and `git show` commands. The bot does not
enumerate instruction or configuration file names.

## Default review skill

The image pre-installs the public `code-review` skill from
[whhe/ai-workshop](https://github.com/whhe/ai-workshop) via
`npx skills add whhe/ai-workshop --skill code-review --global --yes` and sets
`REVIEW_SKILL=~/.agents/skills/code-review`. The skill lands in the appuser
home directory; the tilde is expanded at runtime by the bot. Reviews run with
a consistent methodology baked into the image and require no network access to
the skill source at runtime.

To use a different skill, override `REVIEW_SKILL` when running the container.
The value must be a path **accessible inside the container** — the pre-installed skill
(`~/.agents/skills/code-review`) is already there, but a custom skill file on the host
must be mounted first:

```bash
docker run --env-file .env \
  -v /host/path/to/skills:/skills \
  -e REVIEW_SKILL=/skills/my-skill \
  ...
```

To fall back to the agent's built-in knowledge (no skill), set `REVIEW_SKILL=`.

## Run

```bash
docker run --env-file .env -v ./logs:/app/logs whhe/code-review-bot:claude-code --cr-id <change-request-id>

# Write a Markdown report instead of posting to the platform
docker run --env-file .env -v ./logs:/app/logs whhe/code-review-bot:claude-code --cr-id <change-request-id> --debug
```

## Claude Code image environment variables

`docker-entrypoint.sh` runs `bootstrap.py` on every container start; bootstrap writes
`~/.claude/settings.json` from the variables below on first start only. If the file already
exists it is left untouched.

| Variable | Required when | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | first start (one of two) | — | Anthropic API key (mutually exclusive with `ANTHROPIC_AUTH_TOKEN`) |
| `ANTHROPIC_AUTH_TOKEN` | first start (one of two) | — | Alternative Anthropic credential (mutually exclusive with `ANTHROPIC_API_KEY`) |
| `ANTHROPIC_BASE_URL` | first start (optional) | — | Anthropic API base URL (proxy / custom endpoint) |
| `ANTHROPIC_MODEL` | first start (optional) | `claude-opus-4-6` | Written into Claude Code `settings.json` |

All other bot settings (`GIT_REPO_URL`, `GIT_REPO_TOKEN`, `REVIEW_SKILL`, etc.) are read from the
same env file — see the [configuration reference](../README.md#configuration) in the root README.

## CI integration

The bot runs as a plain CLI command inside the container. Pass the platform's merge/pull request
ID via `--cr-id` and expose the required credentials as masked CI/CD variables.

### GitLab CI

Add a job to the `.gitlab-ci.yml` of the **repository you want reviewed** that runs this image
and passes `$CI_MERGE_REQUEST_IID` as `--cr-id`:

```yaml
code-review:
  image: whhe/code-review-bot:claude-code
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    GIT_STRATEGY: none
    GIT_PLATFORM_TYPE: gitlab
    GIT_REPO_URL: "${CI_PROJECT_URL}.git"
    GIT_REPO_TOKEN: $GITLAB_TOKEN
    ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
  script:
    - code-review-bot --cr-id "${CI_MERGE_REQUEST_IID}"
```

The image `ENTRYPOINT` runs bootstrap setup then `exec`s the job command. GitLab passes the
`script` via `sh -c`, so `code-review-bot --cr-id …` in `script` works without overriding
`entrypoint`.

`GIT_REPO_URL` is derived from the GitLab predefined variable `$CI_PROJECT_URL`. Set
`GITLAB_TOKEN` and `ANTHROPIC_API_KEY` as masked CI/CD variables in that project's settings.
Optionally set `ANTHROPIC_MODEL`.

### Other platforms

Adapt the example above to your platform's workflow syntax. Any CI environment that can run a
Docker container or a Python process can invoke `code-review-bot --cr-id <id>` directly.
