#!/usr/bin/env bash
set -e

cd /mnt/e/0找工作/0大模型全栈知识库/OfferPilot/backend
source .venv/bin/activate

# 加载 .env（跳过注释和空行，容忍中文注释）
while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] && export "$line" 2>/dev/null || true
done < ../.env

echo "=== PRODUCTION_GUARD_ENABLED=$PRODUCTION_GUARD_ENABLED ==="
echo "=== GUARD_TIMEZONE=$GUARD_TIMEZONE ==="
echo "=== 启动后端 (uvicorn --reload) ==="

exec uvicorn app.main:app --host 0.0.0.0 --port 8010 --reload
