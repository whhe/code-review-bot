# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

# Optional build-time mirrors:
#   --build-arg APT_MIRROR=mirrors.aliyun.com
#   --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
#   --build-arg NPM_CONFIG_REGISTRY=https://registry.npmmirror.com
ARG APT_MIRROR
ARG PIP_INDEX_URL
ARG NPM_CONFIG_REGISTRY

# Install shared runtime dependencies for both coding-agent images.
RUN if [ -n "$APT_MIRROR" ]; then \
        for f in /etc/apt/sources.list /etc/apt/sources.list.d/*; do \
            [ -f "$f" ] || continue; \
            sed -i \
                "s|deb.debian.org|${APT_MIRROR}|g; s|security.debian.org|${APT_MIRROR}|g" \
                "$f"; \
        done; \
    fi \
    && apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        curl \
        gnupg \
        ripgrep \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the application before specializing the final image by coding agent.
COPY pyproject.toml ./
COPY src/ ./src/
RUN if [ -n "$PIP_INDEX_URL" ]; then \
        pip install --no-cache-dir -i "$PIP_INDEX_URL" -e .; \
    else \
        pip install --no-cache-dir -e .; \
    fi

COPY docker/bootstrap.py /usr/local/bin/bootstrap.py
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/bootstrap.py /usr/local/bin/docker-entrypoint.sh

RUN useradd -m -s /bin/bash appuser \
    && mkdir -p logs/sessions logs/debug-reports \
    && chown -R appuser:appuser /app

USER appuser

# Install the public code-review skill into the image so reviews work without network I/O.
# The skill is installed to ~/.agents/skills/code-review (appuser home directory).
RUN if [ -n "$NPM_CONFIG_REGISTRY" ]; then \
        npx --registry "$NPM_CONFIG_REGISTRY" skills add whhe/ai-workshop --skill code-review --global --yes; \
    else \
        npx skills add whhe/ai-workshop --skill code-review --global --yes; \
    fi

ENV NPM_CONFIG_REGISTRY=${NPM_CONFIG_REGISTRY} \
    CODE_REVIEW_BOT_ROOT=/app \
    CLONE_BASE_DIR=/tmp/reviews \
    REVIEW_SKILL=~/.agents/skills/code-review

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["code-review-bot", "--help"]

# Keep OpenCode before Claude so a build without --target retains the historical Claude default.
FROM base AS opencode
USER root
ARG NPM_CONFIG_REGISTRY
RUN set -eu; \
    if [ -n "$NPM_CONFIG_REGISTRY" ]; then npm config set registry "$NPM_CONFIG_REGISTRY"; fi; \
    npm install -g opencode-ai; \
    npm list -g --depth=0
USER appuser
ENV ACP_AGENT_TYPE=opencode \
    OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=65536

FROM base AS claude
USER root
ARG NPM_CONFIG_REGISTRY
RUN set -eu; \
    if [ -n "$NPM_CONFIG_REGISTRY" ]; then npm config set registry "$NPM_CONFIG_REGISTRY"; fi; \
    npm install -g @zed-industries/claude-agent-acp; \
    npm list -g --depth=0
USER appuser
ENV ACP_AGENT_TYPE=claude
