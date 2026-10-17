"""凭据发现与续期。

来源优先级：
  1. 网关自有库 state/accounts.json（刷新后的副本，跨重启复用）
  2. WorkBuddy 桌面端 auth 目录里的 *.info
     - 明文 JSON：直接读 auth.accessToken / auth.refreshToken
     - 加密字段（$wbEncrypted）：交给 tools/read_creds.mjs（复用插件的 at-rest 解密）

设计原则：**只读 App 的文件，永不改写**；刷新结果写进我们自己的库。

只支持**国内版**：国际版（`*.workbuddy.ai` / `*.codebuddy.ai`）的凭据会被直接忽略，
否则它们的 token 会被发到 `copilot.tencent.com`，只会换来 openresty 层的 401。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from . import config

log = logging.getLogger("gateway.creds")

#: 国际版域名后缀。命中即跳过：网关只连国内版上游。
_INTERNATIONAL_SUFFIXES = ("workbuddy.ai", "codebuddy.ai")


def is_supported_domain(domain: str) -> bool:
    """该凭据的签发域是否属于国内版。

    空 domain 视为支持（老格式的库文件里可能没写 domain），
    与过去「不认识就当国内版」的行为一致。
    """
    d = (domain or "").strip().lower()
    if not d:
        return True
    return not any(d == s or d.endswith("." + s) for s in _INTERNATIONAL_SUFFIXES)


@dataclass
class Credential:
    uin: str
    uid: str = ""
    domain: str = ""
    access_token: str = ""
    refresh_token: str = ""
    expires_at_ms: int = 0
    refresh_expires_at_ms: int = 0
    nickname: str = ""
    source: str = ""
    # 只读/临时构造的凭据不应被写进自有库（例如测试注入的假账号）
    persistable: bool = True
    # 运行期状态（不持久化）
    cooldown_until: float = 0.0
    exhausted: bool = False
    last_error: str = ""
    # ---- 跨重启累计计数（会持久化，见 docs/账号轮换策略设计.md §5）----
    # 这三个字段回答「谁已经被用过多少」，是轮换加权排序的输入。
    # 全部带默认值且追加在末尾：既有的位置/关键字构造方式都不受影响。
    credits_spent_total: float = 0.0   # 累计消耗积分
    requests_total: int = 0            # 累计请求数
    last_used_wall: float = 0.0        # 上次使用时间（wall clock，跨重启可比较）
    # 注意：last_used_wall 与调度器进程内的 monotonic last_used_at **不是**同一个东西，
    # 前者跨重启可比较，后者只在进程内有意义，两者各管一段，不要合并。

    @property
    def chat_base(self) -> str:
        return config.CN_CHAT_BASE

    @property
    def billing_base(self) -> str:
        return config.CN_BILLING_BASE

    @property
    def account_id(self) -> str:
        return self.uin or self.uid

    @property
    def token_valid(self) -> bool:
        return bool(self.access_token) and self.expires_at_ms > time.time() * 1000

    @property
    def refresh_valid(self) -> bool:
        return bool(self.refresh_token) and (
            self.refresh_expires_at_ms == 0 or self.refresh_expires_at_ms > time.time() * 1000
        )

    def needs_refresh(self) -> bool:
        if not self.refresh_token:
            return False
        if not self.access_token:
            return True
        return self.expires_at_ms - config.REFRESH_MARGIN_MS < time.time() * 1000

    def available(self) -> bool:
        if self.exhausted:
            return False
        if self.cooldown_until > time.monotonic():
            return False
        return self.refresh_valid or self.token_valid

    def public(self) -> dict:
        return {
            "account_id": self.account_id,
            "uin": self.uin,
            "uid": self.uid,
            "nickname": self.nickname,
            "domain": self.domain,
            "source": self.source,
            "expires_at": _iso(self.expires_at_ms),
            "refresh_expires_at": _iso(self.refresh_expires_at_ms),
            "token_valid": self.token_valid,
            "refresh_valid": self.refresh_valid,
            "available": self.available(),
            "exhausted": self.exhausted,
            "cooldown_remaining_s": max(0, int(self.cooldown_until - time.monotonic())),
            "last_error": self.last_error,
        }


def _iso(ms: int) -> Optional[str]:
    if not ms:
        return None
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ms / 1000))


# --------------------------------------------------------------------------
# 从 WorkBuddy 桌面端 auth 目录发现
# --------------------------------------------------------------------------

def _read_plaintext_info(path: Path) -> Optional[dict]:
    """解析明文 .info。返回 None 表示不是明文可读的凭据文件。"""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    auth = doc.get("auth")
    if not isinstance(auth, dict):
        return None
    token = auth.get("accessToken")
    if not isinstance(token, str):  # 加密字段是 dict
        return None
    acct = doc.get("account") if isinstance(doc.get("account"), dict) else {}
    return {
        "accessToken": token,
        "refreshToken": auth.get("refreshToken") if isinstance(auth.get("refreshToken"), str) else "",
        "domain": auth.get("domain") or "",
        "expiresAtMs": int(auth.get("expiresAt") or 0),
        "refreshExpiresAtMs": int(auth.get("refreshExpiresAt") or 0),
        "uid": acct.get("uid") or "",
        "uin": str(acct.get("uin") or ""),
        "nickname": acct.get("nickname") if isinstance(acct.get("nickname"), str) else "",
    }


def _read_via_node(path: Path) -> Optional[dict]:
    """加密字段交给 Node 助手解密（复用 dsh-connect-workbuddy 的实现）。"""
    if not config.READ_CREDS_JS.is_file():
        return None
    node = config.NODE_BIN if os.path.exists(config.NODE_BIN) else "node"
    try:
        proc = subprocess.run(
            [node, str(config.READ_CREDS_JS), str(path)],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("解密助手执行失败 %s: %s", path.name, exc)
        return None
    out = (proc.stdout or "").strip()
    if not out:
        log.warning("解密助手无输出 %s: %s", path.name, (proc.stderr or "")[:200])
        return None
    try:
        data = json.loads(out)
    except Exception:  # noqa: BLE001
        log.warning("解密助手输出无法解析 %s: %s", path.name, out[:200])
        return None
    if not data.get("ok"):
        log.info("解密助手报告不可用 %s: %s", path.name, data.get("reason"))
        return None
    return {
        "accessToken": data.get("accessToken") or "",
        "refreshToken": data.get("refreshToken") or "",
        "domain": data.get("domain") or "",
        "expiresAtMs": int(data.get("expiresAtMs") or 0),
        "refreshExpiresAtMs": int(data.get("refreshExpiresAtMs") or 0),
        "uid": data.get("uid") or "",
        "uin": str(data.get("uin") or ""),
        "nickname": data.get("nickname") or "",
    }


def discover_from_desktop() -> List[dict]:
    """扫描桌面端 auth 目录，返回所有可读凭据（去重前）。"""
    out: List[dict] = []
    auth_dir = config.DESKTOP_AUTH_DIR
    if not auth_dir.is_dir():
        log.warning("凭据目录不存在: %s", auth_dir)
        return out
    for path in sorted(auth_dir.glob("*.info")):
        parsed = _read_plaintext_info(path)
        if parsed is None:
            parsed = _read_via_node(path)
        if not parsed or not parsed.get("accessToken"):
            log.info("跳过 %s（无法读取）", path.name)
            continue
        if not is_supported_domain(parsed.get("domain") or ""):
            log.info("跳过 %s：国际版账号（%s），网关只支持国内版",
                     path.name, parsed.get("domain") or "未知域")
            continue
        parsed["source"] = path.name
        out.append(parsed)
    return out


def _coerce_float(raw, default: float = 0.0) -> float:
    """容错浮点解析：缺失/字符串/None/NaN/Inf/负数 → 默认值。

    旧版 accounts.json 没有这些字段，第三方手改过的文件可能有垃圾值；
    读档绝不能因为一个脏字段把整个账号丢掉。
    """
    if raw is None or isinstance(raw, bool):
        return default
    try:
        out = float(raw)
    except (TypeError, ValueError):
        return default
    if out != out or out in (float("inf"), float("-inf")):  # NaN / Inf
        return default
    if out < 0:  # 计数不可能为负：负数只可能是脏数据或时钟回拨
        return default
    return out


def _coerce_int(raw, default: int = 0) -> int:
    """容错整数解析。字符串 "12.9" / 12.9 这类也接受（向下取整），其余回落默认。"""
    if raw is None or isinstance(raw, bool):
        return default
    try:
        out = int(float(raw))
    except (TypeError, ValueError):
        return default
    if out < 0:
        return default
    return out


# --------------------------------------------------------------------------
# 网关自有库
# --------------------------------------------------------------------------

def load_store() -> Dict[str, Credential]:
    if not config.ACCOUNTS_FILE.is_file():
        return {}
    try:
        doc = json.loads(config.ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        log.warning("自有凭据库损坏，忽略")
        return {}
    creds: Dict[str, Credential] = {}
    for key, item in (doc.get("accounts") or {}).items():
        domain = item.get("domain") or ""
        if not is_supported_domain(domain):
            # 旧版本存进来的国际版账号：直接不再加载，下次落盘时自然被清掉
            log.info("跳过库中的国际版账号 %s（%s）：网关只支持国内版", key, domain)
            continue
        try:
            creds[key] = Credential(
                uin=str(item.get("uin") or key),
                uid=item.get("uid") or "",
                domain=domain,
                access_token=item.get("access_token") or "",
                refresh_token=item.get("refresh_token") or "",
                expires_at_ms=_coerce_int(item.get("expires_at_ms"), 0),
                refresh_expires_at_ms=_coerce_int(item.get("refresh_expires_at_ms"), 0),
                nickname=item.get("nickname") or "",
                source=item.get("source") or "vault",
                # 累计计数：旧格式文件没有这些键 → 走默认值，账号照常加载
                credits_spent_total=_coerce_float(item.get("credits_spent_total"), 0.0),
                requests_total=_coerce_int(item.get("requests_total"), 0),
                last_used_wall=_coerce_float(item.get("last_used_wall"), 0.0),
            )
        except Exception:  # noqa: BLE001
            continue
    return creds


# 上次真正写盘的时间（单调时钟）。热路径节流用，模块级即可——
# 进程内只有一个 state/accounts.json。
_last_save_monotonic: float = 0.0
_save_lock = threading.Lock()
_save_skipped: int = 0  # 被节流跳过的次数，便于观测节流是否过激


def last_save_monotonic() -> float:
    """上次真实写盘的时刻（time.monotonic()）；从未写过为 0.0。测试用。"""
    return _last_save_monotonic


def reset_save_throttle() -> None:
    """清空节流状态，让下一次 save_store 一定写盘。测试用。"""
    global _last_save_monotonic, _save_skipped
    _last_save_monotonic = 0.0
    _save_skipped = 0


def save_store(creds: Dict[str, Credential], force: bool = False) -> bool:
    """把自有库落盘。返回 True 表示这次真的写了。

    **原子性**：先写 `accounts.json.tmp`（0600）再 `replace`，任何时刻
    磁盘上都有一份完整的旧文件或新文件，不存在半截 JSON。

    **节流**：这是请求热路径上的调用（每完成一次请求都要更新计数），
    不能每个请求都重写整个文件。`SAVE_THROTTLE_S` 内跳过除非 `force=True`。
    代价是崩溃最多丢一个节流窗口的计数，而这些计数只用于**加权排序**，
    丢了只会让轮换短暂偏向，不影响正确性（设计文档 §5）。
    """
    global _last_save_monotonic, _save_skipped

    with _save_lock:
        now = time.monotonic()
        if not force and _last_save_monotonic:
            interval = config.SAVE_THROTTLE_S
            if interval > 0 and (now - _last_save_monotonic) < interval:
                _save_skipped += 1
                return False

        doc = {
            "version": 1,
            "updated_at": int(time.time()),
            "accounts": {
                key: {
                    "uin": c.uin, "uid": c.uid, "domain": c.domain,
                    "access_token": c.access_token, "refresh_token": c.refresh_token,
                    "expires_at_ms": c.expires_at_ms,
                    "refresh_expires_at_ms": c.refresh_expires_at_ms,
                    "nickname": c.nickname, "source": c.source,
                    # 累计计数（跨重启续上，设计文档 §5）
                    "credits_spent_total": round(
                        _coerce_float(getattr(c, "credits_spent_total", 0.0), 0.0), 6),
                    "requests_total": _coerce_int(getattr(c, "requests_total", 0), 0),
                    "last_used_wall": round(
                        _coerce_float(getattr(c, "last_used_wall", 0.0), 0.0), 3),
                }
                for key, c in creds.items()
                # 测试注入的假号永远不落盘：这条既有安全约束不变
                if c.persistable
            },
        }
        tmp = config.ACCOUNTS_FILE.with_suffix(".json.tmp")
        try:
            # mkdir 也在 try 内：state 目录不可建时同样只降级为「这次没写成」，
            # 绝不能把异常抛进请求热路径。
            config.STATE_DIR.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(config.ACCOUNTS_FILE)
        except OSError as exc:
            # 写盘失败不能把请求打挂：计数留在内存里，下次再试
            log.warning("自有凭据库落盘失败: %s", exc)
            try:
                tmp.unlink()
            except OSError:
                pass
            return False
        _last_save_monotonic = now
        return True


def merge(store: Dict[str, Credential], found: List[dict]) -> Dict[str, Credential]:
    """把发现到的凭据并入自有库。同一账号取过期时间更晚的那份。"""
    for item in found:
        key = item.get("uin") or item.get("uid")
        if not key:
            continue
        if not is_supported_domain(item.get("domain") or ""):
            log.info("跳过国际版凭据 %s（%s）：网关只支持国内版", key, item.get("domain"))
            continue
        existing = store.get(key)
        cand_exp = int(item.get("expiresAtMs") or 0)
        if existing is not None and existing.expires_at_ms > cand_exp:
            # 已有更新的 token，只补空字段
            if not existing.uid:
                existing.uid = item.get("uid") or ""
            continue
        store[key] = Credential(
            uin=str(item.get("uin") or key),
            uid=item.get("uid") or "",
            domain=item.get("domain") or "",
            access_token=item.get("accessToken") or "",
            refresh_token=item.get("refreshToken") or "",
            expires_at_ms=cand_exp,
            refresh_expires_at_ms=int(item.get("refreshExpiresAtMs") or 0),
            nickname=item.get("nickname") or "",
            source=item.get("source") or "desktop",
        )
    return store


__all__ = [
    "Credential", "is_supported_domain",
    "discover_from_desktop", "load_store", "save_store", "merge",
    "last_save_monotonic", "reset_save_throttle",
]
