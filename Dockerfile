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

USER user

ENV HOME=/home/user \
    HANDOFF_DATA_DIR=/tmp/handoff-demo \
    PORT=7860

EXPOSE 7860

CMD ["uv", "run", "--frozen", "--no-dev", "handoff-demo", "--public", "--port", "7860"]
