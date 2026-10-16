#!/usr/bin/env bash
# 全量回归。只读 + 少量真实请求（会消耗极少积分）。
#
#   ./tests/run_all.sh              跑全部
#   ./tests/run_all.sh --no-live    跳过需要真实上游的 harness 兼容性探针
set -uo pipefail
cd "$(dirname "$0")/.."

PY="${GATEWAY_PYTHON:-/Users/amano/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"

SKIP_LIVE=0
[ "${1:-}" = "--no-live" ] && SKIP_LIVE=1

FAILED=0
run() {
  local name="$1"; shift
  echo
  echo "════════════════════════════════════════════════════════"
  echo "  $name"
  echo "════════════════════════════════════════════════════════"
  if "$@"; then
    echo "→ 通过"
  else
    echo "→ 【失败】"
    FAILED=$((FAILED + 1))
  fi
}

run "1/4 限流重置时间解析（离线）" "$PY" tests/test_cooldown.py
run "2/4 用量统计（离线）" "$PY" tests/test_usage.py

if curl -s --max-time 3 http://127.0.0.1:8788/healthz > /dev/null 2>&1; then
  run "3/4 换号契约（故障注入 + 1 次真实请求）" "$PY" tests/test_failover.py
  if [ "$SKIP_LIVE" = "0" ]; then
    run "4/4 harness 兼容性（约 13 次真实请求）" "$PY" tests/test_harness_compat.py
  else
    echo; echo "（已跳过 harness 兼容性探针）"
  fi
else
  echo
  echo "网关未运行，跳过需要实例的测试。启动：./run.sh"
  FAILED=$((FAILED + 1))
fi

echo
echo "════════════════════════════════════════════════════════"
if [ "$FAILED" = "0" ]; then
  echo "  全部通过"
else
  echo "  $FAILED 个套件失败"
fi
echo "════════════════════════════════════════════════════════"
exit "$FAILED"
