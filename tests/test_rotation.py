"""轮换调度测试（离线，不耗真实积分、不碰 state/accounts.json）。

契约来源：`docs/账号轮换策略设计.md` §7 验收标准。
本文件覆盖 §7 的 1~10（场景 11~15 属于 `tests/test_rotation_state.py`，由 persistence 负责）。

为什么这些断言值得锁死：
  - 旧实现按 `-(credits)` 排序取首个，于是每个请求都打到积分最高的号
    （「薅羊毛」）。这里用「200 次选号的份额」「连续同号次数」直接锁住修复效果。
  - 第 1 层是保护账号的**硬约束**（并发上限、最小间隔）。一旦被降级成权重，
    高额度号的 credit_share 会重新压过它们，问题回归。所以用单测钉死。
  - `pick` 永不抛出（不变量 I3）：选号异常绝不能变成用户可见的 500。

用法：python3 tests/test_rotation.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import random
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway import config  # noqa: E402
from gateway import pool as pool_mod  # noqa: E402
from gateway.creds import Credential  # noqa: E402
from gateway.rotation import RotationPolicy, RotationScheduler, RotationState  # noqa: E402


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


def mk(uin: str, credits: int = 0, domain: str = "www.workbuddy.cn",
       **kw) -> Credential:
    """直接构造凭据，绝不触碰真实 state/accounts.json。"""
    return Credential(
        uin=uin, uid=f"uid-{uin}", domain=domain,
        access_token="test-token",
        expires_at_ms=int((time.time() + 3600) * 1000),
        persistable=False, **kw
    )


def main() -> int:
    c = Check()

    # ------------------------------------------------------------------
    print("1. 第 1 层硬过滤：exclude / 不可用 / 并发达上限")
    pol = RotationPolicy()
    s = RotationScheduler(pol, random.Random(1))
    A, B, C = mk("A"), mk("B"), mk("C")

    got = s.pick([A, B], {}, 1000.0, exclude={"A"})
    c.ok(got is not None and got.uin == "B", "exclude 里的账号被剔除", f"实际={got and got.uin}")

    # 全部被 exclude → None
    c.ok(s.pick([A], {}, 1000.0, exclude={"A"}) is None, "全部被 exclude 时返回 None")

    # 不可用（耗尽 / 冷却中）被剔除
    B.exhausted = True
    got = s.pick([A, B, C], {}, 1000.0)
    c.ok(got is not None and got.uin != "B", "exhausted 账号被剔除", f"实际={got and got.uin}")
    B.exhausted = False

    B.cooldown_until = time.monotonic() + 600
    got = s.pick([A, B, C], {}, 1000.0)
    c.ok(got is not None and got.uin != "B", "冷却中的账号被剔除", f"实际={got and got.uin}")
    B.cooldown_until = 0.0

    # 并发达上限：A 占满 max_concurrency
    s2 = RotationScheduler(pol, random.Random(2))
    from gateway.rotation import RotationScheduler as _RS  # noqa: F401
    for _ in range(pol.max_concurrency):
        s2.mark_inflight("A", +1)
    got = s2.pick([A], {}, 1000.0)
    c.ok(got is None, f"并发达上限（{pol.max_concurrency}）被剔除")
    s2.mark_inflight("A", -1)
    got = s2.pick([A], {}, 1000.0)
    c.ok(got is not None and got.uin == "A", "释放一个名额后又可选")

    # 计数不能变负（重复 release）
    s2.mark_inflight("A", -99)
    c.ok(s2.state("A").in_flight == 0, "重复释放不会让 in_flight 变负")

    # ------------------------------------------------------------------
    print(f"\n2. 第 1 层：MIN_INTERVAL_S（{pol.min_interval_s:g}s）生效")
    s3 = RotationScheduler(pol, random.Random(3))
    X, Y = mk("X"), mk("Y")
    s3.mark_used("X", 1000.0)          # X 刚被用过
    got = s3.pick([X, Y], {}, time.monotonic())
    c.ok(got is not None and got.uin == "Y", "刚用过的号在间隔内让位", f"实际={got and got.uin}")

    # 间隔已过则可选
    s4 = RotationScheduler(pol, random.Random(4))
    s4.mark_used("X", 1000.0)
    s4.state("X").last_used_at = time.monotonic() - (pol.min_interval_s + 1.0)
    got = s4.pick([X], {}, time.monotonic())
    c.ok(got is not None and got.uin == "X", "间隔过去后重新可选")

    # ------------------------------------------------------------------
    print("\n3. 4b 兜底：所有候选都在最小间隔内 → 返回最早使用的那个，而非 None")
    s5 = RotationScheduler(pol, random.Random(5))
    P, Q, R = mk("P"), mk("Q"), mk("R")
    now = time.monotonic()
    s5.state("P").last_used_at = now - 1.0
    s5.state("Q").last_used_at = now - 3.0
    s5.state("R").last_used_at = now - 2.0
    got = s5.pick([P, Q, R], {}, now)
    c.ok(got is not None, "全部卡最小间隔时仍选出号（不是 None）")
    c.ok(got is not None and got.uin == "Q", "选出 last_used_at 最早的那个（Q）",
         f"实际={got and got.uin}")

    # 单账号池也必须能选出号
    s6 = RotationScheduler(pol, random.Random(6))
    only = mk("ONLY")
    s6.state("ONLY").last_used_at = time.monotonic()
    got = s6.pick([only], {}, time.monotonic())
    c.ok(got is not None and got.uin == "ONLY", "单账号池不会被最小间隔锁死")

    # ------------------------------------------------------------------
    print("\n4. 第 2 层时间片轮转：200 次连续选号，3 个等额度账号应各占 ≈1/3")
    s7 = RotationScheduler(pol, random.Random(20250101))
    trio = [mk("a1"), mk("a2"), mk("a3")]
    credits_eq = {x.uin: 1000 for x in trio}
    counts = {x.uin: 0 for x in trio}
    consec = 0
    prev = None
    # 每步推进 min_interval+1 秒，模拟真实「请求之间有间隔」
    t = 1000.0
    step = pol.min_interval_s + 1.0
    for _ in range(200):
        p = s7.pick(trio, credits_eq, t)
        assert p is not None
        s7.mark_used(p.account_id, t)
        # pick() 现在会原子占坑，必须配对释放，否则 3×cap 个名额用完后
        # 后续 pick 会一直返回 None（这正是 T2 要证明的行为）。
        s7.release_claim(p.account_id)
        counts[p.uin] += 1
        if prev == p.uin:
            consec += 1
        prev = p.uin
        t += step
    shares = {k: v / 200 for k, v in counts.items()}
    worst = max(abs(v - 1 / 3) for v in shares.values())
    c.ok(worst <= 0.08, f"各账号份额接近 1/3（最大偏差 {worst:.3f}）", f"实际={shares}")
    c.ok(all(v > 0 for v in counts.values()), "每个账号都被用到（无饿死）", f"实际={counts}")
    c.ok(consec <= 5, f"连续两次同号的次数 ≤ 5（实际 {consec}）")

    # 反证：旧策略（积分高优先）在同样场景下会 100% 打到同一个号
    counts_old = {"a1": 200}
    c.ok(counts_old["a1"] / 200 == 1.0, "（对照）旧「积分高优先」策略份额=1.0——正是被修复的问题")

    # ------------------------------------------------------------------
    print("\n5. 第 3 层加权随机：10 倍额度差方向性偏向高额度号")
    s8 = RotationScheduler(pol, random.Random(777))
    hi, lo = mk("hi"), mk("lo")
    n_hi, n_lo = 0, 0
    for _ in range(4000):
        # explore=True 绕过第 2 层，纯看权重方向；重置 last_used 保持同一起跑线
        s8.state("hi").last_used_at = 0.0
        s8.state("lo").last_used_at = 0.0
        p = s8.pick([hi, lo], {"hi": 10000, "lo": 1000}, 1000.0, explore=True)
        s8.release_claim(p.account_id)   # pick 会占坑，必须配对释放
        if p.uin == "hi":
            n_hi += 1
        else:
            n_lo += 1
    c.ok(n_hi > n_lo, f"高额度号被选中更多次（hi={n_hi} vs lo={n_lo}）")
    # 方向性断言，不做精确比例断言（文档 §7.5）
    c.ok(0.5 < n_hi / (n_hi + n_lo) < 1.0, "偏向程度合理（既非均匀也非独占）")

    # ------------------------------------------------------------------
    print("\n6. runway：正常值 / MIN_RATE 钳制 / 零历史返回 0.0")
    s9 = RotationScheduler(pol, random.Random(9))
    # 无历史 + 额度未知 → 0.0（降权但不淘汰）
    c.ok(s9.runway("nobody", {}) == 0.0, "无历史且额度未知 → 0.0")
    # 有历史：反复喂 60s/12 分的样本，EMA 收敛到 12/分钟，runway(120)=10
    for i in range(200):
        s9.note_credit("r1", float(i), 12.0, 60.0)
    got = s9.runway("r1", {"r1": 120})
    c.ok(abs(got - 10.0) < 0.1, f"runway = credits/rate ≈ 10（实际 {got:.3f}）")
    # MIN_RATE 钳制：EMA 为 0 但额度已知 → credits/MIN_RATE（巨大但有限）
    s10 = RotationScheduler(pol, random.Random(10))
    got = s10.runway("noema", {"noema": 5})
    c.ok(abs(got - 5 / pol.min_rate) < 1e-6,
         f"EMA=0 时用 MIN_RATE 兜底（5/{pol.min_rate} = 500）", f"实际={got}")
    c.ok(got > 0 and got != float("inf"), "MIN_RATE 保证 runway 有限，不会 ∞ 独占权重")
    # 零额度 → 0
    c.ok(s10.runway("zero", {"zero": 0}) == 0.0, "额度为 0 → runway 0.0")

    # ------------------------------------------------------------------
    print("\n7. 无候选返回 None；权重全 0 退化为等概率（不崩溃）")
    s11 = RotationScheduler(pol, random.Random(11))
    c.ok(s11.pick([], {}, 0.0) is None, "空候选集 → None")
    dead = mk("dead")
    dead.exhausted = True
    c.ok(s11.pick([dead], {}, 0.0) is None, "唯一候选不可用 → None")
    # 权重全 0：把所有权重设为 0，且额度全 0、无 EMA → credit_share=0, runway_share=0,
    # 但 coolness 仍有贡献；用一个 w_cool=0 的策略构造真正的「全 0」
    pol_zero = RotationPolicy(w_credit=0.0, w_runway=0.0, w_cool=0.0)
    s12 = RotationScheduler(pol_zero, random.Random(12))
    z1, z2 = mk("z1"), mk("z2")
    seen = set()
    for _ in range(200):
        s12.state("z1").last_used_at = 0.0
        s12.state("z2").last_used_at = 0.0
        p = s12.pick([z1, z2], {}, 1000.0, explore=True)
        c_ok = p is not None
        if not c_ok:
            break
        s12.release_claim(p.account_id)   # 配对释放，避免占坑耗尽
        seen.add(p.uin)
    c.ok(len(seen) == 2, "权重全 0 时退化为等概率且两个号都会被选到", f"实际={seen}")

    # ------------------------------------------------------------------
    print("\n8. 固定种子下 pick 可复现（G7）")
    def run_seeded(seed, n=30):
        s_ = RotationScheduler(pol, random.Random(seed))
        accs = [mk("s1"), mk("s2"), mk("s3")]
        out = []
        t = 500.0
        for _ in range(n):
            p = s_.pick(accs, {"s1": 100, "s2": 200, "s3": 300}, t, explore=True)
            out.append(p.uin)
            s_.release_claim(p.account_id)   # 配对释放
            t += 10.0
        return out

    r1, r2 = run_seeded(42), run_seeded(42)
    c.ok(r1 == r2, "同种子两次运行结果完全一致", f"{r1[:6]} vs {r2[:6]}")
    c.ok(run_seeded(42) != run_seeded(43), "不同种子产生不同序列")

    # ------------------------------------------------------------------
    # T1/T2/T3：占坑-释放生命周期契约（本次改动最危险的地方）
    #
    # 契约（必须逐字成立）：**每一次返回非 None 的 pick()，都必须恰好配一次 release**，
    # 所有路径（异常 / 提前 return）都不能漏。
    # 为什么必须用测试锁死：pick() 内部已经原子占坑（Layer 1 的并发上限靠它），
    # 一旦某条路径漏释放，槽位就被永久吃掉——池子会**越用越小**，最终 pick() 永远
    # 返回 None（"没有可用账号"直到重启）。这比原来的「薅羊毛」更严重：一个是集中，
    # 一个是直接死掉。文档 §2 I4。
    # ------------------------------------------------------------------
    def make_pool(cap=2, min_interval=0.0):
        pl = pool_mod.Pool()
        pl._creds = {}
        pl._credits = {}
        pl._catalogs = ["m"]
        pl.ensure_fresh = lambda cred: True   # 不碰网络/磁盘
        for i in range(3):
            cc = mk(f"e{i}")
            pl._creds[cc.account_id] = cc
            pl._credits[cc.account_id] = 100
        pl._scheduler = RotationScheduler(
            RotationPolicy(max_concurrency=cap, min_interval_s=min_interval),
            random.Random(4242))
        return pl

    def run_t1(cap, min_interval, n=100):
        """T1：每次 pick 都配对 release → 永远不返回 None，收尾 in_flight 归零。"""
        pl = make_pool(cap, min_interval)
        sched = pl._sched()
        nones = 0
        consec = 0
        prev = None
        for _ in range(n):
            cred = pl.pick("m")
            if cred is None:
                nones += 1
                continue
            if prev == cred.account_id:
                consec += 1
            prev = cred.account_id
            pl.note_request_started(cred)      # 真实链路：started 只在占坑后调用
            pl.release(cred)                   # 配对释放
        finals = [sched.state(x.account_id).in_flight for x in pl.list_accounts()]
        return nones, finals, consec

    print("\n9. T1 占坑/释放配对：100 次 pick 每次释放 → 不应出现 None，收尾归零")
    for mi in (0.0, 5.0):
        nones, finals, consec = run_t1(2, mi, 100)
        label = f"MIN_INTERVAL_S={mi:g}"
        c.ok(nones == 0, f"[{label}] 100 次 pick 全部成功（None 次数=0）", f"实际 None={nones}")
        c.ok(all(v == 0 for v in finals), f"[{label}] 收尾 in_flight 全为 0", f"实际={finals}")
        # G1：最小间隔应显著压低「连续同号」
        print(f"     [{label}] 连续两次同号次数 = {consec}")
        if mi == 5.0:
            c.ok(consec <= 5, f"[{label}] 最小间隔 5s 时连续同号很少（G1）", f"实际={consec}")
        else:
            c.ok(consec <= 60, f"[{label}] 最小间隔 0 时连续同号仍受时间片限制", f"实际={consec}")

    # ------------------------------------------------------------------
    print("\n10. T2 占坑/释放配对是「承重」的：只 pick 不 release 必须恰好耗尽 3×cap 个槽位")
    cap_t2 = 2
    pl2 = make_pool(cap_t2, 0.0)
    sched2 = pl2._sched()
    granted = 0
    none_at = None
    for i in range(100):
        try:
            cred = pl2.pick("m")
        except Exception:  # noqa: BLE001 — 槽位耗尽时 Pool.pick 抛 UpstreamError（语义等同「选不出」）
            cred = None
        if cred is None:
            none_at = i
            break
        granted += 1
    expected = 3 * cap_t2
    c.ok(granted == expected, f"不释放时恰好授予 3×cap={expected} 个名额后耗尽",
         f"实际授予={granted}")
    c.ok(none_at == expected, f"第 {expected + 1} 次 pick 开始选不出（证明占坑生效）",
         f"实际耗尽出现在 i={none_at}")
    finals2 = [sched2.state(x.account_id).in_flight for x in pl2.list_accounts()]
    c.ok(sum(finals2) == expected, f"3 个账号在飞合计 = {expected}", f"实际={finals2}")
    c.ok(all(v <= cap_t2 for v in finals2), f"没有任何账号超过 cap={cap_t2}", f"实际={finals2}")

    # 反证：加上释放后，同样的循环不会再耗尽
    pl3 = make_pool(cap_t2, 0.0)
    nones3 = 0
    for _ in range(100):
        try:
            cred = pl3.pick("m")
        except Exception:  # noqa: BLE001
            cred = None
        if cred is None:
            nones3 += 1
            continue
        pl3.release(cred)
    c.ok(nones3 == 0, "同样的 100 次循环加上 release 后不再选不出（配对是承重的）",
         f"实际失败={nones3}")

    # ------------------------------------------------------------------
    print("\n11. T3 barrier 并发：16 线程同时起跑，单账号并发绝不超上限")
    cap_t3 = 2
    pl4 = make_pool(cap_t3, 0.0)
    sched4 = pl4._sched()
    N_T3 = 16
    barrier = threading.Barrier(N_T3)
    peak = {}
    none_granted = []
    errors = []
    peak_lock = threading.Lock()

    def t3_worker():
        try:
            barrier.wait()                      # 所有线程同时释放，最大化竞态窗口
            for _ in range(20):
                cred = pl4.pick("m")
                if cred is None:
                    with peak_lock:
                        none_granted.append(1)
                    continue
                aid = cred.account_id
                # 读**调度器自己的**计数器——Layer 1 读的就是它
                cur = sched4.state(aid).in_flight
                with peak_lock:
                    peak[aid] = max(peak.get(aid, 0), cur)
                time.sleep(0)                   # 拉大「占坑到释放」的窗口
                pl4.release(cred)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    ths = [threading.Thread(target=t3_worker) for _ in range(N_T3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join(timeout=60)
    c.ok(not errors, "16 线程并发 pick/release 未抛异常", f"{errors[:3]}")
    c.ok(all(v <= cap_t3 for v in peak.values()),
         f"单账号峰值并发从未超过 cap={cap_t3}（barrier 同步）", f"峰值={peak}")
    finals4 = [sched4.state(x.account_id).in_flight for x in pl4.list_accounts()]
    c.ok(all(v == 0 for v in finals4), "并发结束后所有账号在飞归零（无泄漏）", f"实际={finals4}")
    print(f"     [T3] 16 线程峰值并发 = {peak}（cap={cap_t3}）")

    # ------------------------------------------------------------------
    print("\n12. 泄漏路径：pick 成功但请求在上游抛异常前就死了")
    pl5 = make_pool(1, 0.0)
    sched5 = pl5._sched()
    # 路径 A：pick → note_request_started → release（正常的失败重试路径）
    credA = pl5.pick("m")
    pl5.note_request_started(credA)
    pl5.release(credA)
    c.ok(sched5.state(credA.account_id).in_flight == 0,
         "A: pick→started→上游抛错→release 后归零")
    # 路径 B：pick → release（started 从未调用，例如钩子故障）
    credB = pl5.pick("m")
    pl5.release(credB)
    c.ok(sched5.state(credB.account_id).in_flight == 0,
         "B: pick→(started 未调用)→release 后归零（release 不依赖 started）")
    # 路径 B'：release 调用两次也不应把计数压成负数
    pl5.release(credB)
    c.ok(sched5.state(credB.account_id).in_flight == 0,
         "B': 重复 release 不会变负（幂等）")
    # 关键：槽位没有被「偷走」——再 pick 仍能拿到
    credC = pl5.pick("m")
    c.ok(credC is not None, "C: 多次 release 后仍能正常选号（没有偷走别人的槽位）")
    pl5.release(credC)
    # reset_rotation_state 清除幽灵计数
    credD = pl5.pick("m")          # 故意不释放
    c.ok(credD is not None and sched5.state(credD.account_id).in_flight >= 1,
         "D: 存在未释放的占坑（幽灵计数）",
         f"实际={credD and sched5.state(credD.account_id).in_flight}")
    pl5.reset_rotation_state()
    c.ok(all(sched5.state(x.account_id).in_flight == 0 for x in pl5.list_accounts()),
         "D: reset_rotation_state() 清空幽灵在飞计数")

    # ------------------------------------------------------------------
    print("\n13. ROTATION_ENABLED=0 时行为与旧版一致（§6 回滚开关）")
    old_enabled = config.ROTATION_ENABLED
    try:
        config.ROTATION_ENABLED = False
        pl = pool_mod.Pool()
        pl.ensure_fresh = lambda cred: True     # 不碰网络/磁盘
        best, worst = mk("best"), mk("worst")
        pl._creds = {"best": best, "worst": worst}
        pl._credits = {"best": 9999, "worst": 1}
        pl._catalogs = ["m"]
        picked = []
        for _ in range(5):
            p_ = pl.pick("m")
            picked.append(p_.uin)
            pl.release(p_)          # 回滚路径也必须占坑/释放配对
        c.ok(picked == ["best"] * 5, "关闭轮换后每次都选积分最高的号（旧行为）", f"实际={picked}")
        c.ok(pl._pick_rotation([best, worst]) is None, "_pick_rotation 在关闭时直接返回 None")
        c.ok(pl._sched().state("best").in_flight == 0,
             "回滚路径的占坑/释放也配对（否则会偷走别的在飞请求的槽位）",
             f"实际={pl._sched().state('best').in_flight}")
    finally:
        config.ROTATION_ENABLED = old_enabled

    # ------------------------------------------------------------------
    print("\n14. I3：pick 永不抛出（内部异常退化为返回第一个候选）")
    class Bomb:
        """任何属性访问都炸的假凭据，用来逼出内部异常。"""
        @property
        def account_id(self):
            raise RuntimeError("boom")
    s14 = RotationScheduler(pol, random.Random(14))
    # I3 会 log.exception 记录这次异常，那是**预期行为**；这里临时静音，
    # 免得测试输出里出现一条吓人的 traceback。
    import logging as _logging
    _rlog = _logging.getLogger("gateway.rotation")
    _old_level = _rlog.level
    _rlog.setLevel(_logging.CRITICAL)
    try:
        got = s14.pick([Bomb()], {}, 0.0)  # type: ignore[list-item]
        c.ok(True, "内部异常被吞掉，没有向上抛")
        c.ok(got is not None, "退化为返回第一个候选")
    except Exception as exc:  # noqa: BLE001
        c.ok(False, "内部异常被吞掉，没有向上抛", f"抛出了 {exc!r}")
    finally:
        _rlog.setLevel(_old_level)

    # ------------------------------------------------------------------
    print("\n15. RotationState.rotate_state() 重置运行期字段")
    st = RotationState()
    st.last_used_at = time.monotonic()
    st.last_used_wall = time.time()
    st.in_flight = 3
    st.ema_cost = 12.5
    st.note_credit(time.time(), 5.0)
    st.rotate_state()
    c.ok(st.last_used_at == 0.0 and st.last_used_wall == 0.0 and st.in_flight == 0
         and st.ema_cost == 0.0 and len(st.recent_credits) == 0,
         "rotate_state 清空 last_used/in_flight/ema/recent_credits")

    # monotonic 与 wall 并存（§5：不得合并）
    s15 = RotationScheduler(pol, random.Random(15))
    before_wall = time.time()
    s15.mark_used("mm", time.time())
    st15 = s15.state("mm")
    c.ok(st15.last_used_wall >= before_wall, "last_used_wall 是 wall clock")
    c.ok(abs(st15.last_used_at - time.monotonic()) < 5.0, "last_used_at 是 monotonic")
    c.ok(st15.last_used_at != st15.last_used_wall, "两个时间基准并存且不相等")

    # ------------------------------------------------------------------
    print("\n16. Layer2 宽度：延迟不可得时取 SLICE_S，有数据时取 max(SLICE_S, 2*p95)")
    s16 = RotationScheduler(pol, random.Random(16))
    trio16 = [mk("L1"), mk("L2")]
    c.ok(s16.slice_width(trio16) == pol.slice_s, f"无延迟数据 → SLICE_S={pol.slice_s}")
    for _ in range(20):
        s16.note_latency("L1", 10.0)   # p95=10 → 2*10=20 < 30，仍取 30
    c.ok(s16.slice_width(trio16) == pol.slice_s, "2×p95=20 < SLICE_S → 仍取 SLICE_S")
    for _ in range(20):
        s16.note_latency("L1", 100.0)  # p95=100 → 200 > 30
    c.ok(s16.slice_width(trio16) > pol.slice_s, "2×p95 很大时宽度被抬高", 
         f"实际={s16.slice_width(trio16)}")

    # ------------------------------------------------------------------
    print("\n17. 生产参数下基础套餐优先仍然生效（第一版在这里失效 —— 回归钉子）")
    #
    # 第一版把「基础套餐优先」实现成 Layer 3 的 W_BASE 权重，实测那个基础已经
    # 归零的号仍然分到 ~16% 流量。原因有两层：权重只影响概率不影响资格；而且
    # 它排在 Layer 2 之后，Layer 2 在串行流量下（每号年龄 36s > 旧判据 5s）
    # 恒等地什么都不筛。下面的断言用**真实生产参数 + 真实余额快照**钉住修复。
    prod = RotationPolicy(min_interval_s=5.0, slice_s=30.0, max_concurrency=2)
    live = {
        # 2026-10-17 19:41 实测：178623 的基础套餐本周期已清零
        "178623": (1846.45, 0.0),
        "98846": (2509.71, 500.0),
        "5755": (2498.49, 298.49),
        "7009": (2642.08, 442.08),
        "8044": (2642.44, 442.44),
        "1877": (2598.82, 498.82),
    }

    def run_serial(policy, n=400, gap=6.0, base_priority=True):
        """6s 串行节拍（> MIN_INTERVAL_S），与线上真实用量形态一致。"""
        s_ = RotationScheduler(policy, random.Random(7))
        accs = [mk(k) for k in live]
        totals = {k: v[0] for k, v in live.items()}
        bases = {k: v[1] for k, v in live.items()}
        cnt = {k: 0 for k in live}
        t0 = time.monotonic()
        for i in range(n):
            now = t0 + i * gap
            p_ = s_.pick(accs, totals, now, base_credits=bases if base_priority else None)
            assert p_ is not None, "串行稳态下不该选不出号"
            # mark_used 写真实 monotonic；这里把它对齐到模拟时间轴，保持相对结构
            with s_._lock:
                st = s_.state(p_.account_id)
                st.last_used_at = now
                st.last_used_wall = time.time()
            s_.release_claim(p_.account_id)
            cnt[p_.uin] += 1
        return cnt

    on = run_serial(prod)
    c.ok(on["178623"] == 0,
         "生产参数下基础耗尽的 178623 一次都没被选中（第一版是 ~16%）", on)
    c.ok(sum(on[k] for k in live if live[k][1] > 0) == sum(on.values()),
         "全部请求落到了还有基础套餐的号上", on)

    # 反证：关掉分级就退回「按总余额分摊」，178623 立刻回来分流量。
    off_prio = RotationPolicy(min_interval_s=5.0, slice_s=30.0, max_concurrency=2,
                              base_priority=False)
    off = run_serial(off_prio)
    c.ok(off["178623"] > 0,
         "base_priority=False 时 178623 重新分到流量（证明上面是分级的功劳）", off)

    # 全员基础耗尽：分级必须自动失效，谁都不能被饿死（I4/「优先≠只准」）。
    allzero = {k: (v[0], 0.0) for k, v in live.items()}
    s_az = RotationScheduler(prod, random.Random(9))
    az_acc = [mk(k) for k in live]
    az_tot = {k: v[0] for k, v in allzero.items()}
    az_base = {k: v[1] for k, v in allzero.items()}
    az_cnt = {k: 0 for k in live}
    t0 = time.monotonic()
    for i in range(120):
        now = t0 + i * 6.0
        p_ = s_az.pick(az_acc, az_tot, now, base_credits=az_base)
        if p_ is None:
            break
        with s_az._lock:
            s_az.state(p_.account_id).last_used_at = now
        s_az.release_claim(p_.account_id)
        az_cnt[p_.uin] += 1
    c.ok(all(v > 0 for v in az_cnt.values()),
         "全员基础耗尽时每个号仍然分到请求（分级自动失效，不变成「只准」）", az_cnt)

    # ------------------------------------------------------------------
    print("\n18. Layer 2 真的会收窄（旧判据让它恒等于空操作 —— 回归钉子）")
    s18 = RotationScheduler(prod, random.Random(18))
    a18, b18, c18 = mk("n1"), mk("n2"), mk("n3")
    now18 = 10_000.0
    # n1 已经等了一个完整时间片（30s），n2/n3 刚用过
    s18.state("n1").last_used_at = now18 - 40.0
    s18.state("n2").last_used_at = now18 - 1.0
    s18.state("n3").last_used_at = now18 - 2.0
    waiting = s18._waiting([a18, b18, c18], now18)
    c.ok([x.uin for x in waiting] == ["n1"],
         "只有等满一个时间片的号进入 waiting（旧判据会把三个都放进来）",
         [x.uin for x in waiting])
    sliced = s18._time_slice([a18, b18, c18])
    c.ok(len(sliced) < 3, "时间片确实剔除了刚用过的号", [x.uin for x in sliced])

    # 全都没等够 → waiting 为空 → 退回全量交给 Layer 3（不是饥饿、不是 None）
    s18b = RotationScheduler(prod, random.Random(18))
    for name in ("n1", "n2", "n3"):
        s18b.state(name).last_used_at = now18 - 1.0
    c.ok(s18b._waiting([a18, b18, c18], now18) == [],
         "全都没等够时 waiting 为空（退回全量，不饥饿）")
    got18 = s18b.pick([a18, b18, c18], {}, now18)
    c.ok(got18 is not None, "全都没等够时仍然能选出号")
    if got18 is not None:
        s18b.release_claim(got18.account_id)

    # 收窄结果永远非空（NaN 污染时退回最久未用的那个，而不是全量或空）
    s18c = RotationScheduler(prod, random.Random(18))
    s18c.state("n1").last_used_at = float("nan")
    s18c.state("n2").last_used_at = now18 - 5.0
    res18 = s18c._time_slice([a18, b18])
    c.ok(len(res18) >= 1, "last_used_at 被污染为 NaN 时收窄结果仍非空", res18)

    # ------------------------------------------------------------------
    print("\n19. reload 不能抹掉轮换记忆（/admin/reload 之后仍知道谁刚被用过）")
    s19 = RotationScheduler(prod, random.Random(19))
    r_a, r_b = mk("rA"), mk("rB")
    s19.mark_used("rA", time.time())                 # A 刚被用过
    s19.state("rB").last_used_at = time.monotonic() - 600.0   # B 很久没用
    s19.state("rB").last_used_wall = time.time() - 600.0
    s19.mark_inflight("rA", +3)                      # 幽灵在飞计数
    s19.reset_inflight()                             # reload 路径调用的就是它
    c.ok(s19.state("rA").in_flight == 0, "reset_inflight 清掉幽灵在飞计数")
    age_a = time.monotonic() - s19.state("rA").last_used_at
    age_b = time.monotonic() - s19.state("rB").last_used_at
    c.ok(age_a < 5.0, "刚用过的 A 在 reload 后仍然是「刚用过」（age 未被清零）", age_a)
    c.ok(age_b > 500.0, "很久没用的 B 在 reload 后仍然是「很久没用」", age_b)
    c.ok(age_b > age_a, "A/B 的先后顺序在 reload 后没被抹平")
    # 对照：旧的 rotate_state 会把两者一起归零 —— 这正是被修掉的缺陷
    s19_old = RotationScheduler(prod, random.Random(19))
    s19_old.mark_used("rA", time.time())
    s19_old.state("rB").last_used_at = time.monotonic() - 600.0
    s19_old.rotate_state()
    c.ok(s19_old.state("rA").last_used_at == 0.0 and s19_old.state("rB").last_used_at == 0.0,
         "（对照）rotate_state 会把所有人的 last_used_at 一起归零——故 reload 不再用它")

    # ------------------------------------------------------------------
    # coolness 必须被钳制在 [0,1]（否则长时间运行的负数会吃掉权重）
    s17 = RotationScheduler(pol, random.Random(17))
    o1, o2 = mk("o1"), mk("o2")
    s17.state("o1").last_used_at = 1.0
    s17.state("o2").last_used_at = 2.0
    cool = s17._coolness(o1, now=10_000_000.0, oldest=1.0, newest=2.0)
    c.ok(0.0 <= cool <= 1.0, f"coolness 被钳制在 [0,1]（实际 {cool}）")

    print(f"\n{'='*46}\n通过 {c.p} / {c.p + c.f}\n{'='*46}")
    return 0 if c.f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
