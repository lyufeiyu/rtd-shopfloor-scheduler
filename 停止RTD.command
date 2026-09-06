#!/bin/zsh
# RTD 一键停止器。
set -u
ROOT="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$ROOT/storage/rtd.pid"

if [[ ! -f "$PID_FILE" ]]; then
  echo "RTD 当前没有记录运行中的服务。"
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
# 兼容曾经直接运行 server.py、没有写入 PID 文件的旧进程；只匹配本项目的服务路径。
for orphan in ${(f)"$(pgrep -f "$ROOT/backend/server.py" 2>/dev/null || true)"}; do
  [[ "$orphan" == <-> && "$orphan" != "$$" ]] || continue
  kill "$orphan" 2>/dev/null || true
done
rm -f "$PID_FILE"
echo "RTD 已停止。"
read -k 1 "?按任意键退出…"
