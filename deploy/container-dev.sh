#!/usr/bin/env bash
# Runtime configuration and credentials must be prepared before starting services.
set -euo pipefail
umask 077

export EVERUNFOLD_BACKEND_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
export EVERUNFOLD_FRONTEND_ROOT="${EVERUNFOLD_FRONTEND_ROOT:-$(dirname -- "$EVERUNFOLD_BACKEND_ROOT")/Everunfold-Frontend}"
export EVERUNFOLD_RUNTIME_DIR="${EVERUNFOLD_RUNTIME_DIR:-$EVERUNFOLD_BACKEND_ROOT/var/container}"
config="$EVERUNFOLD_BACKEND_ROOT/deploy/supervisord.conf"
mkdir -p -- "$EVERUNFOLD_RUNTIME_DIR"
chmod 700 "$EVERUNFOLD_RUNTIME_DIR"

case "${1:-status}" in
  start)
    for required in "$EVERUNFOLD_BACKEND_ROOT/config.toml" \
      "$EVERUNFOLD_BACKEND_ROOT/.env.container.local" \
      "$EVERUNFOLD_FRONTEND_ROOT/config.toml" "$EVERUNFOLD_RUNTIME_DIR/nginx.conf"; do
      test -f "$required" || { echo "Missing runtime file: $required" >&2; exit 1; }
    done
    if ! supervisorctl -c "$config" pid >/dev/null 2>&1; then
      supervisord -c "$config"
    fi
    exec supervisorctl -c "$config" start all
    ;;
  stop)
    exec supervisorctl -c "$config" shutdown
    ;;
  status)
    exec supervisorctl -c "$config" status
    ;;
  restart)
    exec supervisorctl -c "$config" restart "${2:-all}"
    ;;
  update)
    supervisorctl -c "$config" reread
    exec supervisorctl -c "$config" update
    ;;
  *)
    echo 'Usage: container-dev.sh start|stop|status|update|restart [service]' >&2
    exit 2
    ;;
esac
