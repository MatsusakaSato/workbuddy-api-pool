# WorkBuddy API 号池网关

把本机 WorkBuddy 客户端里的模型额度，变成一个**标准的 OpenAI 兼容 API**。

网关读取客户端已经登录的账号，在它们之间自动轮换，对外只暴露一个地址、一个 key、一套 OpenAI 接口。
任何支持自定义 OpenAI 端点的工具（Cherry Studio、Chatbox、OpenWebUI、各类 Agent 框架、LangChain…）都能直接接入。

## 它解决什么

- **额度分散在多个账号上**，挨个切换很麻烦 —— 这里自动轮换，某个号限流或积分耗尽就换下一个。
- **每个工具都要单独配一遍** —— 这里只有一个端点，All OpenAI 客户端通用。
- **基础套餐积分月底清零** —— 优先消耗快过期的额度。
- **每天要手动签到领积分** —— 服务端自动完成。

## 快速开始

需要：macOS + 已安装并登录 **WorkBuddy** 和 **CodeBuddy** 桌面端。

```bash
git clone <this-repo> && cd 反代api号池
cp .env.example .env
./run.sh
```

打开 <http://127.0.0.1:8788/>，页面顶部的 key 就是调用凭据。把下面两项填进任意 OpenAI 客户端：

| 配置项 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:8788/v1` |
| API Key | 面板上显示的那串 |

没有客户端测试就一行命令：

```bash
curl http://127.0.0.1:8788/v1/chat/completions \
  -H "Authorization: Bearer $(grep GATEWAY_API_KEY .env | cut -d= -f2)" \
  -H 'Content-Type: application/json' \
  -d '{"model":"glm-5.3","messages":[{"role":"user","content":"你好"}],"stream":false}'
```

## 使用

```bash
./run.sh              # 启动网关（默认 127.0.0.1:8788，仅本机可访问）
./run.sh --dev        # 开发模式，改 .py 自动重启
./stop.sh             # 停止
```

保留命令的当前进程用 Ctrl+C。

**界面**：概览（账号、额度、用量、最近请求）、模型清单、接入说明。浏览器记住 key 后下次直接进。

**模型清单**：`curl http://127.0.0.1:8788/v1/models`，或在面板「模型」页查看。可用模型取决于账号与上游，同一条请求换个模型只需改 `model` 字段。

## 配置

所有配置在项目根目录的 `.env`：

| 变量 | 默认 | 说明 |
|---|---|---|
| `GATEWAY_PORT` | `8788` | 监听端口 |
| `GATEWAY_HOST` | `127.0.0.1` | 监听地址 |
| `GATEWAY_API_KEY` | — | 调用凭据，`./run.sh` 首次启动自动生成 |
| `GATEWAY_MAX_ROTATIONS` | `4` | 单次请求最多换几个号 |
| `GATEWAY_STATE_DIR` | `state` | 账号库、日志、用量记录 |
| `GATEWAY_USAGE_LOG` | `1` | `0` 关闭用量记录 |

想从别的机器访问，改 `GATEWAY_HOST=0.0.0.0`，**同时务必换一个强 key**，并把 `GATEWAY_API_KEY` 当成真正的密钥保管 —— 这个 key 代表你的全部额度。

## 接入其它工具

任何 OpenAI 兼容客户端都一样：Base URL 填 `http://127.0.0.1:8788/v1`，API Key 填面板上那串。

**OpenAI 官方 SDK**

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8788/v1", api_key="<key>")
r = client.chat.completions.create(
    model="glm-5.3",
    messages=[{"role": "user", "content": "写一个快速排序"}],
    stream=True,
)
for chunk in r:
    print(chunk.choices[0].delta.content or "", end="")
```

**Cherry Studio / Chatbox / OpenWebUI**：模型服务选「OpenAI」，填上面的 Base URL 和 Key，保存后即可在模型列表里选择。

流式和非流式都支持，`tools`（function calling）、`system` 消息、多轮对话、`max_tokens` 与 `stream_options.include_usage` 与 OpenAI 行为一致。

## 额外通道：Gemini

如果本机装了 **Antigravity** 客户端（或配置了 Google AI Studio 的 key），网关会同时暴露 Gemini 模型：
`gemini-2.5-flash`、`gemini-2.5-flash-lite`、`gemini-2.5-flash-thinking` 等，调用方式完全相同，只换 `model`。

```bash
# 使用 Google AI Studio key（可选；不填则使用 Antigravity 本地登录凭据）
echo 'GEMINI_API_KEY=AIza...' >> .env
```

> Gemini 对出口 IP 的地区有要求。如果 Antigravity 能正常用、但网关报 `User location is not supported`，说明当前网络出口不被支持，需要挂一个支持地区的代理；仓库里附了写 Clash 规则的脚本 `tools/clash_gemini_route.py`。

## 常见问题

**启动报「未找到凭据」**：先确认 WorkBuddy / CodeBuddy 已登录并至少启动过一次；然后在面板点「重新扫描」，或 `curl -X POST http://127.0.0.1:8788/admin/reload`。

**返回 401**：客户端的 Key 与 `.env` 里的 `GATEWAY_API_KEY` 不一致，面板上复制一遍最新的。

**某个模型报错**：上游本身对该模型不可用（Pro 类、部分 preview 模型常见），换一个模型即可，不影响其它模型。

**全部账号都限流**：等冷却结束（概览里能看到每个号的冷却时间），或去 WorkBuddy 客户端切到还有额度的账号，然后重新扫描。

---

开发者说明（接口清单、内部设计、踩坑记录）见 `DEV_NOTES.md`，该文件不进 git。
