# The board (web/) is built in a Node stage; only its dist/ goes into the image.
FROM node:22-slim AS web
RUN corepack enable
WORKDIR /src/web
COPY web/package.json web/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY web ./
RUN pnpm build

FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates bubblewrap socat \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv

# Claude Code refuses some permission modes as root, and the agent has a shell: run unprivileged.
RUN useradd --create-home agent
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY alembic.ini ./
COPY migrations ./migrations
COPY --from=web /src/app/static/dist ./app/static/dist
COPY workflows ./workflows
COPY prompts ./prompts
RUN mkdir -p /workspaces && chown agent /workspaces
USER agent

ENV PATH="/app/.venv/bin:$PATH" \
    HARNESS_WORKSPACES_ROOT=/workspaces
EXPOSE 8000
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
