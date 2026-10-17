# WorkBuddy 内部 API 接口说明书

> 目标：不依赖 WorkBuddy 客户端界面，**用自己的程序直接调用**它背后的模型（DeepSeek / Gemini / GLM / Kimi / GPT…）。
>
> 取证方式：本机 `/Applications/WorkBuddy.app` 的 `app.asar` 静态分析 + 本机凭据文件实读 + 开源实现交叉验证。**未经实测调用**（未消耗你的积分），标记 ⚠️ 的条目属于推断。

---

## 0. 一句话结论

WorkBuddy 没有开放任何本地端口，它自己就是一个**中心化网关的客户端**。你只要能拿到它登录态里的 `accessToken`，就能拿着同一个 token 直接 POST 同一个网关地址——**这就是"反代"的全部**。

而且它上游是**双区域、两套独立凭据体系**：

> ⚠️ **网关侧状态（2026-10-16）**：网关只做**国内版**。下表**国际版 (Global) 一列仅作历史记录**
> ——域名为 `*.workbuddy.ai` / `*.codebuddy.ai` 的凭据会被网关直接跳过，不进账号池。

| | 国内版 (CN) | 国际版 (Global)（网关已不再使用） |
|---|---|---|
| 对话网关 | `https://copilot.tencent.com` | `https://www.workbuddy.ai` |
| 计费/配置源 | `https://www.codebuddy.cn` | `https://www.workbuddy.ai` |
| 备用/兜底 | `https://wb.tencentbuddy.com` | — |
| **DeepSeek** | ✅ `deepseek-v4-pro` / `deepseek-v4-flash` | ✅ `deepseek-v4.1-flash` |
| **Gemini** | ❌ 名单内没有 | ✅ `gemini-3.5-flash` |
| GPT 系列 | ❌ | ✅ `gpt-6-astra` / `gpt-5.6-*` / `gpt-5.5` … |
| GLM / Kimi | ✅ | ✅ |

> 关键（技术事实）：**DeepSeek 在国内版，Gemini 在国际版。** 这是两个账号、两个 token、两个
> base_url，不能混用——token 签发域和网关必须匹配（拿 `workbuddy.ai` 的 token 调
> `copilot.tencent.com` 会被 openresty 层直接 401）。但**网关已决定只走国内版**，所以国际版那一列
> （含 `gemini-3.5-flash`、GPT 系列）只是逆向记录，不再是可对接路径。

---

## 1. 对话接口

### 1.1 请求

```http
POST {base}/v2/chat/completions
```

| 区域 | `{base}` |
|---|---|
| CN | `https://copilot.tencent.com` |
| Global（网关已不支持，仅历史记录） | `https://www.workbuddy.ai`（若凭据 domain 是 `codebuddy.ai` 则用 `https://www.codebuddy.ai`） |

### 1.2 请求头

**最小可用集**（官方 CLI 走的就是这套 X-No-* 约定）：

```http
Authorization: Bearer <accessToken>
Content-Type: application/json
X-Product: SaaS
X-User-Id: <account.uid>           # 没有则用 X-No-User-Id: 1
X-Enterprise-Id: <enterpriseId>    # 没有则用 X-No-Enterprise-Id: 1
X-Domain: <auth.domain>            # 没有则用 X-No-Department-Info: 1
Accept: application/json, text/plain, */*
X-Requested-With: XMLHttpRequest
Origin: <计费域>
Referer: <计费域>/
User-Agent: <客户端 UA>
```

**App 自身**（`app.asar` 内 `VlmUpstream`）还会额外带这些，属于可选项，缺了通常也能通：

```http
X-Conversation-ID / X-Conversation-Request-ID / X-Conversation-Message-ID
X-Request-ID / acp-connection-id
X-Agent-Intent: craft
X-Agent-Purpose: conversation
x-codebuddy-request: 1
X-IDE-Type: WorkBuddy / X-IDE-Name: WorkBuddy / X-IDE-Version: 0.0.0
X-Private-Data: true
traceparent / b3 / X-B3-TraceId / X-B3-SpanId / X-B3-ParentSpanId / X-B3-Sampled / X-Trace-ID
```

> 标 ⚠️：来源是 App 与开源插件的实现，实际服务端是否强校验未逐项实测。

### 1.3 请求体与响应

标准 OpenAI Chat Completions 形状（`model` + `messages` + `stream`），响应为 SSE。因此**绝大多数按 OpenAI 协议写的客户端可以直接对接**，只是鉴权头是 Bearer token 而非 `sk-` key。

### 1.4 配套路径（同一网关下）

| 路径 | 用途 |
|---|---|
| `POST /v2/chat/prediction` | 推测/补全 ⚠️ |
| `POST /v2/chat/queue/status` | 排队状态（上游会排队，429 时尤其有用） |
| `POST /v2/chat/queue/cancel` | 取消排队 |
| `/v2/chat_button` | 按钮交互 ⚠️ |
| `GET /v3/config` | **模型目录**（含积分倍率、上下文/输出上限、是否支持图片） |
| `/v2/update?platform=…` | 客户端更新 |

---

## 2. 凭据从哪里拿

### 2.1 文件位置

**macOS（你本机就是这个）**：

```
~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/
├── workbuddy-desktop.info                              ← 当前登录（live）
├── workbuddy-desktop.<时间戳>.info                     ← 历史备份，每个可能是不同账号
└── workbuddy-desktop.info.<hash>.observation.json      ← 插件写的观测记录
```

- Windows：`%LOCALAPPDATA%\CodeBuddyExtension\Data\Public\auth` 与 `%APPDATA%\...` 都探测
- Linux：`$XDG_CONFIG_HOME` 或 `~/.config` 下的同一相对路径
- 可用环境变量 `WORKBUDDY_AUTH_FILE` 指定

### 2.2 文件结构（你本机实读结果）

```jsonc
{
  "account": {
    "uid": "…",              // → X-User-Id
    "uin": "…",              // 账号去重键
    "phoneNumber": { "$wbEncrypted": 1, "envelope": "…" },   // 加密
    "type": "personal"
  },
  "auth": {
    "accessToken":  { "$wbEncrypted": 1, "envelope": "…" },  // ← 要的就是它
    "refreshToken": { "$wbEncrypted": 1, "envelope": "…" },
    "tokenType": "Bearer",
    "domain": "www.workbuddy.cn",   // ← 网关只接受 .cn 域；workbuddy.ai / codebuddy.ai 的凭据会被跳过
    "expiresAt": 1794628367043,     // 约 30 天
    "refreshExpiresAt": 1797220366043
  }
}
```

### 2.3 ⚠️ 加密字段怎么办

你本机 `workbuddy-desktop.info` 里的 `accessToken` 是**加密 envelope**（`$wbEncrypted`），不是明文。

解密需要 App 的 **at-rest 密钥**，获取方式（已被开源实现验证过的路径）：

1. 定位 WorkBuddy 可执行文件，用 `CFBundleIdentifier` 确认身份（**不能只按文件名匹配**，所有 Electron 应用的二进制都叫 `Electron`）；
2. 启动它以取出密钥 payload；
3. `deriveAtRestKey(payload)` → 密钥；
4. 用信封格式 `WBEV1` / AAD 域分隔符 `WB-AAD\0` 解 `accessToken`。

密钥缓存于进程内，**不落盘**。

> **绕开办法**：你本机 `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth/Tencent-Cloud.coding-copilot.info` 里的
> `auth.accessToken` 是**明文 JWT**（同一账号 `3afc9084-…`，Bearer，带 `refreshToken`）。
> 它是 CodeBuddy 通道的凭据，是否被 `copilot.tencent.com` 接受**未实测** ⚠️。
> 更稳的做法是**抓一次包**，直接看 App 真实发出的 `Authorization` 头。

### 2.4 有效期与刷新

- `accessToken` 约 **30 天**（`expiresIn: 2592000`）
- `refreshToken` 约 **60 天**（`refreshExpiresIn: 5183999`）
- 刷新：带 `X-Refresh-Token: <refreshToken>` + `X-Auth-Refresh-Source: workbuddy` 请求刷新端点
- 401 时 App 会自动 `refreshSession()` 重放请求——你自己的程序也需要实现这一步

---

## 3. 模型 ID 清单

### 国内版（`copilot.tencent.com`）

| 模型 ID | 名称 | 上下文 | 最大输出 |
|---|---|---|---|
| `auto` | Auto | 168K | 32K |
| `deepseek-v4-pro` | DeepSeek-V4-Pro | 1M | 50K |
| `deepseek-v4-flash` | DeepSeek-V4-Flash | 1M | 50K |
| `glm-5.3` / `glm-5.2` / `glm-5.1` | GLM | 1M / 1M / 200K | 48K |
| `glm-5v-turbo` | GLM-5v-Turbo（视觉） | 200K | 64K |
| `kimi-k3-1` | Kimi-K3 | 1M | 32K |
| `kimi-k2.7` / `kimi-k2.6` | Kimi | 256K | 32K |
| `minimax-m3` | MiniMax-M3 | 512K | 128K |
| `hy3` | Hy3 | 192K | 64K |

### 国际版（`www.workbuddy.ai`）——⚠️ 网关已不支持（仅历史记录）

| 模型 ID | 名称 |
|---|---|
| **`gemini-3.5-flash`** | **Gemini-3.5-Flash** |
| `deepseek-v4.1-flash` | DeepSeek-V4.1-Flash |
| `gpt-6-astra` / `gpt-5.6-sol` / `gpt-5.6-terra` / `gpt-5.6-luna` | GPT |
| `gpt-5.5` / `gpt-5.4` / `gpt-5.3-codex` | GPT |
| `glm-5.3` / `glm-5.2` | GLM |
| `kimi-k3` / `kimi-k2.6` | Kimi |
| `hy4-preview` / `hy3` | 混元 |
| `default-model` / `fast-model` / `balanced-model` / `primary-model` / `deep-model` | 匿名路由档位 |

> ⚠️ 下面这张表属于**国际版**，网关已不再支持，只作历史记录。
> 上述是**离线兜底名单**（捕获自 `www.workbuddy.ai/v3/config`）。**以 `/v3/config` 实时拉取为准**——它会返回当前可用模型及积分倍率。
> App 二进制里另外出现 `gemini-2.5-pro`、`gemini-3.6/3.7/3.8-flash` 等字符串，属于产品配置缓存，**未必对当前账号开放**。

---

## 4. 最小调用示例（伪代码）

```bash
TOKEN='<从凭据文件解出的 accessToken>'
UID='<account.uid>'
DOMAIN='<auth.domain>'

curl -N https://copilot.tencent.com/v2/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'X-Product: SaaS' \
  -H "X-User-Id: $UID" \
  -H 'X-No-Enterprise-Id: 1' \
  -H "X-Domain: $DOMAIN" \
  -H 'Accept: application/json, text/plain, */*' \
  -H 'X-Requested-With: XMLHttpRequest' \
  -d '{"model":"deepseek-v4-pro","stream":true,"messages":[{"role":"user","content":"你好"}]}'
```

> ⚠️ **以下属于国际版，网关已不支持**（网关只打国内版 `copilot.tencent.com`）。
> Gemini 等国际版模型同理，只换 `base` 与 `model`：

```bash
curl -N https://www.workbuddy.ai/v2/chat/completions \
  -H "Authorization: Bearer $GLOBAL_TOKEN" … \
  -d '{"model":"gemini-3.5-flash","stream":true,"messages":[…] }'
```

---

## 5. 风险与代价（务必先读）

1. **这是账号级凭据，不是 API key。** 泄露 token ≈ 泄露你的 WorkBuddy 登录态（含手机号、企业信息）。不要提交进 git、不要贴进聊天记录。
2. **消耗的是你账号的积分**，与在客户端里聊天走同一笔账。上游对**请求体积**敏感：实测约 20K token 能过、约 30K 可能直接 429，且 429 **不带** `Retry-After`，重置时间写在响应体里。
3. **ToS 风险**：用第三方程序调用私有接口，可能违反用户协议，存在被限流或封号的可能。请自行评估。
4. **接口不稳定**：这是未公开的私有接口，客户端升级随时可能改路径、改头、改加密格式。别把关键业务压在上面。
5. **不要做多账号滥用**：多账号轮换用于自己名下的多个账号属于灰色地带，用于规避配额/批量注册则可能违法违规。

---

## 6. 三条落地路线

| 路线 | 工作量 | 说明 |
|---|---|---|
| **A. 直接装现成插件** `dsh-connect-workbuddy` | 最低 | 已实现双区域、凭据解密、自动续期、账号池、模型目录，并对外暴露**本地 OpenAI 兼容端点** `/v1/chat/completions` + `/v1/models`（loopback + 随机 Bearer）。你正在用的 DSH 可以直接装。 |
| **B. 自己写 FastAPI 中转** | 中 | 按本文档实现：读凭据 → 解 envelope → 国内版路由 → 多 token 轮换 → 对外 OpenAI 格式。适合你想完全掌控或对外提供服务。 |
| **C. 先抓包验证** | 低 | 用 mitmproxy 抓一次真实请求，拿到**明文** `Authorization` 头，即可跳过解密环节，同时验证本文档头部清单是否准确。 |

---

## 附录：取证证据链

| 结论 | 证据 |
|---|---|
| 对话接口 | `app.asar` @89571078：(`VlmUpstream`) `WN=\`https://copilot.tencent.com/v2/chat/completions\``，`UN=\`/v2/chat/completions\`` |
| 鉴权取 token | 同处 `requireAccessToken(e){let n=e.getAccessToken()?.trim(); if(!n) throw Error('[Vlm] 未登录')}`；401 → `retryAfterRefresh` |
| 三域名拓扑 | `app.asar` @78414007：`CN_SAAS_CORE_API_PRIMARY_ORIGIN='https://www.workbuddy.cn'`、`ALTERNATE='https://copilot.tencent.com'`、`BACKUP='https://wb.tencentbuddy.com'` |
| 请求头 | `app.asar` @89574903：`X-Conversation-ID`/`X-Agent-Intent: craft`/`X-IDE-Type: WorkBuddy`/`applyTraceHeaders` |
| 凭据路径 | `dsh-connect-workbuddy` 源码 `defaultDesktopAuthDirs()`：macOS `~/Library/Application Support/CodeBuddyExtension/Data/Public/auth` |
| 凭据字段 | 本机实读 `workbuddy-desktop.info`（`auth.accessToken` 为 `$wbEncrypted` envelope） |
| 请求头最小集 | 插件源码 `chatHeaders()` / `commonHeaders()` / `refreshHeaders()` |
| 模型清单 | 插件源码 `FALLBACK_WORKBUDDY_MODELS`（CN）/ `FALLBACK_WORKBUDDY_MODELS_GLOBAL`（Global） |
