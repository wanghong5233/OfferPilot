#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
START_SCRIPT="$SCRIPT_DIR/start_backend.sh"
LOG_FILE="/tmp/offerpilot.log"
API_BASE="http://127.0.0.1:8010"

_is_up() {
  curl -sS --max-time 3 "$API_BASE/api/guard/status" >/dev/null 2>&1
}

start() {
  if _is_up; then
    echo "[offerpilot] backend already running"
    status
    return 0
  fi

  echo "[offerpilot] starting backend with env-file loader..."
  nohup bash "$START_SCRIPT" >"$LOG_FILE" 2>&1 </dev/null &
  sleep 3

  if _is_up; then
    echo "[offerpilot] started"
    status
    return 0
  fi

  echo "[offerpilot] start failed, recent log:"
  tail -n 60 "$LOG_FILE" 2>/dev/null || true
  return 1
}

stop() {
  local pids
  pids="$(pgrep -f 'uvicorn app.main:app' || true)"
  if [[ -z "${pids}" ]]; then
    echo "[offerpilot] no uvicorn process found"
    return 0
  fi

  echo "[offerpilot] stopping uvicorn..."
  # shellcheck disable=SC2086
  kill $pids || true
  sleep 1

  if pgrep -f 'uvicorn app.main:app' >/dev/null 2>&1; then
    echo "[offerpilot] force killing remaining uvicorn processes..."
    pkill -9 -f 'uvicorn app.main:app' || true
  fi
  echo "[offerpilot] stopped"
}

restart() {
  stop
  start
}

status() {
  echo "[offerpilot] process:"
  pgrep -af 'uvicorn app.main:app' || echo "  (none)"
  echo "[offerpilot] guard:"
  curl -sS --max-time 5 "$API_BASE/api/guard/status" || echo "SERVICE_DOWN"
}

logs() {
  tail -n "${1:-80}" "$LOG_FILE" 2>/dev/null || echo "[offerpilot] no /tmp/offerpilot.log"
}

guard_logs() {
  tail -n "${1:-120}" "$PROJECT_DIR/backend/logs/guard.log" 2>/dev/null || echo "[offerpilot] no backend/logs/guard.log"
}

profile() {
  curl -sS --max-time 5 "$API_BASE/api/profile?profile_id=default" || echo "SERVICE_DOWN"
}

usage() {
  cat <<EOF
Usage: bash $0 <command>

Commands:
  start        Start backend (loads .env via start_backend.sh)
  stop         Stop backend uvicorn process
  restart      Restart backend
  status       Show process + /api/guard/status
  logs [N]     Tail /tmp/offerpilot.log (default 80 lines)
  guard-logs [N] Tail backend/logs/guard.log (default 120 lines)
  profile      Show /api/profile?profile_id=default
EOF
}

cmd="${1:-status}"
case "$cmd" in
  start) start ;;
  stop) stop ;;
  restart) restart ;;
  status) status ;;
  logs) logs "${2:-80}" ;;
  guard-logs) guard_logs "${2:-120}" ;;
  profile) profile ;;
  *) usage; exit 1 ;;
esac

