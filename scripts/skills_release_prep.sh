#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$ROOT_DIR/skills"

REQUIRED_SKILLS=(
  "job-monitor"
  "resume-tailor"
  "boss-chat-copilot"
  "application-tracker"
  "email-reader"
  "company-intel"
  "interview-prep"
)

echo "Checking skill files under: $SKILLS_DIR"
missing=0
for skill in "${REQUIRED_SKILLS[@]}"; do
  path="$SKILLS_DIR/$skill/SKILL.md"
  if [[ -f "$path" ]]; then
    echo "  OK  $skill"
  else
    echo "  MISSING  $skill -> $path"
    missing=1
  fi
done

if [[ "$missing" -ne 0 ]]; then
  echo ""
  echo "Skill preflight failed: missing SKILL.md files."
  exit 1
fi

if ! command -v openclaw >/dev/null 2>&1 && [[ -f "/root/.nvm/nvm.sh" ]]; then
  # shellcheck disable=SC1091
  source "/root/.nvm/nvm.sh"
  nvm use 22 >/dev/null 2>&1 || true
fi

if command -v openclaw >/dev/null 2>&1; then
  echo ""
  echo "OpenClaw skills check:"
  openclaw skills check || true
else
  echo ""
  echo "openclaw not found in PATH; skipped CLI checks."
fi

echo ""
echo "Preflight completed."
echo "Next: publish/update skills in ClawHub manually or via your release workflow."
