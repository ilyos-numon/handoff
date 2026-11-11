FROM node:24-slim AS web

WORKDIR /web

COPY web/package.json web/package-lock.json ./
RUN npm ci

COPY web/ ./
RUN npm run build

FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

RUN useradd --create-home --uid 1000 user

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY --chown=user:user pyproject.toml uv.lock README.md ./
COPY --chown=user:user src/ ./src/

RUN uv sync --frozen --no-dev && chown -R user:user /app

COPY --from=web --chown=user:user /web/dist ./web/dist

USER user

ENV HOME=/home/user \
    HANDOFF_DATA_DIR=/tmp/handoff-demo \
    HANDOFF_WEB_DIST=/app/web/dist \
    PORT=7860

EXPOSE 7860

CMD ["uv", "run", "--frozen", "--no-dev", "handoff-demo", "--public", "--port", "7860"]
