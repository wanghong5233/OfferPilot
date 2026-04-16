#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"
BACKEND_DIR="$PROJECT_DIR/backend"
VENV_DIR="${PULSE_VENV_DIR:-$PROJECT_DIR/.venv}"

echo "=== Pulse Setup ==="
echo ""

# 1) PostgreSQL
echo "[1/4] PostgreSQL..."
if ! command -v psql >/dev/null 2>&1; then
  sudo apt-get update -qq && sudo apt-get install -y -qq postgresql postgresql-contrib
fi
sudo pg_ctlcluster 16 main start 2>/dev/null || true
"$SCRIPT_DIR/setup-pg.sh"

# 2) Python venv + deps
echo ""
echo "[2/4] Python dependencies..."
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e "$PROJECT_DIR"
if [[ -f "$BACKEND_DIR/requirements.txt" ]]; then
  python -m pip install -r "$BACKEND_DIR/requirements.txt"
fi

# 3) Playwright
echo ""
echo "[3/4] Playwright Chromium..."
python -m playwright install chromium

# 4) Frontend
echo ""
echo "[4/4] Frontend npm dependencies..."
if [[ -f "$FRONTEND_DIR/package.json" ]]; then
  cd "$FRONTEND_DIR"
  npm install
fi

echo ""
echo "=== Setup Complete ==="
echo ""
echo "启动方式（建议三个终端）："
echo "  终端1: cd $PROJECT_DIR && ./scripts/start.sh pg"
echo "  终端2: cd $PROJECT_DIR && ./scripts/start.sh backend"
echo "  终端3: cd $PROJECT_DIR && ./scripts/start.sh frontend"
echo ""
echo "或一键全启动："
echo "  cd $PROJECT_DIR && ./scripts/start.sh all"
