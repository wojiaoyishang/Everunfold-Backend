# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.9.0 AS uv
FROM python:3.12-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/everunfold/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/everunfold/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_FILE=/etc/everunfold/config.toml \
    API_PORT=8001

WORKDIR /opt/everunfold
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev \
    && install -d -o 1000 -g 1000 /data/uploads
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/everunfold-backend

WORKDIR /workspace
USER 1000:1000
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['API_PORT'] + '/health/ready', timeout=4).close()"]
ENTRYPOINT ["/usr/local/bin/everunfold-backend"]
CMD ["serve"]
