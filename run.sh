#!/usr/bin/env bash
# 启动码流分析工具后端 + Web 前端。
# 用法: ./run.sh [端口]   默认 8731
set -e
cd "$(dirname "$0")"

PORT="${1:-8731}"
HOST="${HOST:-127.0.0.1}"

# 可选：若存在 venv 则激活
if [ -d "venv" ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

# 依赖自检
python3 - <<'PY' || { echo "缺少依赖，请先: pip install -r requirements.txt"; exit 1; }
import importlib, sys
for m in ("fastapi", "uvicorn", "pydantic"):
    importlib.import_module(m)
PY

echo "启动 http://${HOST}:${PORT}  (Ctrl+C 停止)"
exec python3 -m uvicorn app.main:app --host "${HOST}" --port "${PORT}"
