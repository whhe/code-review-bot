# Docker

## Build

Build from the repository root:

```bash
docker build -f docker/Dockerfile -t whhe/code-review-bot:latest .
```

If PyPI, npm, or apt is slow from your network, pass mirrors at build time:

```bash
docker build -f docker/Dockerfile \
  --build-arg APT_MIRROR=mirrors.aliyun.com \
  --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
  --build-arg NPM_CONFIG_REGISTRY=https://registry.npmmirror.com \
  -t whhe/code-review-bot:latest .
```

| Build arg | Default | Description |
|---|---|---|
| `APT_MIRROR` | — | Debian apt mirror host (replaces `deb.debian.org` and `security.debian.org` in apt sources) |
| `PIP_INDEX_URL` | — | PyPI mirror used during `pip install` only |
| `NPM_CONFIG_REGISTRY` | — | npm registry for the build-time `npx` warm-up; baked into the image for runtime ACP |

`NPM_CONFIG_REGISTRY` is inherited by `npx` when the bot launches the coding agent. Override at
run time with `-e NPM_CONFIG_REGISTRY=...` if needed.

## Run

```bash
docker run --env-file .env -v ./logs:/app/logs whhe/code-review-bot:latest --cr-id <change-request-id>

# Write a Markdown report instead of posting to the platform
docker run --env-file .env -v ./logs:/app/logs whhe/code-review-bot:latest --cr-id <change-request-id> --debug
```

## Environment variables

`bootstrap.py` runs on first container start and writes `~/.claude/settings.json` from the
variables below. If the file already exists it is left untouched.

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | yes (one of two) | — | Anthropic API key (mutually exclusive with `ANTHROPIC_AUTH_TOKEN`) |
| `ANTHROPIC_AUTH_TOKEN` | yes (one of two) | — | Alternative Anthropic credential (mutually exclusive with `ANTHROPIC_API_KEY`) |
| `ANTHROPIC_BASE_URL` | no | — | Optional Anthropic API base URL (proxy / custom endpoint) |
| `ANTHROPIC_MODEL` | no | `claude-opus-4-6` | Written into Claude Code `settings.json` on first container start |

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
  image: whhe/code-review-bot:latest
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  variables:
    GIT_STRATEGY: none
    GIT_PLATFORM_TYPE: gitlab
    GIT_REPO_URL: "${CI_PROJECT_URL}.git"
    GIT_REPO_TOKEN: $GITLAB_TOKEN
    ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
  script:
    - code-review-bot --cr-id $CI_MERGE_REQUEST_IID
```

`GIT_REPO_URL` is derived from the GitLab predefined variable `$CI_PROJECT_URL`. Set
`GITLAB_TOKEN` and `ANTHROPIC_API_KEY` as masked CI/CD variables in that project's settings.
Optionally set `ANTHROPIC_MODEL`.

### Other platforms

Adapt the example above to your platform's workflow syntax. Any CI environment that can run a
Docker container or a Python process can invoke `code-review-bot --cr-id <id>` directly.
