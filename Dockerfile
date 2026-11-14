FROM ghcr.io/astral-sh/uv:0.13.8@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv
FROM python:3.12.13-slim-bookworm@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    PATH="/app/.venv/bin:${PATH}" \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    TIKTOKEN_CACHE_DIR=/opt/tiktoken

WORKDIR /app
COPY --from=uv /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --python /usr/local/bin/python3.12 --no-dev --no-install-project --no-cache \
    && python -c "import tiktoken; tiktoken.get_encoding('o200k_base')" \
    && chmod -R a+rX /opt/tiktoken

COPY alembic.ini ./
COPY migrations ./migrations
COPY src ./src
COPY demo/materials.jsonl ./demo/materials.jsonl

USER 10001:10001
CMD ["python", "-m", "alfa_bot.main"]
