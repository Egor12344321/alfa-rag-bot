# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/app/private_data/models/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    TOKENIZERS_PARALLELISM=false \
    LANGSMITH_TRACING=false \
    LANGCHAIN_TRACING_V2=false
WORKDIR /app

# CPU wheels avoid pulling CUDA libraries into the bot image.
ARG TORCH_VERSION=2.15.0
RUN python -m pip install --no-cache-dir "torch==${TORCH_VERSION}" \
    --index-url https://download.pytorch.org/whl/cpu
COPY requirements.lock requirements-embeddings.lock ./
RUN python -m pip install --no-cache-dir -r requirements.lock -r requirements-embeddings.lock
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --no-deps -e .
COPY alembic.ini ./
COPY migrations ./migrations
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home app \
    && mkdir -p /app/private_data/models \
    && chown -R app:app /app
USER app
CMD ["python", "-m", "alfa_bot.main"]
