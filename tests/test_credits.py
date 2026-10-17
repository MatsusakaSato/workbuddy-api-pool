"""积分分桶与「基础套餐积分优先」轮换测试（离线，不发网络请求、不耗真实积分）。

覆盖两件事：

  A. **余额解析**（gateway/upstream.py::summarize_credits）
     这是「积分显示不准确」的根因所在：
       · 旧代码读 `Data.TotalDosage`，而它把月度套餐的**池值**（CapacityRemain）
         当成余额。实测 330100178623 返回池 500 / 周期 0，旧读数 2353，
         真实可花 1854.11 —— 虚高近 500，而那是一个**已经耗尽本周期**的号。
       · `*Precise` 是带小数的字符串；只读整数会丢掉 .61 这类零头。
       · 基础套餐（CapacityType 4）与平台奖励（CapacityType 1）必须分开。
    夹具直接用**真实响应原文**（2026-10-17 抓取），不是自己拼出来的形状。

  B. **轮换优先使用基础套餐积分**（gateway/rotation.py 第 3 层 w_base）
     · 基础多的号被选中得更频繁（方向性断言，不锁精确比例）；
     · `w_base=0` 时与上一版行为**逐次一致**（回滚开关真的能回到旧行为）；
     · 基础积分为 0 **不是**不可用 —— 它仍然能被选中（Layer 1 不得被污染）；
     · 基础合计全为 0 时退化成等概率，绝不影响可得性（不变量：pick 不返回 None）。

用法：python3 tests/test_credits.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import copy
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway import config, pool as pool_mod, upstream  # noqa: E402
from gateway.creds import Credential  # noqa: E402
from gateway.rotation import RotationPolicy, RotationScheduler  # noqa: E402


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


def row(**kw) -> dict:
    """构造一条 Accounts[] 行（只放解析器关心的字段）。"""
    base = {
        "Uin": "1000", "CapacityType": 1, "Status": 0,
        "CapacityRemain": 0, "CapacitySize": 100,
        "CycleCapacityRemain": 0, "CycleCapacitySize": 100,
        "CycleCapacityEndTime": "", "ExpiredTime": "",
    }
    base.update(kw)
    return base


# 2026-10-17 真实抓取：账号 330100178623（昵称 Rain.）
# 关键对照：月度套餐 CapacityRemain=500 但 CycleCapacityRemain=0（本周期已用光），
# 而旧代码读的 TotalDosage=2353 —— 这正是「显示不准确」的证据。
REAL_RAIN = {
    "code": 0, "msg": "OK",
    "data": {"Response": {"Data": {
        "TotalCount": 11, "TotalDosage": 2353,
        "Accounts": [
            {"CapacityType": 4, "Status": 0, "PackageName": "CodeBuddy个人体验版",
             "CapacityRemain": 500, "CapacitySize": 500,
             "CycleCapacityRemain": 0, "CycleCapacitySize": 500, "CycleCapacityUsed": 500,
             "CycleEndTime": "2026-10-31 23:59:59", "ExpiredTime": "",
             "CapacityRemainPrecise": "500", "CycleCapacityRemainPrecise": "0"},
            {"CapacityType": 1, "Status": 3, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 0, "CapacitySize": 100,
             "CycleCapacityRemain": 0, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-10-28 11:19:53", "ExpiredTime": "2026-09-28 15:15:08",
             "CycleCapacityRemainPrecise": "0"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版拉新权益包",
             "CapacityRemain": 831, "CapacitySize": 1000,
             "CycleCapacityRemain": 831, "CycleCapacitySize": 1000,
             "CycleEndTime": "2026-12-28 16:52:01", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "831.53000015"},
            {"CapacityType": 1, "Status": 3, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 0, "CapacitySize": 100,
             "CycleCapacityRemain": 0, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-10-31 11:29:37", "ExpiredTime": "2026-10-08 17:13:27",
             "CycleCapacityRemainPrecise": "0"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 23, "CapacitySize": 100,
             "CycleCapacityRemain": 23, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-11-08 16:16:54", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "23.44000043"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 100, "CapacitySize": 100,
             "CycleCapacityRemain": 100, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-11-09 16:26:44", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "100"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 100, "CapacitySize": 100,
             "CycleCapacityRemain": 100, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-11-10 09:47:38", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "100"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 100, "CapacitySize": 100,
             "CycleCapacityRemain": 100, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-11-12 09:21:39", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "100"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 100, "CapacitySize": 100,
             "CycleCapacityRemain": 100, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-11-16 10:26:45", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "100"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 500, "CapacitySize": 500,
             "CycleCapacityRemain": 500, "CycleCapacitySize": 500,
             "CycleEndTime": "2026-11-16 15:28:55", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "500"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 100, "CapacitySize": 100,
             "CycleCapacityRemain": 100, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-11-17 06:49:39", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "100"},
        ],
    }}},
}

# 2026-10-17 真实抓取：账号 330127845755 —— 基础套餐本周期还剩 307.45（不是池值 500）
REAL_CN_9107 = {
    "code": 0, "msg": "OK",
    "data": {"Response": {"Data": {
        "TotalCount": 5, "TotalDosage": 2700,
        "Accounts": [
            {"CapacityType": 4, "Status": 0, "PackageName": "CodeBuddy个人体验版",
             "CapacityRemain": 500, "CapacitySize": 500,
             "CycleCapacityRemain": 307, "CycleCapacitySize": 500,
             "CycleEndTime": "2026-10-31 23:59:59", "ExpiredTime": "",
             "CapacityRemainPrecise": "500", "CycleCapacityRemainPrecise": "307.45000012"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版拉新权益包",
             "CapacityRemain": 1500, "CapacitySize": 1500,
             "CycleCapacityRemain": 1500, "CycleCapacitySize": 1500,
             "CycleEndTime": "2026-11-16 14:11:18", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "1500"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 100, "CapacitySize": 100,
             "CycleCapacityRemain": 100, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-11-16 14:12:53", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "100"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 500, "CapacitySize": 500,
             "CycleCapacityRemain": 500, "CycleCapacitySize": 500,
             "CycleEndTime": "2026-11-16 15:28:10", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "500"},
            {"CapacityType": 1, "Status": 0, "PackageName": "CodeBuddy个人版国内运营裂变包",
             "CapacityRemain": 100, "CapacitySize": 100,
             "CycleCapacityRemain": 100, "CycleCapacitySize": 100,
             "CycleEndTime": "2026-11-17 03:23:24", "ExpiredTime": "",
             "CycleCapacityRemainPrecise": "100"},
        ],
    }}},
}


def mk(uin: str, **kw) -> Credential:
    return Credential(
        uin=uin, uid=f"uid-{uin}", domain="www.workbuddy.cn",
        access_token="test-token",
        expires_at_ms=int((time.time() + 3600) * 1000),
        persistable=False, **kw,
    )


# --------------------------------------------------------------------------
# A. 余额解析
# --------------------------------------------------------------------------

def test_parse(c: Check) -> None:
    print("1. 真实响应：基础 / 奖励分桶（Rain. —— 月度套餐本周期已用光）")
    s = upstream.summarize_credits(upstream.package_rows(REAL_RAIN),
                                   total_dosage=REAL_RAIN["data"]["Response"]["Data"]["TotalDosage"])
    c.ok(s["base_credits"] == 0.0, "基础套餐读周期值 0（不是池值 500）", s["base_credits"])
    # 831.53 + 23.44 + 100×3 + 500 + 100 + 100（两行 100 属不同包，各算一次）
    c.ok(abs(s["reward_credits"] - 1854.97) < 0.01,
         "奖励积分 = 831.53+23.44+100×5+500 = 1854.97", s["reward_credits"])
    c.ok(s["total_credits"] == s["base_credits"] + s["reward_credits"], "总数 = 基础 + 奖励")
    c.ok(s["total_dosage"] == 2353, "TotalDosage 原样保留（只用于对账）")
    c.ok(s["total_credits"] < s["total_dosage"],
         "可花余额低于 TotalDosage —— 后者把已耗尽的月度池算进去了",
         f'{s["total_credits"]} vs {s["total_dosage"]}')
    c.ok(len(s["packages"]) == 9, "两条 Status=3 的失效包被剔除（11 行 → 9 个包）",
         len(s["packages"]))
    c.ok(s["packages"][0]["kind"] == "base", "明细里基础套餐排在最前（先花它）")
    c.ok(s["base_refresh_at"] is not None, "基础积分带刷新时刻（周期结束）")

    print("2. 真实响应：基础套餐有零头时读 *Precise（139****9107）")
    s2 = upstream.summarize_credits(upstream.package_rows(REAL_CN_9107),
                                    total_dosage=REAL_CN_9107["data"]["Response"]["Data"]["TotalDosage"])
    c.ok(abs(s2["base_credits"] - 307.45) < 0.001, "基础 = 307.45（池值是 500）", s2["base_credits"])
    c.ok(abs(s2["reward_credits"] - 2200.0) < 0.001, "奖励 = 1500+100+500+100 = 2200", s2["reward_credits"])
    c.ok(abs(s2["total_credits"] - 2507.45) < 0.001, "总 = 2507.45（旧读法 2700，虚高 192.55）",
         s2["total_credits"])

    print("3. 精确值为 0 时不能回落到池值")
    r = row(CapacityType=4, CapacityRemain=500, CapacityRemainPrecise="500",
            CycleCapacityRemain=0, CycleCapacityRemainPrecise="0")
    s3 = upstream.summarize_credits([r], now=1_700_000_000)
    c.ok(s3["base_credits"] == 0.0, "CycleCapacityRemainPrecise='0' 读作 0，不 `or` 回落成 500",
         s3["base_credits"])

    print("4. 没有周期字段的合成行回退读池（避免静默报 0）")
    s4 = upstream.summarize_credits([{"CapacityType": 1, "CapacityRemain": 42, "CapacitySize": 100}],
                                    now=1_700_000_000)
    c.ok(s4["total_credits"] == 42.0, "无周期家族 → 回退 CapacityRemain", s4["total_credits"])

    print("5. 失效 / 过期 / 负值 不进入余额")
    soon = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(1_600_000_000))
    rows = [
        row(CapacityType=1, Status=3, CycleCapacityRemain=100),          # 失效
        row(CapacityType=1, Status=0, CycleCapacityRemain=100, ExpiredTime=soon),  # 过期
        row(CapacityType=1, Status=0, CycleCapacityRemain=-50),          # 负值
        row(CapacityType=1, Status=0, CycleCapacityRemain=10),           # 正常
    ]
    s5 = upstream.summarize_credits(rows, now=1_700_000_000)
    c.ok(s5["total_credits"] == 10.0, "只剩那条正常行计入（-50 不为负也更不计）", s5["total_credits"])

    print("6. 奖励积分「24 小时内过期」单独拎出来")
    tomorrow = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(1_700_000_000 + 3600))
    later = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(1_700_000_000 + 30 * 86400))
    s6 = upstream.summarize_credits([
        row(CapacityType=1, CycleCapacityRemain=60, ExpiredTime=tomorrow),
        row(CapacityType=1, CycleCapacityRemain=200, ExpiredTime=later),
        row(CapacityType=4, CycleCapacityRemain=500, ExpiredTime="",
            CycleEndTime="2026-10-31 23:59:59"),
    ], now=1_700_000_000)
    c.ok(s6["reward_expiring_soon"] == 60.0, "1 小时内过期的 60 分被标记（基础积分不算在里面）",
         s6["reward_expiring_soon"])
    c.ok(s6["total_credits"] == 760.0, "仍全部计入总余额（过期只是提示，不是扣减）", s6["total_credits"])

    print("7. 空输入不崩、返回 0 而不是 None")
    s7 = upstream.summarize_credits([], total_dosage=None)
    c.ok(s7["total_credits"] == 0.0 and s7["base_credits"] == 0.0, "空明细 → 全 0")
    s8 = upstream.summarize_credits([None, "junk", 42], now=1)     # type: ignore[list-item]
    c.ok(s8["total_credits"] == 0.0, "脏行被忽略，不抛异常")

    print("8. 只有 TotalDosage、没有逐包明细时不猜余额")
    c.ok(upstream.package_rows({"data": {"Response": {"Data": {"TotalDosage": 1234}}}}) == [],
         "package_rows 取不到 Accounts → 空列表（fetch_credits 据此返回 raw，不回落 TotalDosage）")


# --------------------------------------------------------------------------
# B. 轮换：基础套餐积分优先
# --------------------------------------------------------------------------

def _sim(w_base: float, totals, bases, n: int = 600, seed: int = 7,
         base_priority: bool = True, min_interval_s: float = 0.0,
         slice_s: float = 0.001) -> dict:
    """跑 n 次「pick → 释放」循环，返回各账号命中次数。全离线、确定性（固定种子）。

    ⚠️ `now` 必须取 `time.monotonic()`，**不能**自己递增一个假时间轴。
    第 2 层把 `last_used_at` 与 `now` 对比来算「等了多久」，而 `mark_used`
    写的是**真实** monotonic。假时间轴（1000、1001…）与真实 monotonic 相差
    数万秒，远超时间片宽度，于是第 2 层每次都会收窄到「最久没用的那个」——
    第 3 层的加权就再也没机会生效。这条在 3.9 与 3.12 上复现方式不同
    （3.9 上恰好有一步重新选中了已有 `last_used_at` 的号而"碰巧"能看出版差别），
    属于**测试缺陷**而不是调度缺陷：修复后两个解释器给出同样的结论。

    另外：`min_interval_s=0, slice_s=0.001` 这组参数让第 2 层近乎不存在，
    适合单独观察第 3 层的加权。**生产参数（5s / 30s）由 `test_rotation.py` 的
    场景 17 覆盖** —— 那才是线上真实遇到的形态（见下面第 11 项）。
    """
    pol = RotationPolicy(w_base=w_base, min_interval_s=min_interval_s, slice_s=slice_s,
                         max_concurrency=1, w_credit=1.0, w_runway=2.0, w_cool=0.5,
                         base_priority=base_priority)
    sched = RotationScheduler(pol, random.Random(seed))
    creds = [mk(u) for u in totals]
    counts = {x.uin: 0 for x in creds}
    for _ in range(n):
        now = time.monotonic()
        got = sched.pick(creds, totals, now, base_credits=bases)
        if got is None:
            return {"__none__": n}
        counts[got.uin] += 1
        sched.release_claim(got.uin)
        sched.mark_used(got.uin, time.time())
    return counts


def test_rotation(c: Check) -> None:
    print("9. 基础套餐优先是**资格**问题：有基底的号拿走全部流量（Layer 1 分级）")
    totals = {"A": 2000.0, "B": 2000.0}
    bases = {"A": 500.0, "B": 0.0}
    # 关闭分级 → 退回「只按总余额 + 权重」：两个号都还能被选中（没有一个被饿死），
    # 这正说明分级是**新加的那道门槛**，而不是 W_BASE 这个权重。W_BASE 仍在
    # Layer 3 里给基础多的号一点偏好，所以份额不是严格 50/50（方向性断言即可）。
    off = _sim(1.5, totals, bases, base_priority=False)
    c.ok(off.get("B", 0) > 0, "base_priority=False 时基础耗尽的 B 仍能分到流量（上一版行为）", off)
    c.ok(off["A"] > off["B"], "关掉分级后仅剩 W_BASE 的弱偏好（A 略多，但 B 不挨饿）", off)
    # 开启分级（默认）→ B 的基础套餐已耗尽，**一次都不该**被选中。
    on = _sim(config.ROTATION_W_BASE if config.ROTATION_W_BASE else 1.5, totals, bases)
    c.ok(on["A"] == sum(on.values()), "开启后基础 500 的 A 拿走 100%（B 基础=0）", on)
    c.ok(on.get("B", 0) == 0, "基础套餐耗尽的 B 被 Layer 1 分级挡住，0 次命中", on)

    print("10. W_BASE 只影响同级别内的偏好，**不是**这条策略的开关")
    # 两个号基础都有剩：W_BASE 越大，基础多的那个越占优（方向性）。
    totals_nb = {"A": 2000.0, "B": 2000.0}
    bases_nb = {"A": 500.0, "B": 100.0}
    seq = [_sim(w, totals_nb, bases_nb, n=900)["A"] for w in (0.0, 0.5, 1.5, 3.0)]
    c.ok(seq[0] <= seq[1] <= seq[2] <= seq[3],
         f"同级别内命中次数随 w_base 非递减（{seq}）")
    # 关键回归：把 W_BASE 拉到很大也压不到 0 —— 这正是第一版失效的证据。
    huge = _sim(50.0, totals, bases)
    c.ok(huge.get("B", 0) == 0,
         "W_BASE=50 时 B 仍为 0（但这是分级给的，不是 W_BASE 给的）", huge)

    print("11. 基础积分为 0 的号**不是**不可用 —— 全员耗尽时它必须重新接过流量")
    # 「优先」不能被写成「只准」：月末所有号基础都清零，此时谁都不能被踢出池子。
    totals_all0 = {"A": 5000.0, "B": 10.0}
    r_all0 = _sim(1.5, totals_all0, {"A": 0.0, "B": 0.0})
    c.ok("__none__" not in r_all0, "全员基础为 0 时不出现选不出号（None）", r_all0)
    c.ok(r_all0.get("A", 0) > 0 and r_all0.get("B", 0) > 0,
         "全员基础为 0 时所有号都重新可用（分级自动失效）", r_all0)
    # 只有 B 还有基底时，A（基础=0）被挡；这与「A 不可用」是两件事：
    # A 的总余额仍有 5000，它只是这一轮不该先花而已。
    r_one = _sim(1.5, totals_all0, {"A": 0.0, "B": 10.0})
    c.ok(r_one.get("A", 0) == 0 and r_one.get("B", 0) > 0,
         "只有 B 有基底时 A 让位（但 A 并未被标记 exhausted）", r_one)

    print("12. 基础合计全为 0 / 完全不传 base_credits：不影响可得性")
    r_zero = _sim(1.5, totals, {"A": 0.0, "B": 0.0})
    c.ok("__none__" not in r_zero, "base 全 0 时不返回 None")
    c.ok(abs(r_zero["A"] - r_zero["B"]) < 120, "base 全 0 时退化为接近等概率", r_zero)

    pol_no_base = RotationPolicy(w_base=1.5, base_priority=True,
                                 min_interval_s=0.0, slice_s=0.001, max_concurrency=1)
    sched = RotationScheduler(pol_no_base, random.Random(3))
    creds = [mk("A"), mk("B")]
    picks = []
    for i in range(50):
        got = sched.pick(creds, totals, 1000.0 + i)
        picks.append(got)
        if got is not None:
            sched.release_claim(got.uin)      # 与生产路径一致：pick 必须配一次 release
    c.ok(all(x is not None for x in picks), "不传 base_credits（旧调用形态）仍能正常选号")

    print("13. 回滚开关 base_priority=False 与上一版行为一致")
    a = _sim(1.5, totals, bases, base_priority=False)
    b = _sim(1.5, totals, bases, base_priority=False)
    c.ok(a == b, "同种子下两次结果一致（确定性）", (a, b))
    c.ok(config.ROTATION_BASE_PRIORITY is True, "默认开启（GATEWAY_ROTATION_BASE_PRIORITY 默认 1）")
    c.ok(RotationPolicy.from_config().base_priority == config.ROTATION_BASE_PRIORITY,
         f"policy.base_priority == config.ROTATION_BASE_PRIORITY ({config.ROTATION_BASE_PRIORITY})")

    print("14. 未知 ≠ 0：没拉到余额的号不能被当成「基础耗尽」")
    # `_base_of` 对缺失账号返回 None，`_bucket_tier` 必须保留它们。
    pol_t14 = RotationPolicy(base_priority=True)
    sched_t14 = RotationScheduler(pol_t14, random.Random(14))
    known, unknown = mk("known"), mk("unknown")
    out = sched_t14._bucket_tier([known, unknown], {"known": 100.0})
    c.ok(unknown in out, "base 余额未知的号被保留（不因缺席被饿死）", out)
    c.ok(known in out, "有基底的号保留", out)
    # 全是未知 → 分级不生效
    out2 = sched_t14._bucket_tier([known, unknown], None)
    c.ok(len(out2) == 2, "完全不传 base_credits 时分级不生效", out2)

    # w_base=0 只关偏好项：权重与「不传 base」逐位相同。
    off_pol = RotationPolicy(w_base=0.0)
    creds = [mk("A"), mk("B")]
    sched = RotationScheduler(off_pol, random.Random(11))
    w = sched._weights(creds, {"A": 5000.0, "B": 10.0}, 1000.0, {"A": 5000.0, "B": 0.0})
    sched2 = RotationScheduler(off_pol, random.Random(11))
    w_ref = sched2._weights(creds, {"A": 5000.0, "B": 10.0}, 1000.0, None)
    c.ok(w == w_ref, "w_base=0 时权重与「不传 base」逐位相同", (w, w_ref))


# --------------------------------------------------------------------------
# C. Pool ↔ 快照
# --------------------------------------------------------------------------

def test_pool_snapshot(c: Check) -> None:
    print("15. Pool.snapshot：两个桶分开展示，未知 ≠ 0")
    p = pool_mod.Pool()
    a, b = mk("111"), mk("222")
    p._creds = {"111": a, "222": b}       # 只改内存，不落盘（persistable=False）
    p._store_credits("111", {"total_credits": 1854.97, "base_credits": 0.0,
                             "reward_credits": 1854.97, "total_dosage": 2353})
    snap = p.snapshot()
    row_a = next(r for r in snap["accounts"] if r["account_id"] == "111")
    row_b = next(r for r in snap["accounts"] if r["account_id"] == "222")
    c.ok(row_a["credits"] == 1854.97 and row_a["base_credits"] == 0.0
         and row_a["reward_credits"] == 1854.97,
         "accounts[] 带 credits / base_credits / reward_credits",
         (row_a["credits"], row_a["base_credits"], row_a["reward_credits"]))
    c.ok(row_b["credits"] is None and row_b["base_credits"] is None,
         "没拉到余额的账号是 None，不是 0")
    c.ok(snap["credits"]["base"] == 0.0 and snap["credits"]["total"] == 1854.97,
         "池级合计只累加已知账号", snap["credits"])
    c.ok(snap["credits"]["known_accounts"] == 1 and snap["credits"]["unknown_accounts"] == 1,
         "unknown_accounts 如实计数")

    print("16. 解析失败不会写进余额（保留上一次的读数，而不是变成 0）")
    before = p.credits_total("111")
    wrote = p._store_credits("111", {"raw": {"x": 1}})
    c.ok(wrote is False and p.credits_total("111") == before,
         "只有 raw 的响应不覆盖已有余额", (wrote, p.credits_total("111")))

    print("17. credits_view 分开返回总数与基础桶")
    t, bs = p.credits_view("111")
    c.ok(t == {"111": 1854.97} and bs == {"111": 0.0}, "总数与基础桶互不串味", (t, bs))


def main() -> int:
    c = Check()
    test_parse(c)
    test_rotation(c)
    test_pool_snapshot(c)
    print()
    print("=" * 46)
    print(f"通过 {c.p} / {c.p + c.f}")
    print("=" * 46)
    return 0 if c.f == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
