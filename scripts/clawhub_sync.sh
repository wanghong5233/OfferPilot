#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-dry-run}" # dry-run | publish
BUMP_TYPE="${CLAWHUB_BUMP_TYPE:-patch}" # patch | minor | major
CHANGELOG="${CLAWHUB_CHANGELOG:-OfferPilot phase-6 update}"
TAGS="${CLAWHUB_TAGS:-latest}"
FORCE_FALLBACK_SKILLS="${CLAWHUB_FORCE_FALLBACK_SKILLS:-interview-prep}"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$ROOT_DIR/skills"

SKILLS=(
  "job-monitor"
  "resume-tailor"
  "boss-chat-copilot"
  "application-tracker"
  "email-reader"
  "company-intel"
  "interview-prep"
)

if [[ "$MODE" != "dry-run" && "$MODE" != "publish" ]]; then
  echo "Usage: $0 [dry-run|publish]"
  exit 1
fi

if [[ "$BUMP_TYPE" != "patch" && "$BUMP_TYPE" != "minor" && "$BUMP_TYPE" != "major" ]]; then
  echo "Invalid CLAWHUB_BUMP_TYPE=$BUMP_TYPE (expected patch|minor|major)"
  exit 1
fi

if [[ ! -d "$SKILLS_DIR" ]]; then
  echo "skills directory not found: $SKILLS_DIR"
  exit 1
fi

if [[ -f "/root/.nvm/nvm.sh" ]]; then
  # shellcheck disable=SC1091
  source "/root/.nvm/nvm.sh"
  nvm use 22 >/dev/null 2>&1 || true
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "npx not found in PATH. Install Node.js (nvm use 22) first."
  exit 1
fi

clawhub() {
  npx -y clawhub "$@"
}

_sanitize_handle() {
  local raw="$1"
  local line
  local tail=""
  local handle
  local out=""
  local i
  local ch

  while IFS= read -r line; do
    tail="$line"
  done <<<"$raw"

  handle="${tail##* }"
  handle="${handle#@}"

  for ((i = 0; i < ${#handle}; i++)); do
    ch="${handle:i:1}"
    case "$ch" in
      [a-zA-Z0-9_-]) out+="$ch" ;;
      *) ;;
    esac
  done
  printf "%s" "$out"
}

_line_value() {
  local text="$1"
  local key="$2"
  local line
  while IFS= read -r line; do
    if [[ "$line" == "$key:"* ]]; then
      printf "%s" "${line#${key}: }"
      return 0
    fi
  done <<<"$text"
  printf ""
}

_bump_semver() {
  local current="$1"
  local mode="$2"
  local major minor patch
  if [[ ! "$current" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    printf "1.0.0"
    return 0
  fi
  IFS="." read -r major minor patch <<<"$current"
  case "$mode" in
    major)
      printf "%s.0.0" "$((major + 1))"
      ;;
    minor)
      printf "%s.%s.0" "$major" "$((minor + 1))"
      ;;
    *)
      printf "%s.%s.%s" "$major" "$minor" "$((patch + 1))"
      ;;
  esac
}

_inspect_slug() {
  local slug="$1"
  local output owner latest
  if ! output="$(clawhub inspect "$slug" 2>/dev/null)"; then
    printf "not_found||"
    return 0
  fi
  owner="$(_line_value "$output" "Owner")"
  latest="$(_line_value "$output" "Latest")"
  printf "found|%s|%s" "$owner" "$latest"
}

_remote_skill_text() {
  local slug="$1"
  local text
  if ! text="$(clawhub inspect "$slug" --tag latest --file SKILL.md 2>/dev/null)"; then
    return 1
  fi
  text="${text#- Fetching skill$'\n'}"
  printf "%s" "$text"
}

_skill_content_changed() {
  local slug="$1"
  local skill_file="$2"
  local local_text
  local remote_text

  local_text="$(<"$skill_file")"
  if ! remote_text="$(_remote_skill_text "$slug")"; then
    return 0
  fi

  if [[ "$local_text" == "$remote_text" ]]; then
    return 1
  fi
  return 0
}

_skill_forced_fallback() {
  local skill="$1"
  local csv
  csv=",${FORCE_FALLBACK_SKILLS// /},"
  [[ "$csv" == *",$skill,"* ]]
}

_resolve_publish_target() {
  local skill="$1"
  local skill_file="$2"
  local primary_slug="$skill"
  local fallback_slug="${FALLBACK_PREFIX}-${skill}"
  local info state owner latest version

  if _skill_forced_fallback "$skill"; then
    _resolve_fallback_target "$skill" "$skill_file"
    return 0
  fi

  info="$(_inspect_slug "$primary_slug")"
  IFS="|" read -r state owner latest <<<"$info"
  if [[ "$state" == "not_found" ]]; then
    printf "%s|1.0.0|NEW|primary slug available" "$primary_slug"
    return 0
  fi
  if [[ "$owner" == "$OWNER_HANDLE" ]]; then
    if ! _skill_content_changed "$primary_slug" "$skill_file"; then
      printf "%s|%s|SKIP|already synced" "$primary_slug" "$latest"
      return 0
    fi
    version="$(_bump_semver "$latest" "$BUMP_TYPE")"
    printf "%s|%s|UPDATE|primary slug owned by you (%s)" "$primary_slug" "$version" "$latest"
    return 0
  fi

  info="$(_inspect_slug "$fallback_slug")"
  IFS="|" read -r state owner latest <<<"$info"
  if [[ "$state" == "not_found" ]]; then
    printf "%s|1.0.0|NEW|primary occupied; use fallback prefix" "$fallback_slug"
    return 0
  fi
  if [[ "$owner" == "$OWNER_HANDLE" ]]; then
    if ! _skill_content_changed "$fallback_slug" "$skill_file"; then
      printf "%s|%s|SKIP|fallback slug already synced" "$fallback_slug" "$latest"
      return 0
    fi
    version="$(_bump_semver "$latest" "$BUMP_TYPE")"
    printf "%s|%s|UPDATE|fallback slug owned by you (%s)" "$fallback_slug" "$version" "$latest"
    return 0
  fi

  echo "Both slugs are occupied by other owners for skill=$skill" >&2
  echo "  primary:  $primary_slug" >&2
  echo "  fallback: $fallback_slug" >&2
  return 1
}

_resolve_fallback_target() {
  local skill="$1"
  local skill_file="$2"
  local fallback_slug="${FALLBACK_PREFIX}-${skill}"
  local info state owner latest version

  info="$(_inspect_slug "$fallback_slug")"
  IFS="|" read -r state owner latest <<<"$info"
  if [[ "$state" == "not_found" ]]; then
    printf "%s|1.0.0|NEW|fallback slug available" "$fallback_slug"
    return 0
  fi
  if [[ "$owner" == "$OWNER_HANDLE" ]]; then
    if ! _skill_content_changed "$fallback_slug" "$skill_file"; then
      printf "%s|%s|SKIP|fallback slug already synced" "$fallback_slug" "$latest"
      return 0
    fi
    version="$(_bump_semver "$latest" "$BUMP_TYPE")"
    printf "%s|%s|UPDATE|fallback slug owned by you (%s)" "$fallback_slug" "$version" "$latest"
    return 0
  fi
  echo "Fallback slug occupied by another owner: $fallback_slug" >&2
  return 1
}

_publish_skill() {
  local skill_path="$1"
  local slug="$2"
  local version="$3"
  local output
  local lowered
  if output="$(clawhub publish \
    "$skill_path" \
    --slug "$slug" \
    --version "$version" \
    --changelog "$CHANGELOG" \
    --tags "$TAGS" 2>&1)"; then
    echo "$output"
    return 0
  fi
  echo "$output" >&2
  lowered="${output,,}"
  if [[ "$lowered" == *"rate limit"* && "$lowered" == *"new skills per hour"* ]]; then
    return 40
  fi
  return 1
}

echo "Running local skill preflight..."
"$ROOT_DIR/scripts/skills_release_prep.sh"

echo ""
echo "Checking ClawHub auth..."
WHOAMI_OUTPUT="$(clawhub whoami 2>&1)" || {
  echo "Not logged in yet. Run: npx clawhub login"
  exit 1
}
echo "$WHOAMI_OUTPUT"

OWNER_HANDLE="$(_sanitize_handle "$WHOAMI_OUTPUT")"
if [[ -z "$OWNER_HANDLE" ]]; then
  echo "Failed to parse ClawHub handle from whoami output."
  exit 1
fi

FALLBACK_PREFIX="${CLAWHUB_FALLBACK_PREFIX:-${OWNER_HANDLE}-offerpilot}"
echo "Using fallback slug prefix: $FALLBACK_PREFIX"
echo "Version bump mode: $BUMP_TYPE"
echo "Force fallback skills: $FORCE_FALLBACK_SKILLS"

echo ""
echo "Resolving skill publish targets..."

declare -a PLAN=()
for skill in "${SKILLS[@]}"; do
  skill_path="$SKILLS_DIR/$skill"
  skill_file="$skill_path/SKILL.md"
  if [[ ! -f "$skill_file" ]]; then
    echo "Missing skill file: $skill_file"
    exit 1
  fi
  resolved="$(_resolve_publish_target "$skill" "$skill_file")"
  PLAN+=("$skill|$skill_path|$resolved")
done

if [[ "$MODE" == "dry-run" ]]; then
  echo "Dry-run only (no publish)..."
  for item in "${PLAN[@]}"; do
    IFS="|" read -r skill _path slug version action note <<<"$item"
    echo "- $skill -> $slug  $action  version=$version  ($note)"
  done
  echo "Dry-run completed."
  exit 0
fi

echo "Publishing skills..."
for item in "${PLAN[@]}"; do
  IFS="|" read -r skill skill_path slug version action note <<<"$item"
  if [[ "$action" == "SKIP" ]]; then
    echo ""
    echo "[$skill] SKIP -> slug=$slug ($note)"
    continue
  fi
  echo ""
  echo "[$skill] $action -> slug=$slug version=$version"
  if _publish_skill "$skill_path" "$slug" "$version"; then
    publish_status=0
  else
    publish_status=$?
  fi
  if [[ "$publish_status" -eq 0 ]]; then
    continue
  fi
  if [[ "$publish_status" -eq 40 ]]; then
    echo "ClawHub rate limit reached (max 5 new skills/hour)." >&2
    echo "Please rerun later: ./scripts/clawhub_sync.sh publish" >&2
    exit 2
  fi

  if [[ "$action" == "NEW" && "$slug" == "$skill" ]]; then
    echo "Primary slug failed for $skill. Retrying with fallback prefix..."
    retry="$(_resolve_fallback_target "$skill" "$skill_path/SKILL.md")"
    IFS="|" read -r retry_slug retry_version retry_action retry_note <<<"$retry"
    if [[ "$retry_action" == "SKIP" ]]; then
      echo "[$skill] SKIP -> slug=$retry_slug ($retry_note)"
      continue
    fi
    echo "[$skill] $retry_action -> slug=$retry_slug version=$retry_version ($retry_note)"
    if _publish_skill "$skill_path" "$retry_slug" "$retry_version"; then
      publish_status=0
    else
      publish_status=$?
    fi
    if [[ "$publish_status" -eq 0 ]]; then
      continue
    fi
    if [[ "$publish_status" -eq 40 ]]; then
      echo "ClawHub rate limit reached (max 5 new skills/hour)." >&2
      echo "Please rerun later: ./scripts/clawhub_sync.sh publish" >&2
      exit 2
    fi
    continue
  fi

  echo "Publish failed and no fallback path was applicable for skill=$skill" >&2
  exit 1
done

echo ""
echo "Publish completed."
