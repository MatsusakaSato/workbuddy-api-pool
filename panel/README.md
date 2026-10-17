# 快速启动面板（QuickPanel）

macOS 桌面常驻悬浮面板，是「反代api号池」网关的第三套展示：
**桌面看板 + 快捷启动器**。和 `web/`（浏览器后台）、`gateway/`（服务端）共用同一份数据。

![布局](../../docs/screenshots/panel-preview.png)

## 它是什么

- **概览区**：今日 Token 用量、今日积分用量、剩余积分（5 账号合计），近 7 日迷你曲线，账号明细可展开
- **启动区**：卡片网格，点击即启动 App / 网页 / 文件夹 / 脚本，**不弹终端窗口**
- **常驻桌面**：无边框悬浮窗，始终置顶，可拖动，不占 Dock

## 快速开始

```bash
cd panel
./build.sh      # 编译（纯 swiftc，无需 Xcode）
./install.sh    # 复制到 dist/ 并启动
```

关闭面板：`pkill -f QuickPanel`

## 数据来源

面板只读网关的管理接口，不直接接触任何上游 token：

| 显示项 | 接口 | 字段 |
|---|---|---|
| 今日 Token | `GET /admin/usage` | `totals.today.tokens` |
| 今日积分 | `GET /admin/usage` | `totals.today.credits` |
| 剩余积分 | `GET /admin/status` | `sum(accounts[].credits)` |
| 近 7 日曲线 | `GET /admin/usage` | `days[]` |

鉴权：面板启动时从项目根 `.env` 读取 `GATEWAY_API_KEY`，不硬编码、不复制。

**前置条件**：网关需在 `127.0.0.1:8788` 运行（`./run.sh`）。网关没开时面板保留上次数据并显示 ⚠️。

## 外观与交互

- **透明毛玻璃**：面板背景是真正的 `NSVisualEffectView`（`material: .sidebar`，`blendingMode: .behindWindow`），
  直接采样桌面壁纸并做模糊，透出后面的窗口内容；窗口本身 `isOpaque = false`、背景透明。
- **钉子按钮**（右上角 📌）：控制面板是否**置顶于所有其他窗口之上**。
  激活时钉子转正、蓝色高亮（窗口层级 `NSWindow.Level.floating`）；
  点击取消后钉子倾斜、灰色（层级回到 `.normal`，会被其他窗口盖住）。
- **统一按钮风格**：两个入口共用同一套胶囊设计 —— 半透明底 + 彩色描边 + 彩色图标 + 文字，
  仅用颜色区分功能（蓝色=网关面板，橙色=重启，绿色=启动）。

## 入口按钮

| 按钮 | 行为 |
|---|---|
| **网关面板**（蓝） | 打开 `http://127.0.0.1:8788/` 后台网页 |
| **启动网关 / 重启网关**（绿 / 橙） | 智能切换：在跑显示「重启网关」，没在跑显示「启动网关」；自动保持 `--dev` 模式；不弹终端 |

网关状态通过直连 `127.0.0.1:8788` 探测（不用 `lsof`，受限环境会卡住），按钮文案随状态实时切换。

## 配置 `config.json`

```jsonc
{
  "panel": {
    "width": 320,            // 面板宽度
    "corner": "top-right",   // top-right / top-left / bottom-right / bottom-left / center
    "margin": 16,            // 离屏幕边缘距离
    "refreshSeconds": 30,    // 刷新间隔（最小 5 秒）
    "showAccounts": true,    // 是否显示账号明细
    "pinnedByDefault": true  // 启动时是否置顶（可由钉子按钮随时切换）
  }
}
```

> 注：卡片式启动器已按需求精简为固定两个按钮，`cards` 字段保留但不再使用。

## 技术说明

- **纯 Command Line Tools 构建**：本机无 Xcode，SwiftUI 的 `@State` 等宏（`SwiftUIMacros`）不可用，
  因此全程使用 `ObservableObject` + `@Published`，不使用任何 SwiftUI 属性包装器宏。
- **窗口**：`NSPanel` + `.borderless` + `.nonactivatingPanel`，`level = .floating`，
  `collectionBehavior` 允许跨 Space 显示。
- **数据层**：`GCD` 后台队列请求两个接口，主线程更新 UI；任一失败保留旧值。

### 数据刷新的三条硬约束

这三条都踩过坑，改动前请先看清：

1. **防重入必须同步判断。** `refresh()` 里的 `guard !isRefreshing` 不能塞进
   `DispatchQueue.main.async` —— 定时器和「立即刷新」按钮可能在同一轮触发，
   异步守卫会让两次请求都放行，两个响应互相覆盖，还会把 `isRefreshing` 状态搞乱。
   现在统一走 `performRefreshIfIdle()`，只从主线程读写。
2. **`reachable` 每轮重置。** 它是「本次拉取是否成功」，从上一份快照继承的话，
   一次成功后即便网关挂掉也永远显示在线，⚠️ 图标再也不会出现。
3. **合计余额只累加已知值。** `null` 表示「还没拉到」，不能当 0。
   若存在未知账号，面板会显式提示「含 N 个账号余额未拉取，合计偏低」，
   避免把「未知」误读成「余额变少了」。

> 另注：近 7 日曲线以**区间最小值**为基线而非绝对 0。号池通常只有两三天有数据，
> 从 0 画起会变成「贴底一条线 + 一根尖刺」，看起来像没数据。

## 已知限制

- 面板属 `LSUIElement`（无 Dock 图标、无菜单栏），只能通过 `pkill` 或重启登录退出。
- 未签名（本地临时签名），若拷到 `~/Applications` 首次打开可能需 **右键 → 打开**。
- 概览数据依赖网关在线；网关离线时只显示缓存值。
- **改完源码必须 `./build.sh && ./install.sh`**：`dist/` 里的 app 是复制件，
  只重新编译不重装，跑的还是旧二进制（这个坑已经踩过一次）。

