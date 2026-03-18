#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="$ROOT_DIR/skills"
TARGET_DIR="${OPENCLAW_SKILLS_DIR:-$HOME/.openclaw/workspace/skills}"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Source skills dir not found: $SOURCE_DIR"
  exit 1
fi

mkdir -p "$TARGET_DIR"
echo "Sync skills from: $SOURCE_DIR"
echo "Sync skills to:   $TARGET_DIR"

for skill_path in "$SOURCE_DIR"/*; do
  [[ -d "$skill_path" ]] || continue
  skill_name="$(basename "$skill_path")"
  if [[ ! -f "$skill_path/SKILL.md" ]]; then
    echo "Skip $skill_name (missing SKILL.md)"
    continue
  fi
  rm -rf "$TARGET_DIR/$skill_name"
  cp -R "$skill_path" "$TARGET_DIR/$skill_name"
  echo "  synced: $skill_name"
done

if ! command -v openclaw >/dev/null 2>&1 && [[ -f "/root/.nvm/nvm.sh" ]]; then
  # shellcheck disable=SC1091
  source "/root/.nvm/nvm.sh"
  nvm use 22 >/dev/null 2>&1 || true
fi

if command -v openclaw >/dev/null 2>&1; then
  echo ""
  echo "OpenClaw skills list (post-sync):"
  openclaw skills list || true
else
  echo ""
  echo "openclaw command not found; sync completed without CLI verification."
fi
