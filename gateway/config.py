"""配置：从 .env 读取，全部有安全默认值。"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_env_file(path: Path) -> None:
    """极简 .env 解析：KEY=VALUE，支持 # 注释与引号。不覆盖已存在的环境变量。"""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


# --- 对外暴露 ---
HOST = os.environ.get("GATEWAY_HOST", "127.0.0.1")
PORT = _int("GATEWAY_PORT", 8788)

# 对外固定 API key。留空则自动生成并写入 .env（只写一次）。
ENV_PATH = ROOT / ".env"
API_KEY = os.environ.get("GATEWAY_API_KEY", "").strip()


def ensure_api_key() -> str:
    """没有 key 就生成一把 sk- 开头的并落盘到 .env，保证跨重启稳定。"""
    global API_KEY
    if API_KEY:
        return API_KEY
    API_KEY = "sk-wb-" + secrets.token_urlsafe(32)
    line = f"GATEWAY_API_KEY={API_KEY}\n"
    if ENV_PATH.is_file():
        ENV_PATH.write_text(ENV_PATH.read_text(encoding="utf-8") + line, encoding="utf-8")
    else:
        ENV_PATH.write_text(
            "# 由网关自动生成，勿提交到 git\n" + line, encoding="utf-8"
        )
    os.chmod(ENV_PATH, 0o600)
    return API_KEY


# --- 上游 ---
CN_CHAT_BASE = "https://copilot.tencent.com"
CN_BILLING_BASE = "https://www.codebuddy.cn"
GLOBAL_CHAT_BASE = "https://www.workbuddy.ai"

# --- Gemini（Google AI Studio，独立上游，与 WorkBuddy 号池解耦）---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_BASE = os.environ.get("GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")

# WorkBuddy 桌面端凭据目录（macOS）
DESKTOP_AUTH_DIR = Path.home() / (
    "Library/Application Support/CodeBuddyExtension/Data/Public/auth"
)

# 凭据解密助手（复用 dsh-connect-workbuddy 的 at-rest 实现）
NODE_BIN = os.environ.get(
    "GATEWAY_NODE_BIN",
    "/Users/amano/.dsh/dsh-runtimes/dsh-primary-runtime/dependencies/node/bin/node",
)
READ_CREDS_JS = ROOT / "tools" / "read_creds.mjs"

# 网关自有凭据库（刷新后的副本，只存自己的文件，不改 App 的）
STATE_DIR = Path(os.environ.get("GATEWAY_STATE_DIR", str(ROOT / "state")))
ACCOUNTS_FILE = STATE_DIR / "accounts.json"

# 请求行为
UPSTREAM_TIMEOUT = _int("GATEWAY_UPSTREAM_TIMEOUT", 300)
REFRESH_MARGIN_MS = _int("GATEWAY_REFRESH_MARGIN_MS", 6 * 60 * 60 * 1000)  # 提前 6 小时续期
RATE_LIMIT_COOLDOWN_S = _int("GATEWAY_RATE_LIMIT_COOLDOWN", 600)
MAX_ROTATIONS = _int("GATEWAY_MAX_ROTATIONS", 4)  # 一次请求最多换几个账号

LOG_LEVEL = os.environ.get("GATEWAY_LOG_LEVEL", "INFO").upper()
LOG_FILE = STATE_DIR / "gateway.log"

__all__ = [
    "HOST", "PORT", "API_KEY", "ensure_api_key",
    "CN_CHAT_BASE", "CN_BILLING_BASE", "GLOBAL_CHAT_BASE",
    "GEMINI_API_KEY", "GEMINI_BASE",
    "DESKTOP_AUTH_DIR", "NODE_BIN", "READ_CREDS_JS",
    "STATE_DIR", "ACCOUNTS_FILE",
    "UPSTREAM_TIMEOUT", "REFRESH_MARGIN_MS", "RATE_LIMIT_COOLDOWN_S",
    "MAX_ROTATIONS", "LOG_LEVEL", "LOG_FILE", "ROOT",
]
