#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"
ENV_FILE="$PROJECT_DIR/.env"

BACKEND_PID=""
FRONTEND_PID=""
MONITOR_PID=""

cleanup() {
  echo ""
  echo "Stopping all services..."
  [[ -n "$MONITOR_PID" ]]  && kill "$MONITOR_PID" 2>/dev/null || true
  [[ -n "$FRONTEND_PID" ]] && kill "$FRONTEND_PID" 2>/dev/null || true
  [[ -n "$BACKEND_PID" ]]  && kill "$BACKEND_PID"  2>/dev/null || true
  wait 2>/dev/null
  echo "All stopped."
}
trap cleanup EXIT INT TERM

load_env() {
  [[ -f "$ENV_FILE" ]] || return 0
  set -a
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" ]] && continue
    [[ "$line" != *=* ]] && continue
    eval "export $line"
  done < "$ENV_FILE"
  set +a
}

# ---------- individual starters ----------

start_pg() {
  if command -v pg_ctlcluster >/dev/null 2>&1; then
    echo "[PG] Starting PostgreSQL cluster..."
    sudo pg_ctlcluster 16 main start 2>/dev/null || true
    echo "[PG] Ready (expected port: 5432)"
  else
    echo "[PG] Skip: pg_ctlcluster not found."
  fi
}

run_backend() {
  load_env
  bash "$SCRIPT_DIR/start_backend.sh" 2>&1 | while IFS= read -r l; do echo "[BE] $l"; done
}

run_frontend() {
  if [[ ! -f "$FRONTEND_DIR/package.json" ]]; then
    echo "[FE] Skip: frontend package.json not found."
    return 0
  fi
  cd "$FRONTEND_DIR"
  npm run dev -- --hostname 0.0.0.0 --port 3000 2>&1 | while IFS= read -r l; do echo "[FE] $l"; done
}

http_code() {
  local url="$1"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "$url" || true)"
  if [[ -n "$code" ]]; then
    echo "$code"
  else
    echo "DOWN"
  fi
}

wait_ready() {
  local be_code fe_code i
  echo "[SYS] Waiting for backend/frontend readiness..."
  for i in $(seq 1 120); do
    be_code="$(http_code "http://127.0.0.1:8010/health")"
    fe_code="$(http_code "http://127.0.0.1:3000")"
    if [[ "$be_code" == "200" ]]; then
      echo "[SYS] Backend ready: backend=200 frontend=$fe_code"
      return 0
    fi
    if (( i % 5 == 0 )); then
      echo "[SYS] Still starting... backend=$be_code frontend=$fe_code (elapsed ${i}s)"
    fi
    sleep 1
  done
  echo "[SYS] Startup timeout. backend=$be_code frontend=$fe_code"
  return 1
}

monitor_loop() {
  local be_code fe_code ts
  while true; do
    be_code="$(http_code "http://127.0.0.1:8010/docs")"
    fe_code="$(http_code "http://127.0.0.1:3000")"
    ts="$(date '+%H:%M:%S')"
    echo "[SYS] $ts idle, waiting requests | backend=$be_code frontend=$fe_code"
    sleep 20
  done
}

# ---------- main ----------

ACTION="${1:-all}"

case "$ACTION" in
  pg)
    start_pg
    ;;
  backend)
    exec bash "$SCRIPT_DIR/start_backend.sh"
    ;;
  frontend)
    echo "=== Frontend (port 3000) ==="
    cd "$FRONTEND_DIR"
    exec npm run dev -- --hostname 0.0.0.0 --port 3000
    ;;
  all)
    load_env
    echo ""
    echo "=========================================="
    echo "  Pulse — One command startup"
    echo "  Backend:  http://127.0.0.1:8010/docs"
    echo "  Frontend: http://127.0.0.1:3000"
    echo "  PULSE_ENVIRONMENT = ${PULSE_ENVIRONMENT:-dev}"
    echo "  Ctrl+C 停止所有服务"
    echo "=========================================="
    echo ""

    start_pg

    run_backend &
    BACKEND_PID=$!

    run_frontend &
    FRONTEND_PID=$!

    wait_ready || true
    monitor_loop &
    MONITOR_PID=$!

    wait
    ;;
  *)
    echo "Usage: $0 [all|pg|backend|frontend]"
    exit 1
    ;;
esac
