#!/bin/zsh
# RTD 一键启动器：双击后启动本地服务并打开浏览器。
set -u

ROOT="$(cd "$(dirname "$0")" && pwd)"
STORAGE="$ROOT/storage"
PID_FILE="$STORAGE/rtd.pid"
PORT="${RTD_PORT:-8765}"
SERVICE_PY="${RTD_SERVICE_PYTHON:-/Users/feiyulv/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python}"
ALGORITHM_PY="${RTD_ALGORITHM_PYTHON:-/Users/feiyulv/miniconda3/bin/python}"

mkdir -p "$STORAGE/logs"

if [[ ! -x "$SERVICE_PY" ]]; then
  SERVICE_PY="$(command -v python3 || true)"
fi
if [[ ! -x "$ALGORITHM_PY" ]]; then
  ALGORITHM_PY="$SERVICE_PY"
fi
if [[ -z "$SERVICE_PY" || ! -x "$SERVICE_PY" ]]; then
  echo "找不到 Python。请安装 Python 3，或设置 RTD_SERVICE_PYTHON。"
  read -k 1 "?按任意键退出…"
  exit 1
fi

# 已经运行时直接打开现有服务，不重复启动。
if curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
  open "http://127.0.0.1:$PORT"
  exit 0
fi

# 如果默认端口被其他程序占用，顺延到下一个端口。
while lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
  PORT=$((PORT + 1))
done

export RTD_ALGORITHM_PYTHON="$ALGORITHM_PY"
nohup "$SERVICE_PY" "$ROOT/backend/server.py" --port "$PORT" --storage "$STORAGE" \
  >> "$STORAGE/logs/launcher.log" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$PID_FILE"

for attempt in {1..30}; do
  if curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    open "http://127.0.0.1:$PORT"
    echo "RTD 已启动：http://127.0.0.1:$PORT"
    echo "关闭服务请双击：停止RTD.command"
    exit 0
  fi
  if ! kill -0 "$SERVER_PID" 2>/dev/null; then
    echo "RTD 启动失败，请查看：$STORAGE/logs/launcher.log"
    read -k 1 "?按任意键退出…"
    exit 1
  fi
  sleep 1
done

echo "RTD 启动超时，请查看：$STORAGE/logs/launcher.log"
exit 1
