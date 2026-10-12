# 固定官方 Python 3.13/uv 镜像摘要,避免构建机连接 Docker Hub。
FROM ghcr.io/astral-sh/uv:0.12.19-python3.13-trixie-slim@sha256:aba5f865793af9275ceaa06d30935dfc6db8b81969c9fb099b0ebca6396b189e AS python-base

FROM python-base AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
# 导出锁定版本及哈希,通过阿里云镜像下载依赖,避免锁文件中的 PyPI 直链。
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file /tmp/requirements.txt >/dev/null \
    && uv venv /app/.venv \
    && uv pip sync --python /app/.venv/bin/python --require-hashes \
        --index-url https://mirrors.aliyun.com/pypi/simple/ /tmp/requirements.txt \
    && rm /tmp/requirements.txt
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --python /app/.venv/bin/python --no-deps \
        --index-url https://mirrors.aliyun.com/pypi/simple/ . \
    && uv pip check --python /app/.venv/bin/python

FROM python-base AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH

RUN rm /usr/local/bin/uv /usr/local/bin/uvx \
    && groupadd --system juya \
    && useradd --system --gid juya --home-dir /app juya

WORKDIR /app
COPY --from=builder --chown=juya:juya /app/.venv /app/.venv
COPY --chown=juya:juya scripts/entrypoint.sh ./scripts/entrypoint.sh
RUN chmod 0555 ./scripts/entrypoint.sh

USER juya
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; os.getenv('JUYA_PROCESS_TYPE','api') == 'worker' or urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"

ENTRYPOINT ["./scripts/entrypoint.sh"]
