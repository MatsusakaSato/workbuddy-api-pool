"""全自动签到调度。

★ 2026-10-16 变更：签到从「半自动」改为「全自动」。

原来的设计（README 里的「半自动签到的保护」）要求前端勾选账号 + 二次确认，
网关自己绝不定时领取。改成全自动的原因很直接：**每日签到是保号的核心动作**，
漏一天就少一天额度，而"记得手动点"本来就该由程序代劳。

自动化带来的唯一真实代价是**上游风控**：签到是真实领取动作，定时执行会留下
机械指纹。所以这里做了三件事，而不是简单地起个循环：
  1. **不固定整点**：每轮结束后按 `interval + random(0, jitter)` 排下一次，
     30 分钟一轮 + 最多 10 分钟抖动；
  2. **账号之间留间隔**：账号不是一口气连打，默认间隔 2.5 秒；
  3. **本地当日记录是第二道闸门**（`ok=True` 且日期是今天就不再领）——
     上游的 `today_checked_in` 在刚领完后可能还是旧值，只信上游会重复领取。

一键回滚：`GATEWAY_AUTO_CHECKIN=0`，退回"只查询、不自动领"。
"""

from __future__ import annotations

import json
import logging
import os
import random
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from . import config

log = logging.getLogger("gateway.checkin")

# 调度线程的轮询粒度。比 interval 小两个数量级即可：
# 它只负责"到点没到点"，真正的上游调用在 auto_checkin_sweep 里。
_POLL_S = 30.0

# 启动后第一轮的延迟区间（秒）。
# 不在进程启动的瞬间就签到：那是个非常显眼的机械特征，而且启动时
# token 续期、目录刷新都在打上游，错开一点也顺带避开这段拥挤。
_FIRST_DELAY_RANGE = (5.0, 45.0)

# 今天已经"不用再领"的三种结果：本轮真的领到了 / 上游说今天已领 / 本地记录说今天已领
_SETTLED = ("claimed", "already", "already_local")


def _today() -> str:
    """本地日期（签到是"自然日"概念，用本地时区而不是 UTC）。"""
    return time.strftime("%Y-%m-%d")


class AutoCheckin:
    """自动签到器：定时把"今天还没领"的账号领掉。

    依赖倒置：它只要求 `pool` 提供
      · `auto_checkin_sweep(skip_ids=..., gap_s=...) -> list[dict]`
    所以测试里可以用一个假 pool 完全离线地验证调度与去重逻辑
    （见 tests/test_autocheckin.py），不需要起网关、更不需要真上游。
    """

    def __init__(
        self,
        pool,
        path: Optional[Path] = None,
        *,
        enabled: Optional[bool] = None,
        interval_s: Optional[int] = None,
        jitter_s: Optional[int] = None,
        gap_s: Optional[float] = None,
        clock=time.time,
    ) -> None:
        self._pool = pool
        self._path = Path(path) if path else config.AUTO_CHECKIN_STATE_FILE
        self.enabled = config.AUTO_CHECKIN_ENABLED if enabled is None else bool(enabled)
        self.interval_s = int(config.AUTO_CHECKIN_INTERVAL_S if interval_s is None else interval_s)
        self.jitter_s = int(config.AUTO_CHECKIN_JITTER_S if jitter_s is None else jitter_s)
        self.gap_s = float(config.AUTO_CHECKIN_ACCOUNT_GAP_S if gap_s is None else gap_s)
        self._clock = clock

        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None

        # 落盘状态。结构刻意保持"人类可读 + 加字段不会让旧文件失效"：
        #   accounts[account_id] = {uin, date, action, ok, credited, error, at}
        self._state: Dict = {
            "version": 1,
            "last_run_at": 0.0,
            "last_run_date": "",
            "next_run_at": 0.0,
            "last_summary": {},
            "accounts": {},
        }
        self._load()

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _load(self) -> None:
        try:
            doc = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as exc:  # noqa: BLE001 坏文件不该阻止网关启动
            log.warning("签到状态文件不可读（忽略并使用空状态）：%s", exc)
            return
        if not isinstance(doc, dict):
            return
        with self._lock:
            for key in ("last_run_at", "last_run_date", "next_run_at"):
                if key in doc:
                    self._state[key] = doc[key]
            if isinstance(doc.get("last_summary"), dict):
                self._state["last_summary"] = doc["last_summary"]
            if isinstance(doc.get("accounts"), dict):
                self._state["accounts"] = doc["accounts"]

    def _save(self) -> None:
        """原子写：先写临时文件再 rename，避免进程在写一半时被杀留下半截 JSON。"""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._state, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self._path)
        except Exception as exc:  # noqa: BLE001 状态落盘失败不该把签到打断
            log.warning("签到状态落盘失败（忽略）：%s", exc)

    def _schedule_next(self, now: float) -> float:
        base = float(self.interval_s)
        jitter = random.uniform(0, float(self.jitter_s)) if self.jitter_s > 0 else 0.0
        return now + base + jitter

    def status(self) -> dict:
        """给面板看的快照。**不发任何上游请求。**"""
        now = self._clock()
        with self._lock:
            accounts = json.loads(json.dumps(self._state["accounts"]))
            summary = dict(self._state.get("last_summary") or {})
            last_run_at = float(self._state.get("last_run_at") or 0.0)
            next_run_at = float(self._state.get("next_run_at") or 0.0)
        today = _today()
        claimed_today = sum(
            1 for rec in accounts.values()
            if rec.get("date") == today and rec.get("ok")
        )
        return {
            "enabled": self.enabled,
            "interval_s": self.interval_s,
            "jitter_s": self.jitter_s,
            "gap_s": self.gap_s,
            "today": today,
            "last_run_at": last_run_at,
            "last_run_age_s": int(now - last_run_at) if last_run_at else None,
            "next_run_in_s": int(max(0.0, next_run_at - now)) if next_run_at else None,
            "claimed_today": claimed_today,
            "last_summary": summary,
            "accounts": accounts,
        }

    def view(self) -> dict:
        """`/admin/checkin` 的附加字段（前端按 account_id 关联到账号行）。"""
        return {"auto": self.status()}

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    def _skip_ids(self) -> set:
        """今天已经确认领过的账号（本地记录优先于上游状态）。"""
        today = _today()
        with self._lock:
            return {
                aid for aid, rec in self._state["accounts"].items()
                if rec.get("date") == today and rec.get("ok")
            }

    def run_once(self, reason: str = "scheduled",
                 with_rows: bool = False) -> Optional[dict]:
        """跑一轮：查状态 → 把今天没领的领掉。返回本轮汇总。

        `enabled=False` 时直接返回 None（**不做任何上游请求**），
        这样回滚开关是"真的关掉"，而不是"关掉了但还是会查"。

        `with_rows=True` 时在返回值里带上每个账号的明细（`rows`）。
        只给「立即签到」按钮用：它要把"哪些领到了 / 哪些本来就领过"回给用户。
        """
        if not self.enabled:
            return None

        started = self._clock()
        skip = self._skip_ids()
        rows: List[dict] = []
        fatal: Optional[str] = None
        try:
            rows = self._pool.auto_checkin_sweep(skip_ids=skip, gap_s=self.gap_s)
        except Exception as exc:  # noqa: BLE001 调度线程绝不能因为一轮失败而退出
            fatal = f"{type(exc).__name__}: {exc}"
            log.warning("自动签到一轮失败（%s）：%s", reason, fatal)

        finished = self._clock()
        today = _today()
        summary = {"total": len(rows), "claimed": 0, "settled": 0, "failed": 0}
        for row in rows:
            action = str(row.get("action") or "error")
            ok = action in _SETTLED
            if action == "claimed":
                summary["claimed"] += 1
            if ok:
                summary["settled"] += 1
            else:
                summary["failed"] += 1
            with self._lock:
                prev = self._state["accounts"].get(row["account_id"])
                # 不要降级今天的"结算"记录：本轮若只是"本来就领过"（already / already_local），
                # 而今天已经有一条 ok 的记录，就保留原来那条 —— 它带着真正领到的金额与时间。
                # 否则每跑一轮，面板上"今天领到多少"就会被 null 覆盖一次。
                if action != "claimed" and isinstance(prev, dict) \
                        and prev.get("date") == today and prev.get("ok"):
                    continue
                self._state["accounts"][row["account_id"]] = {
                    "uin": row.get("uin"),
                    "nickname": row.get("nickname") or "",
                    "date": today,
                    "action": action,
                    "ok": ok,
                    "credited": row.get("credited"),
                    "error": row.get("error") or "",
                    "at": finished,
                }

        # 账号被删掉后旧记录会一直留着，这里顺手清掉（只在真的取到了账号清单时清，
        # 否则一轮"0 个账号"的异常会把所有历史记录误删）。
        if rows:
            alive = {row["account_id"] for row in rows}
            with self._lock:
                for aid in list(self._state["accounts"]):
                    if aid not in alive:
                        del self._state["accounts"][aid]

        summary["reason"] = reason
        summary["fatal"] = fatal
        summary["duration_s"] = round(max(0.0, finished - started), 1)
        with self._lock:
            self._state["last_run_at"] = finished
            self._state["last_run_date"] = today
            # 存副本：下面 with_rows 会往返回值里塞 rows，共用同一个 dict 会把
            # 明细也写进内存状态（并让落盘文件下一轮变大）。
            self._state["last_summary"] = dict(summary)
            self._state["next_run_at"] = self._schedule_next(finished)
        self._save()
        if reason != "startup":
            log.info("自动签到一轮结束（%s）：领到 %d 个，已领 %d 个，失败 %d 个",
                     reason, summary["claimed"], summary["settled"], summary["failed"])
        if with_rows:
            summary["rows"] = rows
        return summary

    def note_manual(self, results: List[dict]) -> None:
        """记录一次「立即签到」按钮的结果，避免自动调度器紧接着重复领取。

        手动领取走的是 `Pool.claim_checkin()`（不先查状态），所以这里只需要
        把成功的那批标成"今天已领"。失败的不记 —— 下一轮自动签到会重试。
        """
        today = _today()
        now = self._clock()
        changed = False
        for row in results:
            if not row.get("ok"):
                continue
            aid = row.get("account_id")
            if not aid:
                continue
            with self._lock:
                prev = dict(self._state["accounts"].get(aid) or {})
                prev.update({
                    "date": today, "action": "claimed_manual", "ok": True,
                    "credited": row.get("credited"), "error": "", "at": now,
                })
                self._state["accounts"][aid] = prev
            changed = True
        if changed:
            self._save()

    def tick(self) -> Optional[dict]:
        """到点就跑一轮，否则什么都不做。调度线程每 `_POLL_S` 秒调一次。"""
        if not self.enabled:
            return None
        now = self._clock()
        with self._lock:
            next_run_at = float(self._state.get("next_run_at") or 0.0)
        if next_run_at and now < next_run_at:
            return None
        return self.run_once("scheduled")

    # ------------------------------------------------------------------
    # 后台线程
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """起调度线程（幂等）。返回是否由本次调用真的起了线程。"""
        if not self.enabled:
            log.info("自动签到已关闭（GATEWAY_AUTO_CHECKIN=0），只查询不领取")
            return False
        if self._thread is not None and self._thread.is_alive():
            return False

        # 首轮延迟；落盘状态里若 already 有下次时间且还在未来，就尊重它
        # （进程重启不该把当天已完成的节奏打乱）。
        now = self._clock()
        with self._lock:
            stored = float(self._state.get("next_run_at") or 0.0)
            if stored <= now:
                self._state["next_run_at"] = now + random.uniform(*_FIRST_DELAY_RANGE)
        self._thread = threading.Thread(target=self._loop, name="auto-checkin", daemon=True)
        self._thread.start()
        log.info("自动签到已开启：每 %d 秒一轮（+0~%d 秒抖动），账号之间间隔 %.1f 秒",
                 self.interval_s, self.jitter_s, self.gap_s)
        return True

    def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception as exc:  # noqa: BLE001 上一行的兜底，再兜一层保证线程不死
                log.warning("自动签到调度异常（忽略）：%s", exc)
            time.sleep(_POLL_S)


__all__ = ["AutoCheckin"]
