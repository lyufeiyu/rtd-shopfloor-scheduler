#!/bin/zsh
# RTD macOS 一键停止器。
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/storage/rtd-macos.pid"
PORT_FILE="$ROOT/storage/rtd-macos.port"

if [[ ! -f "$PID_FILE" ]]; then
  echo "RTD 当前没有记录由 macOS 启动器启动的服务。"
  read -k 1 "?按任意键退出…"
  exit 0
fi

PID="$(cat "$PID_FILE")"
if [[ "$PID" == <-> ]] && kill -0 "$PID" 2>/dev/null; then
  kill "$PID" 2>/dev/null || true
  for attempt in {1..10}; do
    kill -0 "$PID" 2>/dev/null || break
    sleep 1
  done
fi
rm -f "$PID_FILE"
rm -f "$PORT_FILE"
echo "RTD macOS 服务已停止。"
read -k 1 "?按任意键退出…"
