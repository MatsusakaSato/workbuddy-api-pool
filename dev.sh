#!/usr/bin/env bash
# 开发模式：同时起后端（热重载）与前端（HMR），Ctrl+C 一起退。
#
#   ./dev.sh
#
# 打开 http://127.0.0.1:5173/ 开发。
#   · 改 gateway/**.py  → 后端自动重启（hupper 轮询，最多 1s）
#   · 改 web/src/**.tsx → 浏览器热替换，不整页刷新、不丢组件状态（Vite HMR）
#
# 为什么前端要走 5173 而不是直接开 8788：
#   8788 发的是**构建产物**（gateway/static/），改源码不会反映出来，
#   必须手动 `pnpm run build` —— 这正是过去"改了没反应"的根源。
#   Vite dev server 提供真正的 HMR，并把 /admin /v1 /healthz
#   代理到 8788 的真实网关，浏览器视角仍然同源。
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .devdeps ]; then
  echo "首次使用需要安装开发依赖：./run.sh --dev-install"
  exit 1
fi

# 退出时把两个子进程都收干净，避免残留占着 8788 / 5173
PIDS=()
cleanup() {
  echo ""
  echo "正在停止开发进程 ..."
  for pid in "${PIDS[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
  echo "已停止。"
}
trap cleanup EXIT INT TERM

echo "▶ 启动后端（热重载）..."
./run.sh --dev &
PIDS+=($!)

echo "▶ 启动前端（HMR）..."
( cd web && exec pnpm run dev ) &
PIDS+=($!)

echo ""
echo "════════════════════════════════════════════"
echo "  前端地址看 Vite 打印的那行（通常是 5173；"
echo "  本机 5173 被别的应用占着，会自动退到 5174）。"
echo "  改后端 .py → 自动重启；改前端 .tsx → 热替换"
echo "  Ctrl+C 停止全部"
echo "════════════════════════════════════════════"

# 等任一子进程退出（通常是 Ctrl+C）。
# `wait -n` 需要 bash 4.1+，macOS 自带的是 3.2，所以留个回退。
wait -n 2>/dev/null || wait
