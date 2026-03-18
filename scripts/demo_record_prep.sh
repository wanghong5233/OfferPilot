#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8010}"
RUN_SMOKE="${RUN_SMOKE:-1}"
PY_BIN="${PY_BIN:-python}"

echo "[1/4] Checking backend health: ${API_BASE_URL}/health"
if ! curl -sS "${API_BASE_URL}/health" >/tmp/offerpilot_health.json; then
  echo "Backend health request failed."
  exit 1
fi
echo "Health response:"
cat /tmp/offerpilot_health.json

if [[ "${RUN_SMOKE}" == "1" ]]; then
  echo "[2/4] Running backend smoke check"
  (
    cd "${ROOT_DIR}/backend"
    "${PY_BIN}" smoke_check.py
  )
else
  echo "[2/4] Skip smoke check (RUN_SMOKE=${RUN_SMOKE})"
fi

echo "[3/4] Demo key endpoints"
echo "- Frontend: http://127.0.0.1:3000"
echo "- API base: ${API_BASE_URL}"
echo "- Upcoming schedules: ${API_BASE_URL}/api/schedules/upcoming?limit=10&days=14"

echo "[4/4] Ready to record"
echo "Suggested order:"
echo "1) JD analysis -> 2) Material review -> 3) BOSS copilot -> 4) Email heartbeat + schedules -> 5) Intel + Security"
