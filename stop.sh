#!/usr/bin/env bash
# 停掉这个项目的网关（以及所有 --dev 监视器残留）。
#
#   ./stop.sh
#
# 为什么需要它：--dev 模式下，占住 8788 的是**worker**，但重启 worker 的是
# **监视器**。只 kill worker 的话监视器还在，下次改文件它又拉起一个新的，
# 而且残留多个监视器时会互相抢端口、互相把对方挤崩。
# 所以这里按"监视器 → worker"的顺序一起收掉。
set -uo pipefail
cd "$(dirname "$0")"

found=0

# 1) 先收监视器（不带 --dev 的那个 --dev 主进程 + hupper 的 ipc worker）
for pat in "gateway.server --dev" "hupper.ipc" "dev.sh"; do
  for pid in $(pgrep -f "$pat" 2>/dev/null || true); do
    # 别把自己/父进程杀掉
    [ "$pid" = "$$" ] && continue
    kill "$pid" 2>/dev/null && { echo "  停止监视器 $pid ($pat)"; found=1; }
  done
done

# 2) 再收监听 8788 的进程
for pid in $(lsof -nP -iTCP:8788 -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $2}' | sort -u); do
  kill "$pid" 2>/dev/null && { echo "  停止网关 $pid"; found=1; }
done

# 3) 收掉本项目残留的 Vite dev server。
#    只匹配本项目 node_modules 下的 vite —— 这台机器上 5173 还跑着另一个
#    不相干的应用（srtclaw-service），绝不能误杀。
for pid in $(pgrep -f "$(pwd)/web/node_modules/.*vite" 2>/dev/null || true); do
  kill "$pid" 2>/dev/null && { echo "  停止 Vite dev server $pid"; found=1; }
done

sleep 1

# 4) 还赖着不走的，强杀
remain=0
for pid in $(pgrep -f "gateway.server" 2>/dev/null || true); do
  [ "$pid" = "$$" ] && continue
  kill -9 "$pid" 2>/dev/null && remain=1
done
[ "$remain" = "1" ] && echo "  已强制结束残留进程"

# 5) 检查结果
if lsof -nP -iTCP:8788 -sTCP:LISTEN 2>/dev/null | grep -q .; then
  echo "⚠️ 8788 仍被占用："
  lsof -nP -iTCP:8788 -sTCP:LISTEN
  exit 1
fi

echo "✅ 8788 已释放，监视器已清理（残留 ${found} 项）"
