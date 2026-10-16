"""凭据发现与续期。

来源优先级：
  1. 网关自有库 state/accounts.json（刷新后的副本，跨重启复用）
  2. WorkBuddy 桌面端 auth 目录里的 *.info
     - 明文 JSON：直接读 auth.accessToken / auth.refreshToken
     - 加密字段（$wbEncrypted）：交给 tools/read_creds.mjs（复用插件的 at-rest 解密）

设计原则：**只读 App 的文件，永不改写**；刷新结果写进我们自己的库。
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

from . import config

log = logging.getLogger("gateway.creds")


def region_of(domain: str) -> str:
    d = (domain or "").strip().lower()
    if d.endswith("workbuddy.ai") or d.endswith("codebuddy.ai"):
        return "global"
    return "cn"


def chat_base(domain: str) -> str:
    return config.GLOBAL_CHAT_BASE if region_of(domain) == "global" else config.CN_CHAT_BASE


def billing_base(domain: str) -> str:
    return config.GLOBAL_CHAT_BASE if region_of(domain) == "global" else config.CN_BILLING_BASE


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

    @property
    def region(self) -> str:
        return region_of(self.domain)

    @property
    def chat_base(self) -> str:
        return chat_base(self.domain)

    @property
    def billing_base(self) -> str:
        return billing_base(self.domain)

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
            "region": self.region,
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
        parsed["source"] = path.name
        out.append(parsed)
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
        try:
            creds[key] = Credential(
                uin=str(item.get("uin") or key),
                uid=item.get("uid") or "",
                domain=item.get("domain") or "",
                access_token=item.get("access_token") or "",
                refresh_token=item.get("refresh_token") or "",
                expires_at_ms=int(item.get("expires_at_ms") or 0),
                refresh_expires_at_ms=int(item.get("refresh_expires_at_ms") or 0),
                nickname=item.get("nickname") or "",
                source=item.get("source") or "vault",
            )
        except Exception:  # noqa: BLE001
            continue
    return creds


def save_store(creds: Dict[str, Credential]) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
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
            }
            for key, c in creds.items()
            if c.persistable
        },
    }
    tmp = config.ACCOUNTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(config.ACCOUNTS_FILE)


def merge(store: Dict[str, Credential], found: List[dict]) -> Dict[str, Credential]:
    """把发现到的凭据并入自有库。同一账号取过期时间更晚的那份。"""
    for item in found:
        key = item.get("uin") or item.get("uid")
        if not key:
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
    "Credential", "region_of", "chat_base", "billing_base",
    "discover_from_desktop", "load_store", "save_store", "merge",
]
