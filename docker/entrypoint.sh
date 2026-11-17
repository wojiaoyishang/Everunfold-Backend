#!/usr/bin/env bash
set -euo pipefail

cd /workspace
for dependency in pyproject.toml uv.lock; do
  if ! cmp -s "$dependency" "/opt/everunfold/$dependency"; then
    printf 'Mounted %s differs from the image. Rebuild with this source checkout.\n' "$dependency" >&2
    exit 1
  fi
done

if [[ "${EVERUNFOLD_CONFIG_LOADED:-}" != 1 ]]; then
  exec python /workspace/deploy/container_entry.py "$@"
fi

printf 'Everunfold Backend | runtime image %s | source /workspace | data /data\n' "${EVERUNFOLD_IMAGE_REVISION:-unknown}"

start_api() {
  local level=${API_LOG_LEVEL:-INFO}
  exec python -m uvicorn app.entrypoints.api:app \
    --host "${API_HOST:-0.0.0.0}" --port "${API_PORT:-8001}" \
    --workers "${API_WORKERS:-1}" --log-level "${level,,}" --no-proxy-headers --no-access-log \
    --timeout-graceful-shutdown "${API_SHUTDOWN_TIMEOUT:-30}"
}

start_worker() {
  exec celery -A app.entrypoints.worker:celery_app worker \
    "${worker_capacity[@]}" \
    --prefetch-multiplier "$worker_prefetch" --loglevel "${WORKER_LOG_LEVEL:-WARNING}" \
    --without-mingle --without-gossip
}

start_scheduler() {
  exec python -m app.entrypoints.scheduler
}

configure_worker() {
  local capacity
  worker_prefetch=${WORKER_PREFETCH_MULTIPLIER:-1}
  if [[ ! "$worker_prefetch" =~ ^[1-9][0-9]*$ ]]; then
    echo 'WORKER_PREFETCH_MULTIPLIER must be a positive integer.' >&2
    exit 2
  fi
  # The parser only prints fixed option names and validated numbers/pool names.
  capacity=$(python /workspace/deploy/worker_capacity.py) || exit 2
  read -r -a worker_capacity <<< "$capacity"
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
  worker-check) configure_worker ;;
  worker) configure_worker; start_worker ;;
  scheduler) start_scheduler ;;
  *) exec "$@" ;;
esac
