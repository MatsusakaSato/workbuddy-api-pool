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


def _float(name: str, default: float) -> float:
    """浮点配置项。空值/非法值/NaN/Inf 一律回落到默认值，避免脏 env 把调度算坏。"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        out = float(raw.strip())
    except ValueError:
        return default
    if out != out or out in (float("inf"), float("-inf")):  # NaN / Inf
        return default
    return out


def _nonneg_float(name: str, default: float) -> float:
    """与 `_float` 相同，但**接受 0**（负数仍回落默认）。

    存在的理由：`ROTATION_W_BASE=0` 是「关闭基础积分优先」的回滚开关。
    用 `_float` 读会把 0 当成非法值静默回落到 1.5 —— 一个写着"关掉"的配置
    实际却开着，比没有这个开关更糟。
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        out = float(raw.strip())
    except ValueError:
        return default
    if out != out or out in (float("inf"), float("-inf")) or out < 0:  # NaN / Inf / 负数
        return default
    return out


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


# --- 上游（只做国内版）---
# 国际版（www.workbuddy.ai）已整体移除，这里不再有第二个 WorkBuddy base。
CN_CHAT_BASE = "https://copilot.tencent.com"
CN_BILLING_BASE = "https://www.codebuddy.cn"

# --- Gemini（独立上游，与 WorkBuddy 号池解耦）---
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

# --- 账号轮换（反薅羊毛）---
# 契约来源：docs/账号轮换策略设计.md §6。全部有安全默认值，.env 为空也能启动。
# GATEWAY_ROTATION_ENABLED=0 是**回滚开关**：一条配置退回旧的「积分高优先」。
ROTATION_ENABLED = _bool("GATEWAY_ROTATION_ENABLED", True)
ROTATION_SLICE_S = _float("GATEWAY_ROTATION_SLICE_S", 30.0)          # 第 2 层时间片宽度
ROTATION_MIN_INTERVAL_S = _float("GATEWAY_ROTATION_MIN_INTERVAL_S", 5.0)  # 同账号最小复用间隔
ROTATION_MAX_CONCURRENCY = _int("GATEWAY_ROTATION_MAX_CONCURRENCY", 2)    # 同账号最大并发
ROTATION_W_CREDIT = _float("GATEWAY_ROTATION_W_CREDIT", 1.0)         # 剩余额度占比权重
ROTATION_W_RUNWAY = _float("GATEWAY_ROTATION_W_RUNWAY", 2.0)         # 续航占比权重（最高）
ROTATION_W_COOL = _float("GATEWAY_ROTATION_W_COOL", 0.5)             # 冷却度（越久没用越该用）
# 基础套餐积分优先（2026-10-17，同日修正为**硬分级**）。
# 基础套餐月底清零、奖励包过期晚得多，所以只要池子里还有基础积分，就先花它。
#
# 两个开关语义**不同**，别混用（第一版混用过，实测策略没生效）：
#   · BASE_PRIORITY=0  —— 真正的回滚：关掉 Layer 1 的分级过滤，耗尽基础套餐的
#     账号重新参与竞争（回到「只按总余额加权」）。默认开。
#   · W_BASE=0         —— 只关掉 Layer 3 的 base_share 偏好（同级别内微调）。
#     它**管不了资格**：把 W_BASE 从 1.5 拉到 20，耗尽号的份额也只从 14% 降到 10%。
ROTATION_BASE_PRIORITY = _bool("GATEWAY_ROTATION_BASE_PRIORITY", True)
# 允许 0 的读取，不能复用 _float 的"非正即回默认"语义（0 是合法关闭值）。
ROTATION_W_BASE = _nonneg_float("GATEWAY_ROTATION_W_BASE", 1.5)
ROTATION_EMA_HALFLIFE_S = _float("GATEWAY_ROTATION_EMA_HALFLIFE_S", 900.0)  # 消耗速率 EMA 半衰期

# MIN_RATE 是**除零保护兼速率下限**（设计文档 §3 第 3 层）。
# 没有它，「有额度但从未被用过」的号 runway = ∞，会独占全部权重。
# 这里做硬钳制：非正数、NaN、Inf 或过大的值都被压回安全区间，
# 保证 runway(c) = credits / max(ema, MIN_RATE) 永远不可能除零或溢出。
if not (ROTATION_EMA_HALFLIFE_S > 0):
    ROTATION_EMA_HALFLIFE_S = 900.0  # 半衰期非正会让 EMA 没有意义，回默认
if not (ROTATION_SLICE_S > 0):
    ROTATION_SLICE_S = 30.0
if not (ROTATION_MIN_INTERVAL_S >= 0):
    ROTATION_MIN_INTERVAL_S = 5.0
if ROTATION_MAX_CONCURRENCY < 1:
    ROTATION_MAX_CONCURRENCY = 1  # 并发上限小于 1 会让池子直接选不出号

_MIN_RATE_FLOOR = 1e-6      # 速率下限的地板：再小就接近除零
_MIN_RATE_CEILING = 1e6     # 速率下限的天花板：再大所有号 runway 都被压成 0
_RAW_MIN_RATE = _float("GATEWAY_ROTATION_MIN_RATE", 0.01)
if not (_RAW_MIN_RATE > 0):          # 负数 / 0 / NaN / Inf → 回默认
    MIN_RATE = 0.01
else:
    # 只钳制到 [地板, 天花板]，不覆盖用户合理设定（例如 0.5）
    MIN_RATE = min(max(_RAW_MIN_RATE, _MIN_RATE_FLOOR), _MIN_RATE_CEILING)

# 热路径落盘节流间隔（秒）：小间隔内跳过写盘，除非 force=True。
SAVE_THROTTLE_S = _float("GATEWAY_SAVE_THROTTLE_S", 10.0)
if not (SAVE_THROTTLE_S >= 0):
    SAVE_THROTTLE_S = 10.0

# --- 全自动签到 ---
# 2026-10-16 变更：签到由「半自动（前端勾选 + 二次确认）」改为**网关自己定时领取**。
# 理由：每日签到是保号的核心动作，漏一天就少一天额度；而"记得手动点"这件事
# 本来就该由程序代劳。README 里原先写过"网关不会自己定时领取"，那条已被本次需求推翻。
#
# 风控取舍（必须知情）：签到是真实领取动作，定时执行会在上游留下机械指纹。
# 所以这里默认带**随机抖动**、账号之间留间隔，而不是固定整点批量打上去。
# GATEWAY_AUTO_CHECKIN=0 是回滚开关：一条配置退回"只查询、不自动领"。
AUTO_CHECKIN_ENABLED = _bool("GATEWAY_AUTO_CHECKIN", True)
AUTO_CHECKIN_INTERVAL_S = _int("GATEWAY_AUTO_CHECKIN_INTERVAL_S", 1800)
AUTO_CHECKIN_JITTER_S = _int("GATEWAY_AUTO_CHECKIN_JITTER_S", 600)
AUTO_CHECKIN_ACCOUNT_GAP_S = _float("GATEWAY_AUTO_CHECKIN_ACCOUNT_GAP_S", 2.5)

# 钳制：间隔太短会把上游打成扫描器；抖动为负会让 next_run 落到过去，
# 结果就是"每一轮都立刻重跑"，等于没有间隔。
if AUTO_CHECKIN_INTERVAL_S < 60:
    AUTO_CHECKIN_INTERVAL_S = 60
if AUTO_CHECKIN_JITTER_S < 0:
    AUTO_CHECKIN_JITTER_S = 0
if not (AUTO_CHECKIN_ACCOUNT_GAP_S >= 0):
    AUTO_CHECKIN_ACCOUNT_GAP_S = 2.5
AUTO_CHECKIN_STATE_FILE = STATE_DIR / "checkin.json"

LOG_LEVEL = os.environ.get("GATEWAY_LOG_LEVEL", "INFO").upper()
LOG_FILE = STATE_DIR / "gateway.log"


def rotation_policy():
    """构造轮换策略对象。

    契约（docs/账号轮换策略设计.md §3、§6）：返回 `rotation.RotationPolicy`。
    这里**惰性导入** `rotation`，因为 `rotation.py` 反过来 import `config`，
    模块加载期导入会形成循环。
    """
    from .rotation import RotationPolicy  # 惰性导入：避免 config ↔ rotation 循环依赖

    fields = getattr(RotationPolicy, "__dataclass_fields__", None)
    if fields is None:  # 防御：对方若没用 dataclass，就用全量命名参数试一次
        return RotationPolicy(
            enabled=ROTATION_ENABLED,
            slice_s=ROTATION_SLICE_S,
            min_interval_s=ROTATION_MIN_INTERVAL_S,
            max_concurrency=ROTATION_MAX_CONCURRENCY,
            w_credit=ROTATION_W_CREDIT,
            w_runway=ROTATION_W_RUNWAY,
            w_cool=ROTATION_W_COOL,
            base_priority=ROTATION_BASE_PRIORITY,
            w_base=ROTATION_W_BASE,
            ema_halflife_s=ROTATION_EMA_HALFLIFE_S,
            min_rate=MIN_RATE,
        )
    # 只传对方真实声明过的字段：命名不一致时降级为「用对方的默认值」，
    # 而不是抛 TypeError 把网关照崩。两侧的契约差异在评测/联调时可见。
    wanted = {
        "enabled": ROTATION_ENABLED,
        "slice_s": ROTATION_SLICE_S,
        "min_interval_s": ROTATION_MIN_INTERVAL_S,
        "max_concurrency": ROTATION_MAX_CONCURRENCY,
        "w_credit": ROTATION_W_CREDIT,
        "w_runway": ROTATION_W_RUNWAY,
        "w_cool": ROTATION_W_COOL,
        "base_priority": ROTATION_BASE_PRIORITY,
        "w_base": ROTATION_W_BASE,
        "ema_halflife_s": ROTATION_EMA_HALFLIFE_S,
        "min_rate": MIN_RATE,
    }
    return RotationPolicy(**{k: v for k, v in wanted.items() if k in fields})


__all__ = [
    "HOST", "PORT", "API_KEY", "ensure_api_key",
    "CN_CHAT_BASE", "CN_BILLING_BASE",
    "GEMINI_API_KEY", "GEMINI_BASE",
    "DESKTOP_AUTH_DIR", "NODE_BIN", "READ_CREDS_JS",
    "STATE_DIR", "ACCOUNTS_FILE",
    "UPSTREAM_TIMEOUT", "REFRESH_MARGIN_MS", "RATE_LIMIT_COOLDOWN_S",
    "MAX_ROTATIONS", "LOG_LEVEL", "LOG_FILE", "ROOT",
    # 轮换
    "ROTATION_ENABLED", "ROTATION_SLICE_S", "ROTATION_MIN_INTERVAL_S",
    "ROTATION_MAX_CONCURRENCY", "ROTATION_W_CREDIT", "ROTATION_W_RUNWAY",
    "ROTATION_W_COOL", "ROTATION_W_BASE", "ROTATION_BASE_PRIORITY",
    "ROTATION_EMA_HALFLIFE_S", "MIN_RATE",
    "SAVE_THROTTLE_S", "rotation_policy",
    # 全自动签到
    "AUTO_CHECKIN_ENABLED", "AUTO_CHECKIN_INTERVAL_S", "AUTO_CHECKIN_JITTER_S",
    "AUTO_CHECKIN_ACCOUNT_GAP_S", "AUTO_CHECKIN_STATE_FILE",
]
