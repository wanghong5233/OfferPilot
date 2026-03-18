#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-install}"

EMAIL_JOB_NAME="${OFP_EMAIL_JOB_NAME:-offerpilot-email-daily-heartbeat}"
BOSS_JOB_NAME="${OFP_BOSS_JOB_NAME:-offerpilot-boss-daily-scan}"
BOSS_CHAT_JOB_NAME="${OFP_BOSS_CHAT_JOB_NAME:-offerpilot-boss-chat-copilot}"
HOURLY_JOB_NAME="${OFP_HOURLY_JOB_NAME:-offerpilot-hourly-heartbeat-test}"

TZ_NAME="${OFP_TZ:-}"
TZ_SOURCE="env"
EMAIL_CRON="${OFP_EMAIL_CRON:-0 9 * * *}"
BOSS_CRON="${OFP_BOSS_CRON:-5 9 * * *}"
BOSS_CHAT_CRON="${OFP_BOSS_CHAT_CRON:-*/10 10-12,15-17 * * 1-5}"
HOURLY_EVERY="${OFP_HOURLY_EVERY:-1h}"

ENABLE_BOSS_CRON="${OFP_ENABLE_BOSS_CRON:-0}"
ENABLE_BOSS_CHAT_CRON="${OFP_ENABLE_BOSS_CHAT_CRON:-1}"
ENABLE_HOURLY_TEST="${OFP_ENABLE_HOURLY_TEST:-1}"

BOSS_KEYWORD="${OFP_BOSS_KEYWORD:-AI Agent 实习}"

EMAIL_MESSAGE="${OFP_EMAIL_MESSAGE:-Use email-reader skill to trigger one email heartbeat run, then summarize fetched_count, processed_count, interview invites, schedule_reminders, and notification status.}"
BOSS_MESSAGE="${OFP_BOSS_MESSAGE:-Use job-monitor skill to search BOSS with keyword ${BOSS_KEYWORD}, then summarize top 3 jobs by match score.}"
BOSS_CHAT_MESSAGE="${OFP_BOSS_CHAT_MESSAGE:-Use boss-chat-copilot skill to trigger backend endpoint /api/boss/chat/heartbeat/trigger for unread conversations, then report summary and top manual-intervention items.}"
HOURLY_MESSAGE="${OFP_HOURLY_MESSAGE:-Use email-reader skill to query heartbeat status and report running state plus last_error.}"

pick_python() {
  if [[ -n "${OFP_PYTHON_BIN:-}" ]]; then
    echo "${OFP_PYTHON_BIN}"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return 0
  fi
  echo "No python interpreter found (need python3 or python)." >&2
  exit 1
}

require_openclaw() {
  if ! command -v openclaw >/dev/null 2>&1 && [[ -f "/root/.nvm/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    source "/root/.nvm/nvm.sh"
    nvm use 22 >/dev/null 2>&1 || true
  fi
  if ! command -v openclaw >/dev/null 2>&1; then
    echo "openclaw command not found in PATH."
    exit 1
  fi
}

detect_local_tz() {
  local tz=""
  if command -v timedatectl >/dev/null 2>&1; then
    tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    if [[ -n "$tz" && "$tz" != "n/a" ]]; then
      echo "$tz"
      return
    fi
  fi
  if [[ -f "/etc/timezone" ]]; then
    tz="$(tr -d '[:space:]' < /etc/timezone)"
    if [[ "$tz" == */* ]]; then
      echo "$tz"
      return
    fi
  fi
  if [[ -L "/etc/localtime" ]]; then
    local target
    target="$(readlink -f /etc/localtime 2>/dev/null || true)"
    if [[ "$target" == *"/zoneinfo/"* ]]; then
      echo "${target##*/zoneinfo/}"
      return
    fi
  fi
  echo "Asia/Shanghai"
}

resolve_timezone() {
  if [[ -n "$TZ_NAME" ]]; then
    TZ_SOURCE="env"
    return
  fi
  TZ_NAME="$(detect_local_tz)"
  TZ_SOURCE="local"
}

job_id_by_name() {
  local job_name="$1"
  local py_bin
  py_bin="$(pick_python)"
  openclaw cron list --all --json | "$py_bin" -c '
import json
import sys

target = sys.argv[1]
data = json.load(sys.stdin)
for job in data.get("jobs", []):
    if str(job.get("name") or "") == target:
        print(str(job.get("id") or ""))
        break
' "$job_name"
}

remove_job_if_exists() {
  local job_name="$1"
  local job_id
  job_id="$(job_id_by_name "$job_name")"
  if [[ -n "$job_id" ]]; then
    echo "Removing existing job: $job_name ($job_id)"
    openclaw cron rm "$job_id" --json >/dev/null
  fi
}

add_cron_job() {
  local name="$1"
  local cron_expr="$2"
  local message="$3"
  echo "Adding cron job: $name [$cron_expr $TZ_NAME]"
  openclaw cron add \
    --name "$name" \
    --cron "$cron_expr" \
    --tz "$TZ_NAME" \
    --message "$message" \
    --announce \
    --channel last \
    --best-effort-deliver \
    --json
}

add_every_job() {
  local name="$1"
  local every_expr="$2"
  local message="$3"
  echo "Adding interval job: $name [every $every_expr]"
  openclaw cron add \
    --name "$name" \
    --every "$every_expr" \
    --message "$message" \
    --announce \
    --channel last \
    --best-effort-deliver \
    --json
}

print_status() {
  echo "Local system time: $(date '+%Y-%m-%d %H:%M:%S %Z%z')"
  echo "Cron timezone: $TZ_NAME (source=$TZ_SOURCE)"
  openclaw system heartbeat last --json || true
  openclaw cron list --all --json
}

install_jobs() {
  echo "Enabling OpenClaw system heartbeat..."
  openclaw system heartbeat enable --json >/dev/null || true

  remove_job_if_exists "$EMAIL_JOB_NAME"
  add_cron_job "$EMAIL_JOB_NAME" "$EMAIL_CRON" "$EMAIL_MESSAGE"

  if [[ "$ENABLE_BOSS_CRON" == "1" ]]; then
    remove_job_if_exists "$BOSS_JOB_NAME"
    add_cron_job "$BOSS_JOB_NAME" "$BOSS_CRON" "$BOSS_MESSAGE"
  fi

  if [[ "$ENABLE_BOSS_CHAT_CRON" == "1" ]]; then
    remove_job_if_exists "$BOSS_CHAT_JOB_NAME"
    add_cron_job "$BOSS_CHAT_JOB_NAME" "$BOSS_CHAT_CRON" "$BOSS_CHAT_MESSAGE"
  fi

  if [[ "$ENABLE_HOURLY_TEST" == "1" ]]; then
    remove_job_if_exists "$HOURLY_JOB_NAME"
    add_every_job "$HOURLY_JOB_NAME" "$HOURLY_EVERY" "$HOURLY_MESSAGE"
  fi

  echo ""
  echo "OpenClaw heartbeat jobs configured."
  print_status
}

remove_jobs() {
  remove_job_if_exists "$EMAIL_JOB_NAME"
  remove_job_if_exists "$BOSS_JOB_NAME"
  remove_job_if_exists "$BOSS_CHAT_JOB_NAME"
  remove_job_if_exists "$HOURLY_JOB_NAME"
  echo "Removed configured OfferPilot cron jobs."
  print_status
}

run_now() {
  local target_name="${2:-$EMAIL_JOB_NAME}"
  local job_id
  job_id="$(job_id_by_name "$target_name")"
  if [[ -z "$job_id" ]]; then
    echo "Job not found by name: $target_name"
    exit 1
  fi
  echo "Running cron job now: $target_name ($job_id)"
  openclaw cron run "$job_id"
}

main() {
  require_openclaw
  resolve_timezone
  case "$ACTION" in
    install)
      install_jobs
      ;;
    remove)
      remove_jobs
      ;;
    status)
      print_status
      ;;
    run-now)
      run_now "$@"
      ;;
    *)
      echo "Usage: $0 [install|remove|status|run-now <job-name>]"
      exit 1
      ;;
  esac
}

main "$@"
