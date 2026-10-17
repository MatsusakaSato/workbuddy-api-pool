"""账号池：选择、失败分类、轮换、续期、签到。

只服务**一个上游**（WorkBuddy 国内版）。国际版与 Gemini 通道已移除，
所以这里不再有区域路由——所有账号都用同一套 base 和同一份模型目录。

选择策略：
  1. 排除已耗尽 / 冷却中 / 凭据过期的账号
  2. 交给 `rotation.py` 的三层调度器（见 docs/账号轮换策略设计.md）
  3. 失败时依次换下一个账号，只有「请求本身不合法」不换号

积分口径（2026-10-17 修正，见 upstream.fetch_credits 的长注释）：
  余额分成「基础套餐积分」与「平台奖励积分」两个桶，基线分别喂给调度器。
  **基础套餐月底清零、奖励积分过期远得多**，所以轮换优先花基础积分。
"""
from __future__ import annotations

import logging
import random
import threading
import time
from typing import Dict, List, Optional, Tuple

from . import config, creds, rotation, upstream
from .creds import Credential
from .upstream import UpstreamError

log = logging.getLogger("gateway.pool")

#: `Pool._credits[name]` 只允许缺省的键：基础/奖励两个桶，其余键视为"本次没取到"。
_CREDIT_KEYS = ("total_credits", "base_credits", "reward_credits")


def _rate_per_hour(spent_session: float) -> Optional[float]:
    """本次进程的消耗速率（积分/小时）。**没花过就返回 None**，不返回 0。

    用途只有一个：面板上给「还能撑多久」一个量级参考。样本太少时（< 0.1 积分）
    速率没有意义，同样返回 None —— 用 0.001 分推出来的「还能撑 300 天」比不给更误导。
    """
    if spent_session < 0.1:
        return None
    try:
        from .activity import ACTIVITY
        uptime = float(ACTIVITY.stats().get("uptime_s") or 0)
    except Exception:  # noqa: BLE001
        return None
    if uptime < 60:
        return None
    return round(spent_session / (uptime / 3600.0), 3)


class Pool:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._creds: Dict[str, Credential] = {}
        # 每个账号的余额桶：{"total_credits","base_credits","reward_credits", ...}。
        # 没取到时**整个键都不存在** —— 与「余额 0」必须能区分开（0 是耗尽，缺失是未知）。
        self._credits: Dict[str, dict] = {}
        self._spent: Dict[str, float] = {}       # 本次进程内按账号累计的积分消耗
        self._requests: Dict[str, int] = {}      # 本次进程内按账号的请求数
        # 持久化的跨重启累计计数（由 creds 落盘，reload 时按 account_id 背回来）。
        # 与 _spent/_requests 的区别：上面两个是「本次进程」，这三个是「历史累计」。
        self._persisted: Dict[str, dict] = {}
        # 在飞请求数：调度器第 1 层硬过滤用（并发上限）。
        # 这里**不预置**任何账号的键——账号是 reload 出来的，预置会留下永不释放的幽灵条目。
        self._in_flight: Dict[str, int] = {}
        self._catalogs: List[str] = []
        self._catalog_models: List[dict] = []
        self.last_reload = 0.0
        self.last_credit_refresh = 0.0
        # reload 会按 account_id 重建 Credential 对象，累计计数会随之归零。
        # 这里包一层：reload 前后把「历史累计」背回来（详见 _carry_persisted）。
        self._install_reload_carryover()

    def _install_reload_carryover(self) -> None:
        """给 reload 包一层累计计数搬运。

        `reload` 由 scheduler 负责/共用，这里不改它的函数体，只在实例上包一层，
        保证「账号被重建后累计计数不丢」（设计文档 §5、G5 重启不失忆）。
        用实例属性覆盖类方法，因此只影响当前 Pool 实例。
        """
        inner = self.reload

        def reload_with_carryover(deep: bool = True) -> dict:
            with self._lock:
                self._capture_persisted()
            out = inner(deep=deep)
            with self._lock:
                self._reattach_persisted()
            # 账号对象被重建后，调度器里可能残留「幽灵认领」（reload 前占坑、
            # reload 后那个请求早已不在）。不清理会把槽位永久占住，最终
            # pick() 永远返回 None——比薅羊毛更糟（设计文档 §2.1）。
            try:
                self.reset_rotation_state()
            except Exception:  # noqa: BLE001 — 清理失败不该让 reload 挂掉
                log.warning("重置轮换状态失败（已忽略）", exc_info=True)
            return out

        reload_with_carryover.__wrapped__ = inner  # type: ignore[attr-defined]
        self.reload = reload_with_carryover  # type: ignore[method-assign]

    def _capture_persisted(self) -> None:
        """把当前账号对象上的累计计数存进内存快照（reload 前调用）。

        **只搬 persistable=True 的账号**：persistable=False 是测试注入的假号，
        它们从不落盘，其计数也不该被当成历史（设计文档 §5 的安全约束）。
        """
        for c in self._creds.values():
            if not c.persistable:
                continue
            current = self._persisted.get(c.account_id)
            fresh = {
                "credits_spent_total": max(
                    float(getattr(c, "credits_spent_total", 0.0)),
                    float((current or {}).get("credits_spent_total", 0.0))),
                "requests_total": max(
                    int(getattr(c, "requests_total", 0)),
                    int((current or {}).get("requests_total", 0))),
                "last_used_wall": max(
                    float(getattr(c, "last_used_wall", 0.0)),
                    float((current or {}).get("last_used_wall", 0.0))),
            }
            self._persisted[c.account_id] = fresh

    def _reattach_persisted(self) -> None:
        """reload 后把累计计数贴回新建的账号对象（reload 后调用）。

        账号是 reload 从桌面 auth 目录重建的，身份靠 account_id（uin 优先）匹配。
        """
        for c in self._creds.values():
            if not c.persistable:
                continue
            snap = self._persisted.get(c.account_id)
            if not snap:
                continue
            # 取 max：磁盘/内存快照与对象自身的值取大，避免搬运把计数改小
            c.credits_spent_total = max(
                float(getattr(c, "credits_spent_total", 0.0)),
                float(snap["credits_spent_total"]))
            c.requests_total = max(
                int(getattr(c, "requests_total", 0)), int(snap["requests_total"]))
            c.last_used_wall = max(
                float(getattr(c, "last_used_wall", 0.0)), float(snap["last_used_wall"]))

    # ---------------- 生命周期 ----------------

    def reload(self, deep: bool = True) -> dict:
        """重新扫描凭据并合并进自有库。deep=True 时刷新积分与模型目录。"""
        with self._lock:
            store = creds.load_store()
            found = creds.discover_from_desktop()
            self._creds = creds.merge(store, found)
            # reload 是冷路径，且刚发现了新凭据：必须真的落盘，不能被子系统的节流吃掉
            creds.save_store(self._creds, force=True)
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
            # 只有续期成功才写盘：否则临时/注入的凭据会被顺手持久化。
            # 续期结果是「新 token」，丢了要重刷一次，所以这里必须 force。
            if changed:
                creds.save_store(self._creds, force=True)
            return ok

    def refresh_all_tokens(self) -> None:
        """逐个续期。ensure_fresh 内部只在真的续期成功时落盘。"""
        for c in self.list_accounts():
            self.ensure_fresh(c)

    def _store_credits(self, aid: str, info: dict) -> bool:
        """把一次 `fetch_credits` 的结果记进余额桶。返回是否记下了。

        **只认新解析器给出的三个桶**：`fetch_credits` 在没有逐包明细时只返回
        `raw`/`total_count`（见 upstream.py 的说明），此时不写 —— 原来的
        `isinstance(info["total_credits"], int)` 判断会让这种响应静默漏记，
        而更早的版本还会把 `TotalDosage`（含月度池值）当余额写进去。
        """
        if not isinstance(info, dict) or "total_credits" not in info:
            return False
        snapshot = {k: info.get(k) for k in _CREDIT_KEYS}
        if not isinstance(snapshot["total_credits"], (int, float)):
            return False
        for extra in ("base_credits", "reward_credits"):
            if not isinstance(snapshot[extra], (int, float)):
                snapshot[extra] = 0.0
        for extra in ("base_refresh_at", "reward_expiring_soon", "reward_expiring_soon_at",
                      "packages", "total_dosage"):
            snapshot[extra] = info.get(extra)
        with self._lock:
            self._credits[aid] = snapshot
        return True

    def refresh_credits(self) -> None:
        for c in self.list_accounts():
            if not c.token_valid:
                continue
            try:
                info = upstream.fetch_credits(c)
                self._store_credits(c.account_id, info)
            except UpstreamError as exc:
                log.debug("账号 %s 积分查询失败：%s", c.uin, exc.message)
            except Exception:  # noqa: BLE001 — 刷新是后台动作，绝不能把 keeper 打挂
                log.debug("账号 %s 积分解析异常", c.uin, exc_info=True)
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
        """刷新唯一的模型目录。随便挑一个 token 有效的账号去拉即可。"""
        cand = [c for c in self.list_accounts() if c.token_valid]
        if not cand:
            log.debug("没有 token 有效的账号，跳过目录刷新")
            return
        try:
            models = upstream.fetch_models(cand[0])
            with self._lock:
                self._catalog_models = models
                self._catalogs = [m["id"] for m in models]
            log.info("模型目录：%d 个", len(models))
        except Exception as exc:  # noqa: BLE001
            log.debug("目录拉取失败：%s", exc)

    # ---------------- 选择 ----------------

    def list_accounts(self) -> List[Credential]:
        with self._lock:
            return list(self._creds.values())

    @staticmethod
    def _credit_bucket(entry) -> dict:
        """把 `_credits[aid]` 的取值规整成 dict。

        **必须容忍裸数字**：2026-10-17 之前 `_credits[aid]` 就是一个 int，
        测试与外部脚本仍会直接赋值。把读取改成「只认 dict」会在这里抛
        AttributeError，而它在 `Pool.candidates()` 里 —— 即每个请求的选号路径
        （实测：`tests/test_rotation.py` 立刻红）。所以旧形状在这里被接受，
        只是没有分桶信息（基础/奖励为 None），轮换的那一项自然退化为 0。
        """
        if isinstance(entry, dict):
            return entry
        if isinstance(entry, (int, float)) and not isinstance(entry, bool):
            return {"total_credits": float(entry)}
        return {}

    def credits_total(self, account_id: str) -> Optional[float]:
        """账号的可花总余额；**未知返回 None，不返回 0**（0 是耗尽，两者含义相反）。"""
        with self._lock:
            snap = self._credit_bucket(self._credits.get(account_id))
        val = snap.get("total_credits")
        return float(val) if isinstance(val, (int, float)) else None

    def credits_view(self, account_id: str) -> Tuple[Dict[str, float], Dict[str, float]]:
        """调度器要的两个视图：**总余额**与**基础套餐余额**。

        分开传而不是传一个 dict，是因为第 3 层的 credit_share / runway 用的是「能花多少」，
        而「先花基础积分」的偏向只看基础桶。把两者混在一个映射里，任何一处改动都可能
        让它们悄悄互换 —— 那正是余额显示不准这类 bug 的形状。
        """
        with self._lock:
            snap = self._credit_bucket(self._credits.get(account_id))
        totals: Dict[str, float] = {}
        bases: Dict[str, float] = {}
        if isinstance(snap.get("total_credits"), (int, float)):
            totals[account_id] = float(snap["total_credits"])
        if isinstance(snap.get("base_credits"), (int, float)):
            bases[account_id] = float(snap["base_credits"])
        return totals, bases

    def candidates(self) -> List[Credential]:
        now = time.monotonic()
        with self._lock:
            pool = list(self._creds.values())
        usable = [c for c in pool if c.available()]
        if not usable:
            usable = [c for c in pool if not c.exhausted]  # 冷却中的也允许最后一搏
        # 可用优先，其次积分高，其次凭据新鲜
        def key(c: Credential):
            return (
                0 if c.cooldown_until <= now else 1,
                -(self.credits_total(c.account_id) or 0.0),
                -c.expires_at_ms,
            )
        return sorted(usable, key=key)

    def pick(self, model: str, exclude: Optional[set] = None) -> Credential:
        exclude = set(exclude or ())
        cands = self.candidates()
        if cands:
            if not self.rotation_enabled():
                # 回滚路径：旧行为（积分高优先，取首个能续期的）。
                # **仍然要占坑**：lead 选定「release 无条件调用」的契约后，
                # server.py 对每次成功的 pick 都会 release 一次；若这里不占坑，
                # 那次 release 会把别的在飞请求的槽位偷走（计数被错误地减 1）。
                for cred in cands:
                    if cred.account_id in exclude:
                        continue
                    if self.ensure_fresh(cred):
                        try:
                            self._sched().claim(cred.account_id)
                        except Exception:  # noqa: BLE001
                            log.debug("rollback 占坑失败 %s", cred.account_id, exc_info=True)
                        self._sync_inflight(cred.account_id)
                        return cred
            else:
                # 调度器给出「本轮该用谁」，但可能选到需要续期且续期失败的号：
                # 把它加进本地 exclude 再选一次，直到选不动为止。
                # 硬上界 = 候选数 + 1，保证绝不空转。
                tried = set(exclude)
                for _ in range(len(cands) + 1):
                    cred = self._pick_rotation(cands, tried)
                    if cred is None:
                        break
                    if cred.account_id in tried:
                        break  # 调度器兜底返回了已试过的号，避免死循环
                    if self.ensure_fresh(cred):
                        # `_pick_rotation` → `RotationScheduler.pick` 已在**同一把锁内原子占坑**
                        # （检查 in_flight 与 +1 是同一个临界区），所以这里只需要：
                        #   1. 记下「选中了谁」（mark_used）；
                        #   2. 把只读镜像同步过去，便于 snapshot 观测。
                        # 释放由 `note_request_finished` / `release` 负责，且必须与本次占坑
                        # 一一对应（不变量：每次成功的 pick 恰好释放一次）。
                        try:
                            self._sched().mark_used(cred.account_id, time.time())
                        except Exception:  # noqa: BLE001
                            log.debug("mark_used 失败 %s", cred.account_id, exc_info=True)
                        self._sync_inflight(cred.account_id)
                        return cred
                    tried.add(cred.account_id)
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
        # 追加：刷新「上次使用」。**不**在这里释放在飞名额——
        # 释放由 server.py 的 _note_finished（finally）统一负责，避免重复扣减。
        try:
            self._sched().mark_used(cred.account_id, time.time())
        except Exception:  # noqa: BLE001
            log.debug("轮换状态更新失败(success) %s", cred.account_id, exc_info=True)

    # ---------------- 轮换调度 ----------------
    #
    # ## 坑位生命周期契约（不变量，改动前务必读完）
    #
    #   **每一次返回非 None 的 `pick()`，都必须恰好配一次 `release()`
    #     （= `note_request_finished`），所有路径包括异常与提前 return。**
    #
    # - **占坑（claim）** 由 `RotationScheduler.pick` 在**同一把锁内原子完成**
    #   （Layer 1 的并发上限就靠它，check 与 +1 不可分割）。
    #   回滚路径（ROTATION_ENABLED=0）由 `Pool.pick` 显式 `claim()`。
    # - **释放（release）** 由 `note_request_finished` / `release` 负责，
    #   内部走 `RotationScheduler.release_claim`：**只在确实持有占用时才减 1**，
    #   因此幂等、可无条件调用，不会把别的在飞请求的名额偷走。
    # - **权威计数只有一个**：`RotationScheduler.state(aid).in_flight`。
    #   `Pool._in_flight` 是只读镜像（`_sync_inflight`），**不参与任何判定**。
    #   两个计数器两把锁无法共同约束一个上限。
    # - 漏释放的后果：槽位被永久吃掉 → 池子越用越小 → 最终永远选不出号
    #   （比「薅羊毛」更严重：一个是集中，一个是直接死掉）。见设计文档 §2 I4。
    # --------------------------------------------------------------------------

    def _sched(self) -> rotation.RotationScheduler:
        """惰性建调度器（persistence 负责 policy 落盘，这里只消费）。

        用 getattr 探测 `config.rotation_policy`，因此不硬依赖 persistence 先落地：
        没有该函数就退回 `RotationPolicy()` 的安全默认值。
        ROTATION_ENABLED=0 时同样返回调度器，但 `_pick_rotation` 会绕开它，
        退回旧的「积分高优先」语义（§6 回滚开关）。
        """
        sched = getattr(self, "_scheduler", None)
        if sched is not None:
            return sched
        with self._lock:
            sched = getattr(self, "_scheduler", None)
            if sched is None:
                try:
                    rng = random.Random()
                    sched = rotation.RotationScheduler(rotation.RotationPolicy.from_config(), rng)
                except Exception:  # noqa: BLE001 — 调度器建不起来也不能拦住服务
                    log.exception("轮换调度器构造失败，回退默认策略")
                    sched = rotation.RotationScheduler(rotation.RotationPolicy())
                self._scheduler = sched
            return sched

    def rotation_enabled(self) -> bool:
        """§6 回滚开关：GATEWAY_ROTATION_ENABLED=0 退回旧行为。"""
        return bool(getattr(config, "ROTATION_ENABLED", True))

    def reset_rotation_state(self) -> None:
        """清空调度器的**在飞计数**，保留使用历史（`last_used_at` / EMA）。

        重载凭据后调用：账号对象被重建，reload 之前占坑、reload 之后那个请求早已
        不在的「幽灵认领」必须清掉，否则槽位被永久吃掉，`pick` 最终永远返回 None。

        ## 为什么**不是** `rotate_state()`

        2026-10-17 修正：这里原先调 `sched.rotate_state()`，它把每个号的
        `last_used_at` 一起归零——于是重载之后所有号同时变成「从未用过」，
        Layer 2 的最旧者失去参照、Layer 3 的 `coolness` 全员变 1.0。
        实测：`/admin/reload` 之后轮换退化成随机，而这恰恰是最常被手动点的按钮。

        幽灵计数要清的是 `in_flight`，使用历史必须留下——后者正是轮换回答
        「该轮到谁」的唯一依据。所以改调 `reset_inflight()`。

        `Pool.reload` 已经通过 `_install_reload_carryover` 自动调用本方法，
        调用方通常不需要再手动调一次。
        """
        try:
            self._sched().reset_inflight()
        except Exception:  # noqa: BLE001
            log.debug("重置轮换状态失败", exc_info=True)
        with self._lock:
            self._in_flight.clear()

    def reset_rotation_state_hard(self) -> None:
        """**全量**清空调度器状态（连使用历史、EMA 一起）。

        只在「全池画像彻底不再可信」时用（例如凭据被整体替换、时基跳变）。
        常规 reload 用 `reset_rotation_state()`。
        """
        try:
            self._sched().rotate_state()
        except Exception:  # noqa: BLE001
            log.debug("硬重置轮换状态失败", exc_info=True)
        with self._lock:
            self._in_flight.clear()

    def _pick_rotation(self, candidates: List[Credential],
                       exclude: Optional[set] = None) -> Optional[Credential]:
        """用三层调度器从候选集里选一个。返回 None 表示这轮选不出（含调度被禁用）。"""
        if not self.rotation_enabled():
            return None
        creds = list(candidates or [])
        if not creds:
            return None
        totals: Dict[str, float] = {}
        bases: Dict[str, float] = {}
        for c in creds:
            t, b = self.credits_view(c.account_id)
            totals.update(t)
            bases.update(b)
        try:
            return self._sched().pick(
                creds,
                totals,
                time.monotonic(),
                exclude=set(exclude or ()),
                base_credits=bases,
            )
        except Exception:  # noqa: BLE001 — 选号异常绝不能变成 500（不变量 I3）
            log.exception("轮换选号失败，退回候选集首位")
            return creds[0]

    def note_request_started(self, cred: Credential) -> None:
        """请求开始钩子。**不占坑**——坑位已由 `pick()` 原子占好。

        计数权威来源是 `RotationScheduler`（Layer 1 读的就是它），`Pool._in_flight`
        只是它的镜像。这里把镜像对齐（而不是 +1），因为：
          - 两个计数器两把锁无法共同约束一个上限；
          - `pick()` 已经 +1，这里再 +1 就是双计，而释放只走一次
            → 每个请求净泄漏 1 个槽位，池子最终缩到 0。
        """
        if cred is None:
            return
        self._sync_inflight(cred.account_id)

    def note_request_finished(self, cred: Credential) -> None:
        """请求结束：释放 `pick()` 占的槽位。**必须在 finally 里调用**（不变量 I4），
        否则客户端提前断开 / 流式中断 / 异常路径都会永久缩小池子。

        释放**幂等且安全**：`RotationScheduler.release_claim` 只在确实持有占用时才减 1，
        因此重复调用、或对从未占用的账号调用，都不会把别的在飞请求的名额偷走。
        调用方因此**不必**关心 `started` 标志位，可以无条件调用。
        """
        if cred is None:
            return
        aid = cred.account_id
        try:
            self._sched().release_claim(aid)
        except Exception:  # noqa: BLE001
            log.debug("轮换状态更新失败(finished) %s", aid, exc_info=True)
        self._sync_inflight(aid)

    def release(self, cred: Credential) -> None:
        """`note_request_finished` 的别名。幂等：重复调用不会让计数变负。"""
        self.note_request_finished(cred)

    def _sync_inflight(self, aid: str) -> None:
        """把 `Pool._in_flight[aid]` 对齐到调度器的权威计数（纯镜像，不独立累加）。"""
        try:
            authoritative = self._sched().state(aid).in_flight
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            if authoritative:
                self._in_flight[aid] = authoritative
            else:
                self._in_flight.pop(aid, None)

    def note_usage(self, cred: Credential, credit: float, elapsed_s: float) -> None:
        """把一次真实消耗喂给调度器的 EMA（供 runway 计算）。"""
        if cred is None:
            return
        try:
            self._sched().note_credit(cred.account_id, time.time(),
                                      float(credit or 0.0), float(elapsed_s or 0.0))
        except Exception:  # noqa: BLE001
            log.debug("轮换 EMA 更新失败 %s", cred.account_id, exc_info=True)

    def note_latency(self, cred: Credential, seconds: float) -> None:
        """把一次真实请求延迟喂给调度器，Layer 2 的 `2 × p95` 才真正生效。

        不喂的话第 2 层宽度恒为 `SLICE_S`（设计文档 §3），时间片轮转退化成一个
        固定的 30s 窗口——功能上仍然正确，但慢请求下的公平性会变差。
        """
        if cred is None:
            return
        try:
            self._sched().note_latency(cred.account_id, float(seconds or 0.0))
        except Exception:  # noqa: BLE001
            log.debug("轮换延迟更新失败 %s", cred.account_id, exc_info=True)

    # ---------------- 签到 ----------------

    def checkin_overview(self) -> List[dict]:
        """所有账号都是国内版，都支持签到——不再有「该区域没有活动」这种分支。"""
        rows = []
        for cred in self.list_accounts():
            if not self.ensure_fresh(cred):
                rows.append({"account_id": cred.account_id, "uin": cred.uin,
                             "error": "凭据不可用，无法查询"})
                continue
            try:
                st = upstream.checkin_status(cred)
                rows.append({"account_id": cred.account_id, "uin": cred.uin, **st})
            except UpstreamError as exc:
                rows.append({"account_id": cred.account_id, "uin": cred.uin,
                             "error": exc.message})
        return rows

    def claim_checkin(self, account_ids: List[str]) -> List[dict]:
        """真实领取。**只在调用方显式指定账号时执行。**"""
        out = []
        for cred in self.list_accounts():
            if account_ids and cred.account_id not in account_ids:
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

    def auto_checkin_sweep(self, skip_ids: Optional[set] = None,
                           gap_s: float = 0.0,
                           sleep=time.sleep) -> List[dict]:
        """全自动签到一轮：逐个账号查签到状态，今天没领的**真的领**。

        与另外两个签到入口的分工：
          · `checkin_overview()` 只查不领（面板轮询用）
          · `claim_checkin()` 按调用方给的账号领（「立即签到」按钮用）
          · 本方法 = 查 + 判断 + 领，是全自动调度器（gateway/checkin.py）的落地动作

        四个刻意的约束：
        1. **`skip_ids` 是第二道闸门**。上游的 `today_checked_in` 在刚领完的瞬间可能
           还是旧值（写入有延迟），只信上游会重复领取。所以调度器把"今天已经领过"的
           账号传进来，本地记录优先。
        2. **`today_checked_in is None` 时不盲领**。字段缺失说明上游改了协议或返回异常，
           此时领了也无从判断结果，宁可留给下一轮。
        3. **`gap_s`：账号之间留间隔**。签到是真实领取动作，一次性连打会在上游留下
           机械指纹，见 config.AUTO_CHECKIN_ACCOUNT_GAP_S 的说明。
        4. **一个账号失败不影响其他账号**：所有异常都在循环内收成 action=error。
        """
        results: List[dict] = []
        for index, cred in enumerate(self.list_accounts()):
            if index and gap_s > 0:
                try:
                    sleep(gap_s)
                except Exception:  # noqa: BLE001 被中断也不该让整轮垮掉
                    pass

            row: dict = {"account_id": cred.account_id, "uin": cred.uin,
                         "nickname": getattr(cred, "nickname", "") or ""}

            if skip_ids and cred.account_id in skip_ids:
                results.append({**row, "action": "already_local"})
                continue

            if not self.ensure_fresh(cred):
                results.append({**row, "action": "unavailable", "error": "凭据不可用"})
                continue

            try:
                st = upstream.checkin_status(cred)
            except UpstreamError as exc:
                results.append({**row, "action": "error", "error": exc.message})
                continue

            if st.get("today_checked_in") is True:
                results.append({**row, "action": "already",
                                "daily_credit": st.get("daily_credit")})
                continue
            if st.get("active") is False:
                results.append({**row, "action": "inactive",
                                "error": "签到活动未开始或已结束"})
                continue
            if st.get("today_checked_in") is None:
                results.append({**row, "action": "unknown",
                                "error": "上游未返回今日签到状态"})
                continue

            try:
                res = upstream.claim_checkin(cred)
            except UpstreamError as exc:
                results.append({**row, "action": "error", "error": exc.message})
                continue

            results.append({**row, "action": "claimed", "credited": res.get("credited"),
                            "streak_days": res.get("streak_days")})
            log.info("自动签到：%s 领到 %s", cred.uin, res.get("credited"))

            # 领完立刻刷新这个账号的余额 —— 否则面板上的「余额」要等下一轮
            # keeper（30 分钟）才更新，刚领到的积分看不见。
            # 签到奖励进的是**奖励包**（CapacityType 1），所以刷新后基础/奖励要
            # 分别重算 —— 不能只更新总数，否则两张卡会自相矛盾。
            try:
                info = upstream.fetch_credits(cred)
                self._store_credits(cred.account_id, info)
            except UpstreamError as exc:
                log.debug("签到后刷新余额失败（忽略）：%s", exc.message)
            except Exception:  # noqa: BLE001
                log.debug("签到后余额解析失败（忽略）", exc_info=True)
        return results

    # ---------------- 展示 ----------------

    def snapshot(self) -> dict:
        with self._lock:
            accounts = [c.public() for c in self._creds.values()]
            cred_by_id = {c.account_id: c for c in self._creds.values()}
            for row in accounts:
                aid = row["account_id"]
                # 余额三件套：总数 + 基础 + 奖励。**全部可能为 None** ——
                # None = 还没拉到（面板显示 "—"），0 = 真的耗尽。两者绝不可互换。
                snap = self._credit_bucket(self._credits.get(aid))
                rate = _rate_per_hour(float(self._spent.get(aid, 0.0)))
                total = snap.get("total_credits")
                total = float(total) if isinstance(total, (int, float)) else None
                # `credits` 是**总余额**的旧字段名，保持兼容：
                # 老前端、桌面面板、用户自己的脚本都读它，删掉会静默变成 undefined。
                # 它的口径已经修正（不再含月度池值），但字段名与含义（"能花多少"）不变。
                row["credits"] = total
                row["total_credits"] = total
                for key in ("base_credits", "reward_credits"):
                    val = snap.get(key)
                    row[key] = float(val) if isinstance(val, (int, float)) else None
                row["base_refresh_at"] = snap.get("base_refresh_at")
                row["reward_expiring_soon"] = snap.get("reward_expiring_soon")
                row["reward_expiring_soon_at"] = snap.get("reward_expiring_soon_at")
                row["total_dosage"] = snap.get("total_dosage")
                # 悬停要看得见的明细：每个包一行（基础在前、先到期的在前）
                row["packages"] = snap.get("packages") or []
                # 面板「还剩多少分钟」用；本次进程没花过积分时是 None，不是 0
                row["credits_per_hour"] = rate
                row["requests_session"] = self._requests.get(aid, 0)
                row["credits_spent_session"] = round(self._spent.get(aid, 0.0), 4)
                # 跨重启累计计数：优先用账号对象上的权威值（load_store/热路径都在写它），
                # 没有账号对象时（例如 reload 刚重建）回落到本进程背下来的持久化快照。
                c = cred_by_id.get(aid)
                snap_p = self._persisted.get(aid) or {}
                row["credits_spent_total"] = round(
                    float(getattr(c, "credits_spent_total", 0.0))
                    if c is not None else float(snap_p.get("credits_spent_total", 0.0)), 4)
                row["requests_total"] = int(
                    getattr(c, "requests_total", 0)
                    if c is not None else int(snap_p.get("requests_total", 0)))
                row["last_used_wall"] = float(
                    getattr(c, "last_used_wall", 0.0)
                    if c is not None else float(snap_p.get("last_used_wall", 0.0)))
                # 轮换可观测性：在飞数是「池子有没有被泄漏缩小」的唯一窗口
                row["in_flight"] = self._in_flight.get(aid, 0)
            spent_total = round(sum(self._spent.values()), 4)
            requests_total = sum(self._requests.values())
            in_flight_total = sum(self._in_flight.values())

            # 池级合计。只累加**已知**的桶：一个还没拉到余额的号不该被当成 0 拉低合计。
            base_total = sum(r["base_credits"] for r in accounts
                             if isinstance(r["base_credits"], (int, float)))
            reward_total = sum(r["reward_credits"] for r in accounts
                               if isinstance(r["reward_credits"], (int, float)))
            known = [r["total_credits"] for r in accounts
                     if isinstance(r["total_credits"], (int, float))]
            credits = {
                "total": round(sum(known), 4),
                "base": round(base_total, 4),
                "reward": round(reward_total, 4),
                "known_accounts": len(known),
                "unknown_accounts": len(accounts) - len(known),
            }
        return {
            "accounts": accounts,
            "catalog": len(self._catalogs),
            "last_reload": self.last_reload,
            "last_credit_refresh": self.last_credit_refresh,
            "credits": credits,
            "session": {"requests": requests_total, "credits_spent": spent_total},
            "rotation": {
                "enabled": config.ROTATION_ENABLED,
                "in_flight": in_flight_total,
                "max_concurrency": config.ROTATION_MAX_CONCURRENCY,
                "min_interval_s": config.ROTATION_MIN_INTERVAL_S,
                "slice_s": config.ROTATION_SLICE_S,
                # 基础套餐优先的分级状态：面板据此解释「为什么这个号现在分不到请求」。
                # tier_active=True 表示池子里还有基础积分，基础已耗尽的号被 Layer 1 挡在门外；
                # False 表示全员耗尽，分级已自动失效（正是「优先不变成只准」的判据）。
                "base_priority": bool(getattr(config, "ROTATION_BASE_PRIORITY", True)),
                "base_tier_active": base_total > 0,
                "w_base": getattr(config, "ROTATION_W_BASE", None),
            },
        }

    def all_model_ids(self) -> List[dict]:
        with self._lock:
            ids = list(self._catalogs)
        return [{"id": mid, "object": "model", "created": 0, "owned_by": "workbuddy"}
                for mid in ids]

    def models_overview(self) -> List[dict]:
        """面板用：带上下文、倍率等元数据的完整清单。"""
        with self._lock:
            items = list(self._catalog_models)
        return [
            {
                "id": m.get("id"),
                "name": m.get("name") or m.get("id"),
                "context_window": m.get("context_window"),
                "max_tokens": m.get("max_tokens"),
                "credits": m.get("credits"),
                "cost": m.get("cost"),
                "owned_by": "workbuddy",
            }
            for m in items
        ]

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
