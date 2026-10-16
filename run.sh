#!/usr/bin/env bash
# 启动 WorkBuddy 统一网关（仅本机 127.0.0.1）
set -euo pipefail
cd "$(dirname "$0")"

PY="${GATEWAY_PYTHON:-/Users/amano/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

echo "使用 Python: $PY"
exec "$PY" -m gateway.server "$@"
