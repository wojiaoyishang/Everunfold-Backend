#!/usr/bin/env bash
set -euo pipefail

cd /workspace
for dependency in pyproject.toml uv.lock; do
  if ! cmp -s "$dependency" "/opt/everunfold/$dependency"; then
    printf 'Mounted %s differs from the image. Rebuild with this source checkout.\n' "$dependency" >&2
    exit 1
  fi
done

start_api() {
  exec python -m uvicorn app.entrypoints.api:app \
    --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8001}" \
    --workers "${API_WORKERS:-1}" --no-proxy-headers --no-access-log \
    --timeout-graceful-shutdown "${API_SHUTDOWN_TIMEOUT:-30}"
}

start_worker() {
  exec celery -A app.entrypoints.worker:celery_app worker \
    --pool "$worker_pool" --concurrency "$worker_concurrency" \
    --prefetch-multiplier "$worker_prefetch" --loglevel "${WORKER_LOG_LEVEL:-INFO}" \
    --without-mingle --without-gossip
}

start_scheduler() {
  exec python -m app.entrypoints.scheduler
}

configure_worker() {
  worker_pool=${WORKER_POOL:-solo}
  worker_concurrency=${WORKER_CONCURRENCY:-1}
  worker_prefetch=${WORKER_PREFETCH_MULTIPLIER:-1}
  if [[ "$worker_pool" != solo && "$worker_pool" != prefork ]] ||
     [[ ! "$worker_concurrency" =~ ^[1-9][0-9]*$ || ! "$worker_prefetch" =~ ^[1-9][0-9]*$ ]] ||
     [[ "$worker_pool" == solo && "$worker_concurrency" != 1 ]]; then
    echo 'Use solo with concurrency 1, or prefork with positive concurrency; prefetch must be positive.' >&2
    exit 2
  fi
}

stop_services() {
  trap '' TERM INT
  kill -TERM "${children[@]}" 2>/dev/null || true
  wait "${children[@]}" 2>/dev/null || true
}

case "${1:-serve}" in
  serve)
    configure_worker
    children=()
    trap 'stop_services; exit 0' TERM INT
    start_api & children+=("$!")
    start_worker & children+=("$!")
    start_scheduler & children+=("$!")
    status=0
    wait -n "${children[@]}" || status=$?
    echo 'A backend process exited; stopping the remaining processes.' >&2
    stop_services
    exit "$((status == 0 ? 1 : status))"
    ;;
  api) start_api ;;
  worker) configure_worker; start_worker ;;
  scheduler) start_scheduler ;;
  *) exec "$@" ;;
esac
