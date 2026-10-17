#!/usr/bin/env bash
# 启动 WorkBuddy 统一网关（仅本机 127.0.0.1）
#
#   ./run.sh                 生产模式。运行时零第三方依赖，行为与过去完全一致。
#   ./run.sh --dev           开发模式。改 gateway/**.py 保存即自动重启。
#   ./run.sh --dev-install   安装开发依赖（hupper + watchdog）到项目内 .devdeps/
set -euo pipefail
cd "$(dirname "$0")"

PY="${GATEWAY_PYTHON:-/Users/amano/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/python/bin/python3}"
if [ ! -x "$PY" ]; then
  PY="$(command -v python3)"
fi

# 开发依赖只装在项目内的 .devdeps/，不碰解释器的 site-packages。
# 原因：打包运行时的 site-packages 是只读的，且属于 DSH 运行时，
# 往那里装东西既会失败、又会污染它。
DEVDIR=".devdeps"

case "${1:-}" in
  --dev-install)
    echo "使用 Python: $PY"
    echo "安装开发依赖到 $DEVDIR/ ..."
    # 只装 hupper，**不装 watchdog**：macOS 上 watchdog 只能退到 kqueue，
    # 而 kqueue 的监视在 worker 重启后失效（实测只有第一次改动会触发重载）。
    # hupper 自带的轮询监视器更慢但可靠，见 gateway/server.py 里的说明。
    "$PY" -m pip install --quiet --target "$DEVDIR" hupper
    "$PY" -c "import sys; sys.path.insert(0,'$DEVDIR'); import hupper; print('✅ hupper 就绪 →', hupper.__file__)"
    echo "完成。现在可以：./run.sh --dev"
    exit 0
    ;;
esac

echo "使用 Python: $PY"
exec "$PY" -m gateway.server "$@"
