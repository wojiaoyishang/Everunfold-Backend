#!/usr/bin/env bash
# Workspace shortcut target: start the existing container stack and check readiness.
set -euo pipefail
umask 077

usage() {
  echo '用法：start-everunfold.sh [--check | --help]'
  echo '默认启动测试服务并等待就绪；--check 只检查，不启动或重启服务。'
}

mode=start
case "${1:-}" in
  '') ;;
  --check) mode=check ;;
  --help|-h) usage; exit 0 ;;
  *) usage >&2; exit 2 ;;
esac
if (( $# > 1 )); then usage >&2; exit 2; fi

for command in readlink curl flock supervisorctl supervisord ss node npm; do
  command -v "$command" >/dev/null || { echo "缺少命令：$command" >&2; exit 1; }
done

script_path="$(readlink -f -- "${BASH_SOURCE[0]}")"
backend_root="$(cd -- "$(dirname -- "$script_path")/.." && pwd -P)"
export EVERUNFOLD_FRONTEND_ROOT="${EVERUNFOLD_FRONTEND_ROOT:-$(dirname -- "$backend_root")/Everunfold-Frontend}"
export EVERUNFOLD_RUNTIME_DIR="${EVERUNFOLD_RUNTIME_DIR:-$backend_root/var/container}"
controller="$backend_root/deploy/container-dev.sh"
for required in "$backend_root/config.toml" "$backend_root/.env.container.local" \
  "$EVERUNFOLD_FRONTEND_ROOT/config.toml" "$EVERUNFOLD_RUNTIME_DIR/nginx.conf"; do
  [[ -f "$required" ]] || { echo "缺少运行配置：$required" >&2; exit 1; }
done
[[ -x "$backend_root/.venv/bin/python" ]] || { echo '后端 Python 环境不存在，请先恢复项目依赖。' >&2; exit 1; }
[[ -d "$EVERUNFOLD_FRONTEND_ROOT/node_modules" ]] || { echo '前端依赖不存在，请先恢复项目依赖。' >&2; exit 1; }
startup_timeout="${EVERUNFOLD_START_TIMEOUT:-120}"
if [[ ! "$startup_timeout" =~ ^[1-9][0-9]{0,2}$ ]] || (( startup_timeout > 600 )); then
  echo 'EVERUNFOLD_START_TIMEOUT 必须是 1 至 600 秒的整数。' >&2
  exit 2
fi

mkdir -p -- "$EVERUNFOLD_RUNTIME_DIR"
chmod 700 "$EVERUNFOLD_RUNTIME_DIR"
exec 9>"$EVERUNFOLD_RUNTIME_DIR/start-test.lock"
if ! flock -n 9; then
  echo '已有启动检查正在进行，请稍后重试。' >&2
  exit 1
fi

if [[ "$mode" == start ]]; then
  echo '正在启动 Everunfold 测试服务（已运行的进程保持不变）…'
  # A newly daemonized Supervisor must not inherit this invocation's startup lock.
  if ! "$controller" start 9>&-; then
    echo '服务尚未全部启动，将继续检查就绪状态。' >&2
  fi
fi

services=(api frontend proxy receiver worker scheduler)
ports=(3000 3100 8001 3001)
status=''
listeners=''
ready() {
  local name state rest service port endpoint
  local -A running=()
  status="$("$controller" status 2>&1)" || return 1
  while read -r name state rest; do
    if [[ "$state" == RUNNING ]]; then running["$name"]=1; fi
  done <<< "$status"
  for service in "${services[@]}"; do
    [[ "${running[$service]:-}" == 1 ]] || return 1
  done
  listeners="$(ss -H -ltn)"
  for port in "${ports[@]}"; do
    [[ "$listeners" == *"0.0.0.0:$port "* ]] || return 1
  done
  for endpoint in \
    http://127.0.0.1:8001/health/ready \
    http://127.0.0.1:3000/health/ready \
    http://127.0.0.1:3000/admin; do
    [[ "$(curl --noproxy '*' --silent --output /dev/null --write-out '%{http_code}' \
      --connect-timeout 1 --max-time 3 "$endpoint")" == 200 ]] || return 1
  done
}

deadline=$((SECONDS + startup_timeout))
if [[ "$mode" == start ]]; then
  echo "等待六个进程、0.0.0.0 监听与 HTTP 就绪（等待时限 ${startup_timeout} 秒）…"
else
  echo '检查当前六个进程、0.0.0.0 监听与 HTTP 就绪状态…'
fi
until ready; do
  if [[ "$mode" == check ]] || (( SECONDS >= deadline )); then
    echo '测试服务尚未就绪。' >&2
    printf '%s\n' "$status" >&2
    echo "请检查数据库/Redis连接、四个端口及日志目录：$EVERUNFOLD_RUNTIME_DIR" >&2
    exit 1
  fi
  sleep 2
done

printf '%s\n' "$status"
echo '检查通过：四个端口均监听 0.0.0.0；API、代理健康检查和后台页面均返回 HTTP 200。'
echo '容器内访问：http://127.0.0.1:3000/'
echo '远程访问：使用宿主机已经映射的 3000 端口，保持 HTTP。'
