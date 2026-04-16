#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8010}"
RUN_ACCEPTANCE="${RUN_ACCEPTANCE:-0}"
PY_BIN="${PY_BIN:-python}"
HEALTH_FILE="/tmp/pulse_health.json"

echo "[1/4] Checking backend health: ${API_BASE_URL}/health"
if ! curl -sS "${API_BASE_URL}/health" >"$HEALTH_FILE"; then
  echo "Backend health request failed."
  exit 1
fi
echo "Health response:"
cat "$HEALTH_FILE"

if [[ "${RUN_ACCEPTANCE}" == "1" ]]; then
  echo "[2/4] Running MCP acceptance check"
  (
    cd "${ROOT_DIR}"
    "${PY_BIN}" -m backend.mcp_boss_acceptance
  )
else
  echo "[2/4] Skip acceptance check (RUN_ACCEPTANCE=${RUN_ACCEPTANCE})"
fi

echo "[3/4] Demo key endpoints"
echo "- Frontend: http://127.0.0.1:3000"
echo "- API base: ${API_BASE_URL}"
echo "- API docs: ${API_BASE_URL}/docs"
echo "- Brain run: ${API_BASE_URL}/api/brain/run"
echo "- Events recent: ${API_BASE_URL}/api/system/events/recent?limit=20"

echo "[4/4] Ready to record"
echo "Suggested order: Brain -> MCP tools -> boss_greet/boss_chat -> Memory/Evolution -> Events/trace view"
