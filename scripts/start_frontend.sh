#!/usr/bin/env bash
# 前端 watchdog：Next.js dev server 崩溃后自动重启
# 用法: cd Pulse/frontend && bash ../scripts/start_frontend.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../frontend" && pwd)"
PORT=3000
LOCK_FILE="$FRONTEND_DIR/.next/dev/lock"
MAX_RESTARTS=50
RESTART_DELAY=5

cd "$FRONTEND_DIR"

cleanup() {
    echo "[watchdog] Cleaning up..."
    lsof -ti:$PORT 2>/dev/null | xargs kill -9 2>/dev/null || true
    rm -f "$LOCK_FILE"
}

trap cleanup EXIT

restart_count=0

while [ $restart_count -lt $MAX_RESTARTS ]; do
    cleanup
    sleep 2

    echo "[watchdog] Starting Next.js dev server --webpack (attempt $((restart_count + 1))/$MAX_RESTARTS)..."

    npm run dev 2>&1
    exit_code=$?

    restart_count=$((restart_count + 1))

    if [ $exit_code -eq 0 ] || [ $exit_code -eq 130 ] || [ $exit_code -eq 143 ]; then
        echo "[watchdog] Frontend exited normally (code=$exit_code), not restarting."
        break
    fi

    echo "[watchdog] Frontend crashed (exit_code=$exit_code), restarting in ${RESTART_DELAY}s..."
    sleep $RESTART_DELAY
done

echo "[watchdog] Frontend watchdog stopped after $restart_count restarts."
