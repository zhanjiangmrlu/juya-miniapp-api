FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.12.19 \
    && groupadd --system juya \
    && useradd --system --gid juya --home-dir /app juya

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN uv sync --frozen --no-dev \
    && chmod 0555 ./scripts/entrypoint.sh \
    && chown -R juya:juya /app

USER juya
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; os.getenv('JUYA_PROCESS_TYPE','api') == 'worker' or urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

ENTRYPOINT ["./scripts/entrypoint.sh"]
