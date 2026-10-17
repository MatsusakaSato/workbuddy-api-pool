"""WorkBuddy 统一网关。

把本机登录的 WorkBuddy（国内版 / 国际版）模型，以 OpenAI 兼容接口对外暴露，
并提供多账号池、自动续期与半自动签到。

模块划分：
  config    配置与密钥
  creds     凭据发现、自有库与续期
  upstream  上游调用（对话 / 计费 / 签到 / 模型目录）
  gemini    Gemini 独立上游（OpenAI <-> generateContent 协议转换）
  antigravity  Antigravity（Code Assist 免费档）作为 Gemini 的默认后端
  pool      账号池：选择、失败分类、轮换
  server    HTTP 层（OpenAI 兼容路由 + 本地管理页）
"""

__version__ = "0.1.0"
