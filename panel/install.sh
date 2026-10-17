#!/bin/zsh
# 把编译好的 QuickPanel.app 复制到项目内 dist/ 并启动。
# 说明：沙箱/权限环境下 ~/Applications 可能不可写，因此默认装在项目内；
#      如需安装到 ~/Applications，手动执行：
#        cp -R panel/dist/QuickPanel.app ~/Applications/ && open ~/Applications/QuickPanel.app
set -euo pipefail
cd "$(dirname "$0")"

APP=".build/QuickPanel.app"

if [ ! -d "$APP" ]; then
  echo "未找到 $APP，请先运行 ./build.sh"
  exit 1
fi

echo "==> 复制到 dist/"
mkdir -p dist
rm -rf dist/QuickPanel.app
cp -R "$APP" dist/
xattr -dr com.apple.quarantine "dist/QuickPanel.app" 2>/dev/null || true

echo "==> 启动"
pkill -f "QuickPanel.app/Contents/MacOS/QuickPanel" 2>/dev/null || true
sleep 0.5
open "dist/QuickPanel.app"

echo "完成。面板位于屏幕右上角；关闭：pkill -f QuickPanel"
