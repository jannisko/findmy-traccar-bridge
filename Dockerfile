FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /bridge

COPY uv.lock pyproject.toml .

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable

ADD . /bridge

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable

FROM python:3.12-slim

RUN adduser --system --no-create-home app
USER app
WORKDIR /bridge

COPY --from=builder --chown=app /bridge/.venv /bridge/.venv

CMD [".venv/bin/findmy-traccar-bridge"]
