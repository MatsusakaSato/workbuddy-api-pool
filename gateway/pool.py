"""账号池：选择、失败分类、轮换、续期、签到。

选择策略（可用优先）：
  1. 排除已耗尽 / 冷却中 / 凭据过期的账号
  2. 同区域优先
  3. 积分高的优先（拿到积分后才排序，拿不到就按凭据新鲜度）
  4. 失败时依次换下一个账号，只有「请求本身不合法」不换号
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import config, creds, upstream
from .creds import Credential
from .upstream import UpstreamError

log = logging.getLogger("gateway.pool")

# 模型名前缀 → 区域启发式（目录不可用时兜底）
_GLOBAL_HINTS = ("gemini", "gpt-", "gpt5", "gpt6", "gpt-5", "gpt-6")
_CN_HINTS = ("glm", "kimi", "minimax", "hy3", "hy4", "auto")


class Pool:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._creds: Dict[str, Credential] = {}
        self._credits: Dict[str, int] = {}
        self._spent: Dict[str, float] = {}       # 本次进程内按账号累计的积分消耗
        self._requests: Dict[str, int] = {}      # 本次进程内按账号的请求数
        self._catalogs: Dict[str, List[str]] = {"cn": [], "global": []}
        self._catalog_models: Dict[str, List[dict]] = {"cn": [], "global": []}
        self.last_reload = 0.0
        self.last_credit_refresh = 0.0

    # ---------------- 生命周期 ----------------

    def reload(self, deep: bool = True) -> dict:
        """重新扫描凭据并合并进自有库。deep=True 时刷新积分与模型目录。"""
        with self._lock:
            store = creds.load_store()
            found = creds.discover_from_desktop()
            self._creds = creds.merge(store, found)
            creds.save_store(self._creds)
            self.last_reload = time.time()
            n = len(self._creds)
        log.info("账号池重载：%d 个账号", n)
        if deep:
            self.refresh_all_tokens()
            self.refresh_credits()
            self.refresh_catalogs()
        return self.snapshot()

    def ensure_loaded(self) -> None:
        with self._lock:
            if not self._creds:
                pass
            else:
                return
        self.reload(deep=False)

    # ---------------- 凭据维护 ----------------

    def _refresh_locked(self, cred: Credential) -> tuple:
        """返回 (是否可用, 是否真的发生了续期)。只有真的续期了才需要落盘。"""
        if not cred.needs_refresh() or not cred.refresh_token:
            return cred.token_valid, False
        try:
            upstream.refresh_token(cred)
            return True, True
        except UpstreamError as exc:
            cred.last_error = exc.message
            if exc.kind == "auth":
                log.warning("账号 %s 续期被拒（refresh 已失效）", cred.uin)
            else:
                log.warning("账号 %s 续期失败：%s", cred.uin, exc.message)
            return cred.token_valid, False

    def ensure_fresh(self, cred: Credential) -> bool:
        with self._lock:
            ok, changed = self._refresh_locked(cred)
            # 只有续期成功才写盘：否则临时/注入的凭据会被顺手持久化
            if changed:
                creds.save_store(self._creds)
            return ok

    def refresh_all_tokens(self) -> None:
        """逐个续期。ensure_fresh 内部只在真的续期成功时落盘。"""
        for c in self.list_accounts():
            self.ensure_fresh(c)

    def refresh_credits(self) -> None:
        for c in self.list_accounts():
            if not c.token_valid:
                continue
            try:
                info = upstream.fetch_credits(c)
                if isinstance(info.get("total_credits"), int):
                    self._credits[c.account_id] = info["total_credits"]
            except UpstreamError as exc:
                log.debug("账号 %s 积分查询失败：%s", c.uin, exc.message)
        self.last_credit_refresh = time.time()

    def record_usage(self, cred: Credential, usage: Optional[dict]) -> None:
        """从上游 usage 里累计消耗。上游会把本次花费放在 credit 字段。"""
        if not isinstance(usage, dict):
            return
        with self._lock:
            self._requests[cred.account_id] = self._requests.get(cred.account_id, 0) + 1
            raw = usage.get("credit")
            try:
                spent = float(raw)
            except (TypeError, ValueError):
                return
            if spent:
                self._spent[cred.account_id] = self._spent.get(cred.account_id, 0.0) + spent

    def refresh_catalogs(self) -> None:
        for region in ("cn", "global"):
            cand = [c for c in self.list_accounts() if c.region == region and c.token_valid]
            if not cand:
                continue
            try:
                models = upstream.fetch_models(cand[0])
                with self._lock:
                    self._catalog_models[region] = models
                    self._catalogs[region] = [m["id"] for m in models]
                log.info("%s 区域模型目录：%d 个", region, len(models))
            except Exception as exc:  # noqa: BLE001
                log.debug("%s 区域目录拉取失败：%s", region, exc)

    # ---------------- 路由与选择 ----------------

    def route(self, model: str) -> List[str]:
        """决定该模型应该用哪个（些）区域，按优先顺序返回。"""
        m = (model or "").strip()
        if m.startswith("cn/"):
            return ["cn"]
        if m.startswith("global/"):
            return ["global"]
        low = m.lower()
        regions: List[str] = []
        for region, ids in self._catalogs.items():
            if low in [i.lower() for i in ids]:
                regions.append(region)
        if regions:
            # 两边都有时国内版优先（延迟低、有签到）
            regions.sort(key=lambda r: 0 if r == "cn" else 1)
            return regions
        if any(h in low for h in _GLOBAL_HINTS):
            return ["global", "cn"]
        if any(h in low for h in _CN_HINTS):
            return ["cn", "global"]
        return ["cn", "global"]

    def list_accounts(self) -> List[Credential]:
        with self._lock:
            return list(self._creds.values())

    def candidates(self, regions: List[str]) -> List[Credential]:
        now = time.monotonic()
        with self._lock:
            pool = [c for c in self._creds.values() if c.region in regions]
        usable = [c for c in pool if c.available()]
        if not usable:
            usable = [c for c in pool if not c.exhausted]  # 冷却中的也允许最后一搏
        # 可用优先，其次积分高，其次凭据新鲜
        def key(c: Credential):
            return (
                0 if c.cooldown_until <= now else 1,
                -(self._credits.get(c.account_id, 0)),
                -c.expires_at_ms,
            )
        return sorted(usable, key=key)

    def pick(self, model: str, exclude: Optional[set] = None) -> Credential:
        exclude = exclude or set()
        regions = self.route(model)
        for cred in self.candidates(regions):
            if cred.account_id in exclude:
                continue
            if self.ensure_fresh(cred):
                return cred
        # 放宽：任何区域的可用账号
        for cred in self.candidates(["cn", "global"]):
            if cred.account_id in exclude:
                continue
            if self.ensure_fresh(cred):
                return cred
        raise UpstreamError("server", "没有可用账号：请先在 WorkBuddy 里登录，或等待冷却结束")

    # ---------------- 失败处理 ----------------

    def report_failure(self, cred: Credential, exc: UpstreamError) -> bool:
        """记录失败。返回 True 表示应该换号重试。"""
        cred.last_error = exc.message
        if exc.kind == "rate":
            # 上游给了精确重置时间就用它，否则退回默认冷却
            cooldown = exc.retry_after_s if exc.retry_after_s else config.RATE_LIMIT_COOLDOWN_S
            cred.cooldown_until = time.monotonic() + cooldown
            log.warning("账号 %s 被限流，冷却 %ds%s", cred.uin, int(cooldown),
                        "（上游给出重置时间）" if exc.retry_after_s else "（默认值）")
            return True
        if exc.kind == "credit":
            cred.exhausted = True
            log.warning("账号 %s 积分耗尽，移出池子", cred.uin)
            return True
        if exc.kind == "auth":
            return True
        if exc.kind in ("busy", "server", "policy"):
            cred.cooldown_until = time.monotonic() + 60
            return True
        return False  # client 错误：换谁都一样，直接报给调用方

    def report_success(self, cred: Credential) -> None:
        cred.last_error = ""
        cred.cooldown_until = 0.0

    # ---------------- 签到 ----------------

    def checkin_overview(self) -> List[dict]:
        rows = []
        for cred in self.list_accounts():
            if cred.region != "cn":
                rows.append({"account_id": cred.account_id, "uin": cred.uin,
                             "region": cred.region, "supported": False,
                             "note": "国际版没有签到活动"})
                continue
            if not self.ensure_fresh(cred):
                rows.append({"account_id": cred.account_id, "uin": cred.uin,
                             "region": cred.region, "supported": True,
                             "error": "凭据不可用，无法查询"})
                continue
            try:
                st = upstream.checkin_status(cred)
                rows.append({"account_id": cred.account_id, "uin": cred.uin,
                             "region": cred.region, "supported": True, **st})
            except UpstreamError as exc:
                rows.append({"account_id": cred.account_id, "uin": cred.uin,
                             "region": cred.region, "supported": True,
                             "error": exc.message})
        return rows

    def claim_checkin(self, account_ids: List[str]) -> List[dict]:
        """真实领取。**只在调用方显式指定账号时执行。**"""
        out = []
        for cred in self.list_accounts():
            if account_ids and cred.account_id not in account_ids:
                continue
            if cred.region != "cn":
                out.append({"account_id": cred.account_id, "ok": False,
                            "reason": "国际版没有签到活动"})
                continue
            if not self.ensure_fresh(cred):
                out.append({"account_id": cred.account_id, "ok": False,
                            "reason": "凭据不可用"})
                continue
            try:
                res = upstream.claim_checkin(cred)
                out.append({"account_id": cred.account_id, "ok": True, **res})
                log.info("账号 %s 签到成功：%s", cred.uin, res.get("credited"))
            except UpstreamError as exc:
                out.append({"account_id": cred.account_id, "ok": False, "reason": exc.message})
        return out

    # ---------------- 展示 ----------------

    def snapshot(self) -> dict:
        with self._lock:
            accounts = [c.public() for c in self._creds.values()]
            for row in accounts:
                aid = row["account_id"]
                row["credits"] = self._credits.get(aid)
                row["requests_session"] = self._requests.get(aid, 0)
                row["credits_spent_session"] = round(self._spent.get(aid, 0.0), 4)
            spent_total = round(sum(self._spent.values()), 4)
            requests_total = sum(self._requests.values())
        return {
            "accounts": accounts,
            "catalogs": {k: len(v) for k, v in self._catalogs.items()},
            "last_reload": self.last_reload,
            "last_credit_refresh": self.last_credit_refresh,
            "session": {"requests": requests_total, "credits_spent": spent_total},
        }

    def all_model_ids(self) -> List[dict]:
        out, seen = [], set()
        for region in ("cn", "global"):
            for mid in self._catalogs.get(region, []):
                if mid in seen:
                    continue
                seen.add(mid)
                out.append({"id": mid, "object": "model", "created": 0,
                            "owned_by": f"workbuddy-{region}"})
        return out

    def models_by_region(self) -> List[dict]:
        """面板用：带区域、上下文、倍率等元数据的完整清单。"""
        with self._lock:
            cn = list(self._catalog_models.get("cn") or [])
            gl = list(self._catalog_models.get("global") or [])
        out: List[dict] = []
        for region, items in (("cn", cn), ("global", gl)):
            for m in items:
                out.append({
                    "id": m.get("id"),
                    "name": m.get("name") or m.get("id"),
                    "region": region,
                    "context_window": m.get("context_window"),
                    "max_tokens": m.get("max_tokens"),
                    "credits": m.get("credits"),
                    "cost": m.get("cost"),
                    "owned_by": f"workbuddy-{region}",
                })
        return out

    def account_count(self) -> Dict[str, int]:
        with self._lock:
            items = list(self._creds.values())
        return {
            "total": len(items),
            "available": sum(1 for c in items if c.available()),
            "exhausted": sum(1 for c in items if c.exhausted),
            "cooling": sum(1 for c in items if c.cooldown_until > time.monotonic()),
        }


__all__ = ["Pool"]
