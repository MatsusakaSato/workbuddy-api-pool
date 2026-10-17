#!/bin/zsh
# 编译 QuickPanel.app。纯 swiftc，无需 Xcode 工程。
set -euo pipefail
cd "$(dirname "$0")"

APP_NAME="QuickPanel"
BUILD=".build"
APP="$BUILD/$APP_NAME.app"
SDK=$(xcrun --show-sdk-path 2>/dev/null || echo "")

echo "==> 清理旧产物"
rm -rf "$APP" "$BUILD/ModuleCache"
mkdir -p "$BUILD/ModuleCache" "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "==> 编译（arm64）"
SDK_ARGS=()
if [ -n "$SDK" ]; then SDK_ARGS=(-sdk "$SDK"); fi
swiftc -O \
  -parse-as-library \
  -module-cache-path "$BUILD/ModuleCache" \
  -target arm64-apple-macos13.0 \
  "${SDK_ARGS[@]}" \
  -o "$APP/Contents/MacOS/$APP_NAME" \
  Sources/*.swift \
  -framework AppKit -framework SwiftUI

echo "==> 写入 Info.plist"
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>QuickPanel</string>
  <key>CFBundleDisplayName</key>
  <string>快速启动面板</string>
  <key>CFBundleIdentifier</key>
  <string>local.quickpanel</string>
  <key>CFBundleExecutable</key>
  <string>QuickPanel</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
  </dict>
</dict>
</plist>
PLIST

echo "==> 本地临时签名"
codesign --force --deep --sign - "$APP" 2>/dev/null || echo "  (codesign 失败，忽略，仍可运行)"

echo "==> 完成：$APP"
echo "    下一步：./install.sh 安装到 ~/Applications"
