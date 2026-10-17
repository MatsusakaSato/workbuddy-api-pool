"""上游调用：对话、续期、积分、签到、模型目录。

全部用标准库 urllib，零第三方依赖。

实测结论（2026-10-16）：
  - 对话必须 stream=true，非流式返回业务码 11101
  - 响应是标准 OpenAI chat.completion.chunk SSE，可直接透传
  - 续期走 POST {chat}/v2/plugin/auth/token/refresh，需 X-Refresh-Token
  - 计费类接口必须带 Origin / Referer / X-Requested-With，否则 10085
"""
from __future__ import annotations

import calendar
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, Iterator, List, Optional, Tuple

from . import config
from .creds import Credential

log = logging.getLogger("gateway.upstream")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) WorkBuddy/5.8.2 Chrome/138.0.0.0 Electron/38.0.0 Safari/537.36"
)


class UpstreamError(Exception):
    """上游业务或传输错误。kind 用于决定要不要换号。"""

    def __init__(self, kind: str, message: str, status: int = 0, code: Optional[int] = None,
                 retry_after_s: Optional[float] = None):
        super().__init__(message)
        self.kind = kind          # auth | rate | credit | busy | policy | server | client
        self.message = message
        self.status = status
        self.code = code
        # 上游给的重置时间（秒）。有的话比默认冷却精确得多。
        self.retry_after_s = retry_after_s

    def as_dict(self) -> dict:
        return {"kind": self.kind, "message": self.message, "status": self.status,
                "code": self.code, "retry_after_s": self.retry_after_s}


# --------------------------------------------------------------------------
# 限流重置时间解析
#
# 上游的 429 **不带** Retry-After 头，但把重置时间写在响应体里，形如：
#   {"code":6004,"msg":"…将在 2026-09-30 02:30:30 UTC+8 重置…"}
# 解析不出来就返回 None，由调用方退回默认冷却。
# --------------------------------------------------------------------------

_RESET_WITH_OFFSET = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})\s*UTC\s*([+-]\d{1,2})(?::?(\d{2}))?"
)
# 不带偏移时按**本地时区**理解：上游是中文服务，省掉偏移大概率就是本地时间。
# 用 UTC 解析会整整差一个时区（UTC+8 下 900 秒被算成 29700 秒）。
_RESET_NO_OFFSET = re.compile(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})")


def _valid_parts(y: int, mo: int, d: int, h: int, mi: int, s: int) -> bool:
    """先自己校验范围。

    注意 time.mktime 对越界值是**归一化**而不是报错（13 月会变成次年 1 月、
    45 日会变成 2 月中），所以不能靠它兜底，否则乱码时间会算出一个假冷却。
    """
    return (
        1970 <= y <= 9999
        and 1 <= mo <= 12
        and 1 <= d <= 31
        and 0 <= h <= 23
        and 0 <= mi <= 59
        and 0 <= s <= 60  # 容忍闰秒
    )


def parse_reset_seconds(text: str, now: Optional[float] = None) -> Optional[float]:
    """从响应体文本里解析「还有多少秒重置」。解析不出返回 None。"""
    if not text:
        return None
    now = time.time() if now is None else now

    target: Optional[float] = None
    m = _RESET_WITH_OFFSET.search(text)
    if m:
        try:
            y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
            off_h = int(m.group(7))
            off_m = int(m.group(8) or 0)
            # 负数偏移（如 UTC-5）的额外分钟数也要跟着变号
            offset_s = off_h * 3600 + (off_m * 60 if off_h >= 0 else -off_m * 60)
            if _valid_parts(y, mo, d, h, mi, s):
                wall = calendar.timegm((y, mo, d, h, mi, s, 0, 0, 0))
                target = wall - offset_s
        except (TypeError, ValueError, OverflowError):
            target = None
    else:
        m = _RESET_NO_OFFSET.search(text)
        if m:
            try:
                y, mo, d, h, mi, s = (int(m.group(i)) for i in range(1, 7))
                if _valid_parts(y, mo, d, h, mi, s):
                    target = time.mktime((y, mo, d, h, mi, s, 0, 0, -1))
            except (TypeError, ValueError, OverflowError):
                target = None

    if target is None:
        return None
    delta = target - now
    if delta <= 0:
        return None
    # 上限一天，避免上游写错年份就把账号封死
    return float(min(delta, 86400))


def _retry_after_from(headers, body_text: str) -> Optional[float]:
    """先看 Retry-After 头，再看响应体。"""
    if headers is not None:
        try:
            raw = headers.get("Retry-After")
        except Exception:  # noqa: BLE001
            raw = None
        if raw:
            try:
                return float(min(float(str(raw).strip()), 86400))
            except ValueError:
                pass
    return parse_reset_seconds(body_text)


# --------------------------------------------------------------------------
# 头部构造
# --------------------------------------------------------------------------

def _common_headers(cred: Credential) -> Dict[str, str]:
    origin = cred.billing_base
    return {
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin + "/",
        "User-Agent": USER_AGENT,
    }


def chat_headers(cred: Credential) -> Dict[str, str]:
    h = _common_headers(cred)
    h["Content-Type"] = "application/json"
    h["Accept"] = "text/event-stream"
    h["Authorization"] = "Bearer " + cred.access_token
    if cred.uid:
        h["X-User-Id"] = cred.uid
    else:
        h["X-No-User-Id"] = "1"
    h["X-No-Enterprise-Id"] = "1"
    if cred.domain:
        h["X-Domain"] = cred.domain
    else:
        h["X-No-Department-Info"] = "1"
    h["X-Product"] = "SaaS"
    return h


def billing_headers(cred: Credential) -> Dict[str, str]:
    h = _common_headers(cred)
    h["Content-Type"] = "application/json"
    h["Accept"] = "application/json"
    h["Authorization"] = "Bearer " + cred.access_token
    if cred.uid:
        h["X-User-Id"] = cred.uid
    if cred.domain:
        h["X-Domain"] = cred.domain
    h["X-No-Enterprise-Id"] = "1"
    return h


def refresh_headers(cred: Credential) -> Dict[str, str]:
    h = _common_headers(cred)
    h["Content-Type"] = "application/json"
    h["X-Refresh-Token"] = cred.refresh_token
    h["X-Auth-Refresh-Source"] = "workbuddy"
    if cred.uid:
        h["X-User-Id"] = cred.uid
    return h


# --------------------------------------------------------------------------
# 错误分类
# --------------------------------------------------------------------------

_RATE_MARKERS = ("rate limit", "too many requests", "请求过于频繁", "限流", "frequency")
_CREDIT_MARKERS = ("insufficient credit", "no credit", "credit exhausted",
                   "积分不足", "额度不足", "余额不足", "积分用完", "额度用尽")
_CREDIT_CODES = {14018, 10085 + 10000}  # 14018 = 官方积分耗尽码


def classify(status: int, body: str, code: Optional[int]) -> str:
    low = (body or "").lower()
    # 10110=用户未绑定 10111=凭据过期 10113=用户票据过期
    if status == 401 or code in (401, 10110, 10111, 10113):
        return "auth"
    if status == 429 or any(m in low for m in _RATE_MARKERS):
        return "rate"
    if code in (14018,) or any(m in low for m in _CREDIT_MARKERS):
        return "credit"
    if status == 403:
        return "policy"
    if status >= 500:
        return "server"
    if status == 400:
        return "client"
    if code not in (0, None):
        return "server"
    return "server"


def _read_json(resp) -> Tuple[int, dict]:
    raw = resp.read().decode("utf-8", "replace")
    try:
        return resp.status, json.loads(raw)
    except Exception:  # noqa: BLE001
        return resp.status, {"_raw": raw[:500]}


# --------------------------------------------------------------------------
# 具体调用
# --------------------------------------------------------------------------

def refresh_token(cred: Credential) -> Credential:
    """续期。成功则就地更新 cred，返回它。失败抛 UpstreamError。"""
    url = cred.chat_base + "/v2/plugin/auth/token/refresh"
    req = urllib.request.Request(url, data=b"{}", headers=refresh_headers(cred), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            status, doc = _read_json(resp)
    except urllib.error.HTTPError as exc:
        status, doc = exc.code, {"_raw": exc.read().decode("utf-8", "replace")[:300]}
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("server", f"续期传输失败: {exc}") from exc

    code = doc.get("code")
    if status != 200 or code != 0:
        raise UpstreamError(classify(status, json.dumps(doc, ensure_ascii=False), code),
                            f"续期被拒: HTTP {status} code={code} {str(doc)[:200]}", status, code)
    data = doc.get("data") or {}
    now = int(time.time() * 1000)
    cred.access_token = data.get("accessToken") or cred.access_token
    cred.refresh_token = data.get("refreshToken") or cred.refresh_token
    exp_in = int(data.get("expiresIn") or 0)
    ref_in = int(data.get("refreshExpiresIn") or 0)
    if exp_in:
        cred.expires_at_ms = now + exp_in * 1000
    if ref_in:
        cred.refresh_expires_at_ms = now + ref_in * 1000
    if data.get("domain"):
        cred.domain = data["domain"]
    log.info("续期成功 uin=%s 新有效期至 %s", cred.uin,
             time.strftime("%Y-%m-%d %H:%M", time.localtime(cred.expires_at_ms / 1000)))
    return cred


def open_chat_stream(cred: Credential, body: dict) -> Tuple[object, str, dict]:
    """发起流式对话。返回 (response, model, extra)。调用方负责关闭。

    上游强制流式：这里始终 stream=true。
    """
    payload = dict(body)
    payload["stream"] = True
    url = cred.chat_base + "/v2/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=chat_headers(cred), method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=config.UPSTREAM_TIMEOUT)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        code = None
        try:
            code = json.loads(raw).get("code")
        except Exception:  # noqa: BLE001
            pass
        raise UpstreamError(classify(exc.code, raw, code),
                            f"HTTP {exc.code} code={code} {raw[:200]}", exc.code, code,
                            retry_after_s=_retry_after_from(exc.headers, raw)) from exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("server", f"对话传输失败: {exc}") from exc

    # 上游可能用 200 + 业务错误体回绝
    ctype = resp.headers.get("Content-Type", "")
    if "event-stream" not in ctype:
        raw = resp.read().decode("utf-8", "replace")
        resp.close()
        code = None
        try:
            code = json.loads(raw).get("code")
        except Exception:  # noqa: BLE001
            pass
        raise UpstreamError(classify(resp.status, raw, code),
                            f"非流式回绝 code={code} {raw[:200]}", resp.status, code,
                            retry_after_s=_retry_after_from(resp.headers, raw))
    return resp, payload.get("model", ""), {}


def iter_sse(resp) -> Iterator[bytes]:
    """把上游 SSE 逐块吐出来。"""
    while True:
        chunk = resp.read(4096)
        if not chunk:
            break
        yield chunk


# --------------------------------------------------------------------------
# 积分余额：解析「基础套餐」与「平台奖励」两个桶
#
# ## 为什么必须分桶读（2026-10-17 实测）
#
# `get-user-resource` 的 `Data.Accounts[]` 里，**每个账号包一行**，用
# `CapacityType` 区分种类：
#
#   CapacityType == 4  月度订阅套餐 —— 官方客户端文案「**套餐基础积分**」
#   CapacityType == 1  一次性赠送包（拉新权益包 / 国内运营裂变包 / 签到奖励）
#                      —— 官方客户端文案「**平台奖励积分**」
#
# 上游顺手给的 `Data.TotalDosage` **不是可花余额**：
#   · 实测 330127845755 的 TotalDosage=2700，而逐行相加只有 2700 - (500-307.45)
#     = 2507.45 —— 它把月度套餐的**池值**（CapacityRemain=500）算进去了。
#   · 月度套餐的池值回答的是「这个套餐一共给了多少」，不是「这个周期还剩多少」。
#     同一天实测 330100178623 返回 CapacityRemain=500 / CycleCapacityRemain=0
#     ——「一点没用、全都还在」，而它本周期其实已经用光，上游正用 14018
#     拒绝它的请求。按 TotalDosage 读余额，面板就会把一个耗尽的号显示成还有 2354。
#
# 所以：**余额一律逐行读周期家族 `CycleCapacityRemain`（`*Precise` 优先）**，
# 两种套餐统一口径；完全没有周期字段的合成行才回退读池 `CapacityRemain`。
# 这与 dsh-connect-workbuddy 4.4.5（issue #58）用官方客户端截图定下的口径一致：
#   总剩余积分 2,196.61 = 套餐基础积分 500 + 平台奖励积分 1,696.61
#
# ## 两个桶各自的使用规则（决定了轮换为什么优先花基础积分）
#
#   · 基础套餐（月度周期）：**月底清零**。今天不用，明天也没了 —— 这是**沉没成本**，
#     必须先花；而且消耗顺序上它不被保留，留着只是浪费。
#   · 平台奖励（一次性包）：**各自有过期时间，但普遍很长**（实测 +13 ~ +31 天，
#     而月度周期只有十几天）。用不完的零头可以留给下个月。
#
# 于是「优先使用基础套餐积分」= 在**同样的可用性**前提下，把请求优先派给
# 基础积分还有剩、且奖励积分同样充足的号 —— 详见 rotation.py 第 3 层。
# --------------------------------------------------------------------------

#: 月度订阅套餐的 CapacityType。其余（实测只有 1）都是赠送/奖励包。
CAPACITY_TYPE_BASE = 4

#: 整包视为「完全不可用」的上游 Status 值（实测 3 = 已失效/已用尽）
_CAPACITY_STATUS_DEAD = 3


def package_rows(doc) -> List[dict]:
    """从 `get-user-resource` 的完整响应里取出逐包明细 `Data.Accounts[]`。

    与 `fetch_credits` 共用这条路径，测试因此可以喂**真实响应原文**，
    而不必自己复刻一遍嵌套结构（复刻出来的夹具正是过去字段解析零覆盖的根源）。
    """
    try:
        rows = doc["data"]["Response"]["Data"]["Accounts"]
    except (KeyError, TypeError):
        return []
    return rows if isinstance(rows, list) else []


def _num(raw, default: float = 0.0) -> float:
    """容错数值解析：上游的精确值字段是**字符串**（"1318.14000014"）。"""
    if raw is None or isinstance(raw, bool):
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if val != val or val in (float("inf"), float("-inf")):  # NaN / ±Inf
        return default
    return val


def _precise(row: dict, key: str) -> Optional[float]:
    """读 `<key>Precise`（字符串，带小数）→ 回退 `key`（整数）。都没有返回 None。

    **不能 `or` 串联**：精确值为 0 时 `or` 会继续去读池值，把「本周期 0」
    读成「池 500」。必须按键是否存在判断。
    """
    for name in (key + "Precise", key):
        if name in row and row[name] not in (None, ""):
            return _num(row[name])
    return None


def _parse_ts(raw) -> Optional[float]:
    """把上游时间字段转成 Unix 秒。支持毫秒时间戳与 "YYYY-MM-DD HH:MM:SS"。

    上游同一个响应里两种格式混用：`CreateTime` 是毫秒整数，
    `ExpiredTime` 是本地时间字符串（空串表示永不过期）。
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        val = float(raw)
        return val / 1000.0 if val > 1e11 else val
    if isinstance(raw, str) and raw.strip():
        try:
            return float(time.mktime(time.strptime(raw.strip()[:19], "%Y-%m-%d %H:%M:%S")))
        except (ValueError, OverflowError):
            return None
    return None


def _classify_package(row: dict, now: float) -> Optional[dict]:
    """把一条 Accounts[] 行归类成 {kind, remain, size, expires_at}。

    kind ∈ {"base", "reward"}；返回 None 表示这一行不贡献可用余额。
    """
    if not isinstance(row, dict):
        return None
    ctype = int(_num(row.get("CapacityType"), 0))
    kind = "base" if ctype == CAPACITY_TYPE_BASE else "reward"

    # 余额：周期家族优先（两种套餐统一），无周期字段的合成行回退池值。
    if "CycleCapacityRemain" in row or "CycleCapacityRemainPrecise" in row:
        remain = _precise(row, "CycleCapacityRemain") or 0.0
    else:
        remain = _precise(row, "CapacityRemain") or 0.0
    size = _precise(row, "CycleCapacitySize")
    if size is None:
        size = _precise(row, "CapacitySize") or 0.0

    status = int(_num(row.get("Status"), 0))
    expired_ms = row.get("ExpiredTime")
    expired = _parse_ts(expired_ms)
    cycle_end = _parse_ts(row.get("CycleEndTime"))

    # 失效包不计入任何桶（与插件的丢弃规则一致）。
    if status == _CAPACITY_STATUS_DEAD:
        return None
    if expired is not None and expired <= now:
        return None

    # 基础套餐过期时间 = 本周期结束（它在周期结束后重置，不是"作废"）；
    # 奖励包用各自的 ExpiredTime，缺失时退回周期结束。
    expires_at = cycle_end if kind == "base" else (expired if expired is not None else cycle_end)

    return {
        "kind": kind,
        "name": str(row.get("PackageName") or ""),
        "remain": max(0.0, remain),
        "size": max(0.0, size),
        "expires_at": expires_at,
    }


def summarize_credits(rows: List[dict], total_dosage=None, now: Optional[float] = None) -> dict:
    """把 `Data.Accounts[]` 汇总成两个桶 + 总余额。**纯函数，可离线单测。**

    返回值（同时也是下发给前端 / 桌面面板的形状）:

        {
          "total_credits": float,        # 可花总余额 = 基础 + 奖励
          "base_credits":  float,        # 基础套餐积分（月度周期，月底清零）
          "reward_credits": float,       # 平台奖励积分（一次性赠送包）
          "total_dosage":  float | None, # 上游 TotalDosage，**仅用于对账，不作为余额**
          "base_refresh_at": float | None,     # 基础积分最近的一次刷新时刻（周期结束）
          "reward_expiring_soon_at": float|None, # 奖励积分里最先过期的那批的过期时刻
          "reward_expiring_soon": float,  # 24 小时内会过期的奖励积分
          "packages": [{kind,name,remain,size,expires_at}],  # 明细，面板悬停用
        }
    """
    now = time.time() if now is None else float(now)
    pkgs: List[dict] = []
    for row in rows or []:
        item = _classify_package(row, now)
        if item is not None:
            pkgs.append(item)

    base = sum(p["remain"] for p in pkgs if p["kind"] == "base")
    reward = sum(p["remain"] for p in pkgs if p["kind"] == "reward")

    base_refresh = [p["expires_at"] for p in pkgs
                    if p["kind"] == "base" and p["expires_at"] is not None]
    soon_cutoff = now + 24 * 3600
    reward_exp = [p["expires_at"] for p in pkgs
                  if p["kind"] == "reward" and p["expires_at"] is not None]
    expiring_soon = sum(
        p["remain"] for p in pkgs
        if p["kind"] == "reward" and p["expires_at"] is not None and p["expires_at"] <= soon_cutoff
    )

    # 明细按"先用谁"排序：基础在前、到期早的在前 —— 前端悬停时直接按顺序读。
    pkgs.sort(key=lambda p: (0 if p["kind"] == "base" else 1,
                             p["expires_at"] if p["expires_at"] is not None else float("inf")))

    return {
        "total_credits": round(base + reward, 4),
        "base_credits": round(base, 4),
        "reward_credits": round(reward, 4),
        "total_dosage": _num(total_dosage) if total_dosage is not None else None,
        "base_refresh_at": min(base_refresh) if base_refresh else None,
        "reward_expiring_soon": round(expiring_soon, 4),
        "reward_expiring_soon_at": min(reward_exp) if reward_exp else None,
        "packages": pkgs,
    }


def fetch_credits(cred: Credential) -> dict:
    url = cred.billing_base + "/v2/billing/meter/get-user-resource"
    req = urllib.request.Request(url, data=b"{}", headers=billing_headers(cred), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            status, doc = _read_json(resp)
    except urllib.error.HTTPError as exc:
        raise UpstreamError(classify(exc.code, "", None), f"积分查询 HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("server", f"积分查询失败: {exc}") from exc
    if status != 200 or doc.get("code") != 0:
        raise UpstreamError(classify(status, json.dumps(doc), doc.get("code")),
                            f"积分查询被拒 code={doc.get('code')}")
    try:
        data = doc["data"]["Response"]["Data"]
    except Exception:  # noqa: BLE001
        return {"raw": doc.get("data")}

    rows = package_rows(doc)
    if not rows:
        # 没有逐包明细时**不猜**：宁可使余额未知（面板显示 "—"），
        # 也不回落到 TotalDosage —— 它把月度池值当成余额，正是这次要修的错。
        return {"raw": doc.get("data"), "total_count": data.get("TotalCount")}

    out = summarize_credits(rows, total_dosage=data.get("TotalDosage"))
    out["total_count"] = data.get("TotalCount")
    return out


def checkin_status(cred: Credential) -> dict:
    url = cred.billing_base + "/v2/billing/meter/checkin-activity-status"
    req = urllib.request.Request(url, data=b"{}", headers=billing_headers(cred), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            status, doc = _read_json(resp)
    except urllib.error.HTTPError as exc:
        raise UpstreamError(classify(exc.code, "", None), f"签到状态 HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("server", f"签到状态失败: {exc}") from exc
    if status != 200 or doc.get("code") != 0:
        raise UpstreamError(classify(status, json.dumps(doc), doc.get("code")),
                            f"签到状态被拒 code={doc.get('code')}")
    d = doc.get("data") or {}
    return {
        "active": d.get("active"),
        "today_checked_in": d.get("today_checked_in"),
        "streak_days": d.get("streak_days"),
        "daily_credit": d.get("daily_credit"),
        "today_credit": d.get("today_credit"),
        "total_credits": d.get("total_credits"),
        "activity_name": d.get("activity_name"),
        "theme_name": d.get("theme_name"),
        "end_time": d.get("end_time"),
        "claim_button_text": d.get("claim_button_text"),
    }


def claim_checkin(cred: Credential) -> dict:
    """真实领取今日奖励。**只在用户显式确认后调用。**"""
    url = cred.billing_base + "/v2/billing/meter/daily-checkin"
    req = urllib.request.Request(url, data=b"{}", headers=billing_headers(cred), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            status, doc = _read_json(resp)
    except urllib.error.HTTPError as exc:
        raise UpstreamError(classify(exc.code, "", None), f"签到 HTTP {exc.code}") from exc
    except Exception as exc:  # noqa: BLE001
        raise UpstreamError("server", f"签到失败: {exc}") from exc
    if status != 200 or doc.get("code") != 0:
        raise UpstreamError(classify(status, json.dumps(doc), doc.get("code")),
                            f"签到被拒 code={doc.get('code')} {str(doc.get('msg'))[:120]}")
    d = doc.get("data") or {}
    return {"credited": d.get("credit") or d.get("daily_credit") or d.get("today_credit"),
            "streak_days": d.get("streak_days"), "raw": d}


# 产品令牌决定返回哪个渠道的名单：CLI 通道 / 桌面通道。
# 国际版（桌面 UA `WorkBuddy/5.5.2`）已整体移除，这里只剩国内版需要的那一个。
CLIENT_UA = "CLI/2.63.2 CodeBuddy/2.63.2"

CONFIG_PATH = "/v3/config"
FALLBACK_CATALOG_PATH = "/v2/enterprises/personal/models"


def _catalog_headers(cred: Credential) -> Dict[str, str]:
    """目录是 GET，且 UA 决定名单内容（国内版必须用 CLI UA，否则名单不同）。"""
    h = {
        "Authorization": "Bearer " + cred.access_token,
        "Accept": "application/json",
        "X-Product": "SaaS",
        "X-Requested-With": "XMLHttpRequest",
        "Connection": "close",
        "User-Agent": CLIENT_UA,
    }
    if cred.uid:
        h["X-User-Id"] = cred.uid
    if cred.domain:
        h["X-Domain"] = cred.domain
    return h


def fetch_models(cred: Credential) -> List[dict]:
    """拉取模型目录。GET /v3/config，回退 /v2/enterprises/personal/models。"""
    for path in (CONFIG_PATH, FALLBACK_CATALOG_PATH):
        url = cred.billing_base + path
        req = urllib.request.Request(url, headers=_catalog_headers(cred), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                status, doc = _read_json(resp)
        except Exception as exc:  # noqa: BLE001
            log.debug("目录 %s 拉取失败：%s", path, exc)
            continue
        if status != 200 or doc.get("code") not in (0, None):
            log.debug("目录 %s 被拒：code=%s", path, doc.get("code"))
            continue
        models = _extract_models(doc.get("data"))
        if models:
            log.info("目录 %s 解析出 %d 个模型", path, len(models))
            return models
    return []


def _parse_cost(raw) -> Optional[float]:
    """把上游的倍率字符串解析成数值，供排序与「免费」判定。

    上游给的是人读的字符串（例如 "x0.79 credits"），既不能排序也不能比较。
    0 表示免费 —— 这是选模型时最有价值的属性，所以必须转成数字。
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if not isinstance(raw, str):
        return None
    m = re.search(r"\d+(?:\.\d+)?", raw)
    return float(m.group(0)) if m else None


def _parse_model(raw) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    mid = raw.get("id")
    if not isinstance(mid, str) or not mid or raw.get("disabled") is True:
        return None
    max_in = raw.get("maxInputTokens") if isinstance(raw.get("maxInputTokens"), int) else 0
    max_out = raw.get("maxOutputTokens") if isinstance(raw.get("maxOutputTokens"), int) else 0
    credits = raw.get("credits")
    return {
        "id": mid,
        "name": raw.get("name") if isinstance(raw.get("name"), str) and raw["name"] else mid,
        "context_window": max_in or None,
        "max_tokens": max_out or None,
        "credits": credits,
        "cost": _parse_cost(credits),
    }


def _extract_models(data) -> List[dict]:
    """解析 {models:[...], agents:[...]}，优先取 cli agent 的名单（与 App 的 roster 对齐）。"""
    if not isinstance(data, dict):
        return []
    by_id: Dict[str, dict] = {}
    raw_models = data.get("models")
    if isinstance(raw_models, list):
        for raw in raw_models:
            parsed = _parse_model(raw)
            if parsed:
                by_id[parsed["id"]] = parsed

    cli_ids: Optional[List[str]] = None
    for agent in data.get("agents") or []:
        if isinstance(agent, dict) and agent.get("name") == "cli" and isinstance(agent.get("models"), list):
            cli_ids = [i for i in agent["models"] if isinstance(i, str)]
            break

    ordered = [by_id[i] for i in cli_ids if i in by_id] if cli_ids else list(by_id.values())
    # cli 名单外的模型补在后面，保证 /v1/models 不丢东西
    seen = {m["id"] for m in ordered}
    ordered.extend(m for m in by_id.values() if m["id"] not in seen)
    return ordered


__all__ = [
    "UpstreamError", "chat_headers", "billing_headers", "refresh_headers",
    "classify", "refresh_token", "open_chat_stream", "iter_sse",
    "fetch_credits", "summarize_credits", "package_rows", "CAPACITY_TYPE_BASE",
    "checkin_status", "claim_checkin", "fetch_models",
]
