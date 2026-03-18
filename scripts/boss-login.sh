#!/usr/bin/env bash
set -euo pipefail

# BOSS 直聘登录脚本
#
# 关键设计：登录时不使用 Playwright，而是直接启动原生 Chrome。
# 原因：BOSS 直聘检测 Playwright 的 CDP 协议连接并重定向到 about:blank。
# 任何通过 Playwright 控制的浏览器（包括真 Chrome）都会被检测。
# 直接启动 Chrome 则完全不触发反爬。
# Cookie 保存在 persistent profile 目录，后续 Playwright 自动化复用同一 profile。

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
ENV_FILE="$PROJECT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
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
fi

PROFILE_DIR="${BOSS_BROWSER_PROFILE_DIR:-$BACKEND_DIR/.playwright/boss}"
if [[ "$PROFILE_DIR" != /* ]]; then
  PROFILE_DIR="$PROJECT_DIR/$PROFILE_DIR"
fi
mkdir -p "$PROFILE_DIR"

LOGIN_URL="https://www.zhipin.com/web/user/?ka=header-login"

# Remove stale lock files from previous sessions
rm -f "$PROFILE_DIR/SingletonLock" "$PROFILE_DIR/SingletonSocket" "$PROFILE_DIR/SingletonCookie" 2>/dev/null || true

echo ""
echo "=========================================="
echo "  BOSS 直聘 — 首次登录"
echo "=========================================="
echo ""
echo "即将打开 Chrome 浏览器。"
echo "请用手机扫码登录，登录成功后关闭浏览器窗口。"
echo "Cookie 会自动保存到: $PROFILE_DIR"
echo ""

# Detect which Chrome binary is available
CHROME_BIN=""
for candidate in google-chrome google-chrome-stable chromium-browser chromium; do
  if command -v "$candidate" &>/dev/null; then
    CHROME_BIN="$candidate"
    break
  fi
done

if [[ -z "$CHROME_BIN" ]]; then
  echo "ERROR: 未找到 Chrome/Chromium。请安装："
  echo "  sudo apt-get install -y google-chrome-stable"
  echo "  或  sudo apt-get install -y chromium-browser"
  exit 1
fi

echo "[LOGIN] 使用浏览器: $CHROME_BIN"
echo "[LOGIN] Profile: $PROFILE_DIR"
echo ""

# Launch Chrome directly (NO Playwright, NO CDP — bypasses anti-bot detection)
"$CHROME_BIN" \
  --user-data-dir="$PROFILE_DIR" \
  --no-first-run \
  --no-default-browser-check \
  --no-sandbox \
  "$LOGIN_URL"

echo ""
echo "[LOGIN] 浏览器已关闭，Cookie 已保存！"
echo ""
