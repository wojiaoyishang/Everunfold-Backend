# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.9.0 AS uv
FROM python:3.12-slim-bookworm AS everunfold-backend-runtime

ARG BUILD_REVISION=unknown
ARG BUILD_CREATED=unknown
LABEL org.opencontainers.image.title="Everunfold Backend" \
    org.opencontainers.image.description="Everunfold API, Worker and Scheduler runtime" \
    org.opencontainers.image.source="https://github.com/wojiaoyishang/Everunfold-Backend" \
    org.opencontainers.image.revision="${BUILD_REVISION}" \
    org.opencontainers.image.created="${BUILD_CREATED}"
ENV EVERUNFOLD_IMAGE_REVISION=${BUILD_REVISION}

COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/everunfold/venv \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/everunfold/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CONFIG_FILE=/workspace/config.yaml \
    EVERUNFOLD_DATA_DIR=/data

WORKDIR /opt/everunfold
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev
COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/everunfold-backend

WORKDIR /workspace
USER 1000:1000
EXPOSE 8001
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s \
    CMD ["python", "/workspace/deploy/healthcheck.py"]
ENTRYPOINT ["/usr/local/bin/everunfold-backend"]
CMD ["serve"]
