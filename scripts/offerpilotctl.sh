#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
echo "[deprecated] scripts/offerpilotctl.sh 已废弃，请改用 scripts/pulsectl.sh"
exec bash "$SCRIPT_DIR/pulsectl.sh" "$@"

