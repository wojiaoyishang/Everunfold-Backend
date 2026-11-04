#!/usr/bin/env bash
# Independent development processes supervised inside the desktop container.
set -euo pipefail
umask 077

backend_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
frontend_root="${EVERUNFOLD_FRONTEND_ROOT:-$(dirname -- "$backend_root")/Everunfold-Frontend}"
runtime_dir="${EVERUNFOLD_RUNTIME_DIR:-$backend_root/var/container}"
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1

case "${1:-}" in
  api|worker|scheduler|frontend|receiver)
    set -a
    source "$backend_root/.env.container.local"
    set +a
    ;;
esac

case "${1:-}" in
  api)
    cd -- "$backend_root"
    exec .venv/bin/python -m uvicorn app.entrypoints.api:app \
      --host 0.0.0.0 --port 8001 --reload --no-proxy-headers --no-access-log
    ;;
  worker)
    cd -- "$backend_root"
    exec .venv/bin/python -m celery -A app.entrypoints.worker:celery_app worker \
      --pool=solo --concurrency=1 --loglevel=WARNING --without-mingle --without-gossip
    ;;
  scheduler)
    cd -- "$backend_root"
    exec .venv/bin/python -m app.entrypoints.scheduler
    ;;
  frontend)
    cd -- "$frontend_root"
    export EVERUNFOLD_PROJECT_ROOT="$frontend_root"
    unset CONFIG_FILE
    exec npm run dev -- --port 3100
    ;;
  receiver)
    cd -- "$frontend_root"
    export EVERUNFOLD_PROJECT_ROOT="$frontend_root"
    unset CONFIG_FILE
    exec npm run settings:receiver
    ;;
  proxy)
    exec /usr/sbin/nginx -p "$runtime_dir/" -c "$runtime_dir/nginx.conf" -g 'daemon off;'
    ;;
  *)
    echo 'Usage: container-service.sh api|worker|scheduler|frontend|receiver|proxy' >&2
    exit 2
    ;;
esac
