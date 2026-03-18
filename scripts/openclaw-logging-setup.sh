#!/usr/bin/env bash
set -euo pipefail

# OpenClaw 结构化日志与可观测性配置脚本
# 用途：配置 openclaw-gateway 输出结构化 JSON 日志到文件，便于审计与排查。

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
OPENCLAW_CONFIG="${PROJECT_DIR}/openclaw.json"
LOG_DIR="${PROJECT_DIR}/logs/openclaw"

mkdir -p "$LOG_DIR"

echo "=== OpenClaw 可观测性配置 ==="
echo ""

if [[ ! -f "$OPENCLAW_CONFIG" ]]; then
  echo "[WARN] $OPENCLAW_CONFIG 不存在，跳过 openclaw.json 配置。"
  echo "       请确保 openclaw-gateway 已初始化。"
else
  echo "[1/3] 检查 openclaw.json 日志配置..."

  HAS_LOGGING=$(python3 -c "
import json, sys
with open('$OPENCLAW_CONFIG') as f:
    cfg = json.load(f)
if 'logging' not in cfg:
    cfg['logging'] = {
        'level': 'info',
        'format': 'json',
        'file': '$LOG_DIR/openclaw.log',
        'max_size_mb': 50,
        'max_backups': 5
    }
    with open('$OPENCLAW_CONFIG', 'w') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print('ADDED')
else:
    print('EXISTS')
" 2>/dev/null || echo "ERROR")

  case "$HAS_LOGGING" in
    ADDED)
      echo "  已添加 structured logging 配置到 openclaw.json"
      ;;
    EXISTS)
      echo "  openclaw.json 已有 logging 配置，跳过"
      ;;
    *)
      echo "  [WARN] 无法解析 openclaw.json，请手动配置 logging 字段"
      ;;
  esac
fi

echo ""
echo "[2/3] 检查 diagnostics-otel 可选依赖..."
if command -v openclaw-gateway >/dev/null 2>&1; then
  echo "  openclaw-gateway 已安装"
  if pip show diagnostics-otel >/dev/null 2>&1; then
    echo "  diagnostics-otel 已安装"
  else
    echo "  diagnostics-otel 未安装（可选）"
    echo "  安装命令: pip install diagnostics-otel"
  fi
else
  echo "  [INFO] openclaw-gateway 未在 PATH 中找到，请确认 OpenClaw 已安装。"
fi

echo ""
echo "[3/3] OpenTelemetry 环境变量配置提示："
echo "  若需启用 OTEL 追踪，请在 .env 中设置："
echo "    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318"
echo "    OTEL_SERVICE_NAME=offerpilot-openclaw"
echo ""
echo "日志目录: $LOG_DIR"
echo "=== 配置完成 ==="
