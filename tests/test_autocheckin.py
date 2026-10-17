"""全自动签到测试（离线，不碰网络、不需要网关）。

锁死这几件事（都是自动化最容易出错、且出错了当天就丢额度的点）：
  1. **只领今天没领的**：上游说 today_checked_in=True 时不领。
  2. **本地当日记录是第二道闸门**：上游状态还是旧值时也不重复领。
  3. **跨重启不重复领**：落盘状态能挡住"重启后立刻又领一遍"。
  4. **协议不清楚时不盲领**：today_checked_in 缺失 / 活动未开始 → 不动手。
  5. **关掉开关是真的关掉**：enabled=False 时一个上游请求都不发。
  6. **一个账号失败不影响其他账号**，且失败的当天会被再试。
  7. **落盘文件是原子写且人类可读**，坏文件不会把调度器搞崩。

用法：python3 tests/test_autocheckin.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.checkin import AutoCheckin  # noqa: E402


class Check:
    def __init__(self) -> None:
        self.p = self.f = 0

    def ok(self, cond: bool, label: str, detail: str = "") -> None:
        if cond:
            self.p += 1
            print(f"  ✅ {label}")
        else:
            self.f += 1
            print(f"  ❌ {label} {detail}")


class FakePool:
    """假账号池：只实现 auto_checkin_sweep 依赖的那一个方法。

    这里的"上游状态"由测试直接摆布 —— 这正是把签到策略与网络分开的价值：
    策略的错误（多领、漏领、重启后重领）不需要真上游就能测出来。
    """

    def __init__(self, statuses: dict, claim_ok: bool = True) -> None:
        # statuses: account_id -> checkin_status 的返回（或 "raise" 表示上游炸了）
        self.statuses = statuses
        self.claim_ok = claim_ok
        self.calls: list[str] = []          # 每次 sweep 收到的 skip_ids（字符串化）
        self.claimed: list[str] = []        # 真的发出去的领取请求

    def _row(self, aid: str) -> dict:
        return {"account_id": aid, "uin": aid, "nickname": "昵称" + aid}

    def auto_checkin_sweep(self, skip_ids=None, gap_s: float = 0.0, **kw) -> list[dict]:
        self.calls.append(("|".join(sorted(skip_ids or []))))
        out = []
        for aid, st in self.statuses.items():
            row = self._row(aid)
            if skip_ids and aid in skip_ids:
                out.append({**row, "action": "already_local"})
                continue
            if st == "raise":
                out.append({**row, "action": "error", "error": "上游 500"})
                continue
            if st.get("today_checked_in") is True:
                out.append({**row, "action": "already"})
                continue
            if st.get("active") is False:
                out.append({**row, "action": "inactive", "error": "活动未开始"})
                continue
            if st.get("today_checked_in") is None:
                out.append({**row, "action": "unknown", "error": "未返回状态"})
                continue
            if not self.claim_ok:
                out.append({**row, "action": "error", "error": "领取被拒"})
                continue
            self.claimed.append(aid)
            out.append({**row, "action": "claimed", "credited": 1.5})
        return out


def main() -> int:
    c = Check()
    tmp = Path(tempfile.mkdtemp(prefix="wbgw-checkin-"))

    # [1] 只领今天没领的
    print("\n[1] 只领今天没领的账号")
    pool = FakePool({
        "a": {"today_checked_in": False, "active": True},
        "b": {"today_checked_in": True, "active": True},
        "c": {"today_checked_in": False, "active": True},
    })
    ac = AutoCheckin(pool, path=tmp / "s1.json", interval_s=1800, jitter_s=0, gap_s=0)
    summary = ac.run_once("test", with_rows=True)
    c.ok(pool.claimed == ["a", "c"], "只对未领取的账号发起领取", str(pool.claimed))
    c.ok(summary["claimed"] == 2 and summary["settled"] == 3,
         "汇总口径正确（领到 2 / 已领 3）", json.dumps(summary, ensure_ascii=False))

    # [2] 同一进程内第二轮：本地记录挡住重复领取
    print("\n[2] 同一进程内第二轮不重复领")
    pool.claimed.clear()
    ac.run_once("test2")
    c.ok(pool.claimed == [], "第二轮不再领取", str(pool.claimed))
    c.ok(ac.status()["claimed_today"] == 3, "本地记录 3 个账号今天已领",
         str(ac.status()["claimed_today"]))

    # [3] 跨重启：新建实例从落盘状态恢复，仍然不重复领
    print("\n[3] 重启后不重复领（落盘状态）")
    ac2 = AutoCheckin(pool, path=tmp / "s1.json", interval_s=1800, jitter_s=0, gap_s=0)
    pool.claimed.clear()
    ac2.run_once("after-restart")
    c.ok(pool.claimed == [], "重启后第一天不重复领取", str(pool.claimed))
    c.ok(pool.calls[-1].split("|") == ["a", "b", "c"], "三个账号都进了 skip 名单",
         pool.calls[-1])

    # [4] 协议不清楚时不盲领
    print("\n[4] 状态不明确时不盲领")
    pool4 = FakePool({
        "x": {"today_checked_in": None, "active": True},
        "y": {"today_checked_in": False, "active": False},
    })
    ac4 = AutoCheckin(pool4, path=tmp / "s4.json", interval_s=1800, jitter_s=0, gap_s=0)
    s4 = ac4.run_once("unknown")
    c.ok(pool4.claimed == [], "字段缺失 / 活动未开始都不领取", str(pool4.claimed))
    c.ok(s4["failed"] == 2, "两个账号都记成待重试", json.dumps(s4, ensure_ascii=False))

    # [5] 关掉开关 = 一个请求都不发
    print("\n[5] GATEWAY_AUTO_CHECKIN=0 时完全不动")
    pool5 = FakePool({"a": {"today_checked_in": False, "active": True}})
    ac5 = AutoCheckin(pool5, path=tmp / "s5.json", enabled=False)
    c.ok(ac5.run_once("disabled") is None, "run_once 返回 None")
    c.ok(ac5.tick() is None, "tick 也返回 None")
    c.ok(pool5.calls == [], "没有调用账号池（零上游请求）", str(pool5.calls))
    c.ok(ac5.status()["enabled"] is False, "状态里 enabled=False")

    # [6] 失败当天会重试；一个账号失败不影响其他账号
    print("\n[6] 失败当天重试，且不影响其他账号")
    pool6 = FakePool({
        "good": {"today_checked_in": False, "active": True},
        "bad": "raise",
    })
    ac6 = AutoCheckin(pool6, path=tmp / "s6.json", interval_s=1800, jitter_s=0, gap_s=0)
    ac6.run_once("r1")
    c.ok(pool6.claimed == ["good"], "好账号照常领到", str(pool6.claimed))
    pool6.claimed.clear()
    ac6.run_once("r2")
    c.ok(pool6.claimed == [], "已领的 good 不再领（bad 仍在重试）", str(pool6.claimed))
    c.ok(pool6.calls[-1] == "good", "skip 名单里只有 good（bad 没被误标为已领）",
         pool6.calls[-1])

    # [7] 状态文件：原子写、可读、坏文件不致命
    print("\n[7] 状态文件")
    doc = json.loads((tmp / "s1.json").read_text(encoding="utf-8"))
    c.ok(doc["version"] == 1 and doc["accounts"]["a"]["credited"] == 1.5,
         "落盘内容可读且带金额", json.dumps(doc["accounts"]["a"], ensure_ascii=False))
    c.ok(not (tmp / "s1.json.tmp").exists(), "临时文件已 rename 掉")
    broken = tmp / "broken.json"
    broken.write_text("{ 这不是 JSON", encoding="utf-8")
    ac7 = AutoCheckin(FakePool({}), path=broken)
    c.ok(ac7.status()["accounts"] == {}, "坏状态文件被安全忽略（不抛异常）")

    # [8] 下一轮时间：间隔 + 抖动，且不会排到过去
    print("\n[8] 下一轮时间")
    now = time.time()
    ac8 = AutoCheckin(FakePool({"a": {"today_checked_in": True, "active": True}}),
                      path=tmp / "s8.json", interval_s=1800, jitter_s=600, gap_s=0)
    ac8.run_once("sched")
    status = ac8.status()
    c.ok(status["next_run_in_s"] is not None and 1800 <= status["next_run_in_s"] <= 2401,
         "下一轮落在 [间隔, 间隔+抖动] 之间", f"{status['next_run_in_s']}s")
    c.ok(status["last_run_at"] >= now, "last_run_at 已更新")
    c.ok(status["today"] == time.strftime("%Y-%m-%d"), "日期口径是本地自然日", status["today"])

    # [9] 手动领取记账（立即签到按钮）：记成已领，自动调度不再重复
    print("\n[9] 立即签到后的记账")
    pool9 = FakePool({"a": {"today_checked_in": False, "active": True}})
    ac9 = AutoCheckin(pool9, path=tmp / "s9.json", interval_s=1800, jitter_s=0, gap_s=0)
    ac9.note_manual([{"account_id": "a", "ok": True, "credited": 2.0}])
    ac9.run_once("after-manual")
    c.ok(pool9.claimed == [], "手动领过的账号自动调度不再领", str(pool9.claimed))
    c.ok(ac9.status()["accounts"]["a"]["action"] == "claimed_manual", "记账动作是 claimed_manual")

    print(f"\n通过 {c.p} / {c.p + c.f}")
    return 1 if c.f else 0


if __name__ == "__main__":
    raise SystemExit(main())
