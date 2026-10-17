"""用量统计测试（离线，不需要网关）。

锁死这几件事：
  1. 落盘后重启不丢 —— 这是「用量统计」能成立的前提；只有内存记录时它是假的。
  2. 时间窗口口径正确（今天 / 7 天 / 30 天 / 全部）。
  3. 日期序列连续补零 —— 断档的柱状图会把趋势画错。
  4. 超过保留期的旧记录被清掉且文件被重写（否则文件无限增长）。
  5. 脏数据不致命：坏行跳过，不影响其它记录。
  6. 落盘可整体关闭（GATEWAY_USAGE_LOG=0），且不留痕迹。

用法：python3 tests/test_usage.py
"""
from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway import activity as A  # noqa: E402


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


def line(ts: float, model="m", account="a", **kw) -> str:
    rec = {"ts": ts, "seq": 1, "model": model, "account": account,
           "upstream": "workbuddy", "stream": False, "latency_ms": 100,
           "total_ms": 100, "credit": 0, "tokens": 0, "ok": True, "error": ""}
    rec.update(kw)
    return json.dumps(rec, ensure_ascii=False) + "\n"


def main() -> int:
    c = Check()
    tmp = Path(tempfile.mkdtemp(prefix="wbusage-"))
    path = tmp / "usage.jsonl"
    now = time.time()

    # ---------------------------------------------------------------- 空状态
    print("\n[1] 空状态不崩")
    a = A.Activity(path=path)
    u = a.usage()
    c.ok(u["totals"]["all"]["requests"] == 0, "无记录时请求数为 0")
    c.ok(u["models"] == [] and u["accounts"] == [], "无记录时明细为空")
    c.ok(len(u["days"]) == 30, "日期序列默认 30 天", f"实际 {len(u['days'])}")
    c.ok(all(d["requests"] == 0 for d in u["days"]), "空状态日期序列全为 0")

    # ---------------------------------------------------------------- 基本聚合
    print("\n[2] 聚合正确")
    a.record(model="deepseek-v4-pro", account="u1", credit=0.5, tokens=100, total_ms=200)
    a.record(model="deepseek-v4-pro", account="u1", credit=0.25, tokens=50, total_ms=400)
    a.record(model="glm-5.3-flash", account="u2", credit=0, tokens=10,
             total_ms=100, ok=False, error="boom")
    u = a.usage()
    t = u["totals"]
    c.ok(t["today"]["requests"] == 3, "今天 3 次", t["today"])
    c.ok(abs(t["today"]["credits"] - 0.75) < 1e-9, "今天消耗 0.75", t["today"]["credits"])
    c.ok(t["today"]["tokens"] == 160, "今天 tokens 160", t["today"]["tokens"])
    c.ok(t["today"]["errors"] == 1, "今天 1 次失败")
    # error_rate 落盘时按 4 位小数取整，比较要留出这个精度
    c.ok(abs(t["today"]["error_rate"] - 1 / 3) < 1e-4, "失败率 1/3",
         t["today"]["error_rate"])
    c.ok(t["today"]["avg_ms"] == round(700 / 3), "平均耗时 = 总和/次数", t["today"]["avg_ms"])
    c.ok(t["today"]["max_ms"] == 400, "最大耗时 400")
    c.ok(t["all"]["requests"] == 3 and t["d7"]["requests"] == 3, "7 天/全部窗口一致")

    # ---------------------------------------------------------------- 明细排序
    print("\n[3] 明细按消耗降序")
    c.ok([m["model"] for m in u["models"]] == ["deepseek-v4-pro", "glm-5.3-flash"],
         "模型按积分降序", [m["model"] for m in u["models"]])
    c.ok(u["accounts"][0]["account"] == "u1", "账号按积分降序")

    # ---------------------------------------------------------------- 落盘 & 重载
    print("\n[4] 重启不丢（这是整个功能的前提）")
    c.ok(path.exists(), "记录文件已创建")
    mode = stat.S_IMODE(path.stat().st_mode)
    c.ok(mode == 0o600, "文件权限 0600", oct(mode))
    a2 = A.Activity(path=path)
    u2 = a2.usage()
    c.ok(u2["totals"]["all"]["requests"] == 3, "重载后仍为 3 次", u2["totals"]["all"])
    c.ok(abs(u2["totals"]["all"]["credits"] - 0.75) < 1e-9, "重载后积分不丢")
    c.ok(u2["loaded_records"] == 3, "载入条数 3")
    c.ok(len(a2.recent(10)) == 3, "最近请求列表也恢复了")

    # ---------------------------------------------------------------- 跨天
    print("\n[5] 跨天分桶 + 日期补零")
    p2 = tmp / "跨天.jsonl"
    p2.write_text(
        line(now - 3 * 86400, model="old", credit=1, tokens=5) +
        line(now - 86400, model="yest", credit=2, tokens=5) +
        line(now, model="today", credit=4, tokens=5),
        encoding="utf-8")
    a3 = A.Activity(path=p2)
    u3 = a3.usage(days=5)
    c.ok(len(u3["days"]) == 5, "要求 5 天就返回 5 天", len(u3["days"]))
    by = {d["date"]: d["credits"] for d in u3["days"]}
    c.ok(by.get(time.strftime("%Y-%m-%d")) == 4, "今天的桶是 4")
    c.ok(sum(by.values()) == 7, "5 天内合计 7", sum(by.values()))
    c.ok(u3["totals"]["d7"]["requests"] == 3, "7 天窗口含全部 3 条")
    c.ok(u3["totals"]["today"]["requests"] == 1, "今天窗口只含 1 条")
    # 第 1 天（5 天窗口最左）应该是 0，证明补零生效
    c.ok(u3["days"][0]["requests"] == 0, "无请求的日子补 0 而不是跳过")

    # ---------------------------------------------------------------- 保留期
    print("\n[6] 过期记录被清理")
    p3 = tmp / "过期.jsonl"
    p3.write_text(
        line(now - (A.RETENTION_DAYS + 5) * 86400, model="ancient", credit=99) +
        line(now, model="fresh", credit=1),
        encoding="utf-8")
    a4 = A.Activity(path=p3)
    u4 = a4.usage()
    c.ok(u4["totals"]["all"]["requests"] == 1, "过期记录未计入", u4["totals"]["all"])
    c.ok(u4["totals"]["all"]["credits"] == 1, "过期记录的积分未计入")
    kept = [json.loads(x) for x in p3.read_text(encoding="utf-8").splitlines() if x.strip()]
    c.ok(len(kept) == 1 and kept[0]["model"] == "fresh",
         "文件已被重写，只剩未过期的", [k.get("model") for k in kept])

    # ---------------------------------------------------------------- 脏数据
    print("\n[7] 脏数据不致命")
    p4 = tmp / "脏.jsonl"
    p4.write_text(
        line(now, model="good") +
        "{ 这不是 json\n" +
        "\n" +
        json.dumps({"no_ts": True}) + "\n" +
        line(now, model="good2"),
        encoding="utf-8")
    a5 = A.Activity(path=p4)
    c.ok(a5.usage()["totals"]["all"]["requests"] == 2, "坏行被跳过，好行保留",
         a5.usage()["totals"]["all"])
    c.ok(len(a5.recent(10)) == 2, "最近请求列表只含好行")

    # ---------------------------------------------------------------- 开关
    print("\n[8] 落盘可关闭")
    os.environ["GATEWAY_USAGE_LOG"] = "0"
    try:
        off = A.Activity()
        c.ok(off.usage()["path"] is None, "GATEWAY_USAGE_LOG=0 时不落盘")
        off.record(model="x", account="y", credit=1)
        c.ok(off.usage()["totals"]["all"]["requests"] == 1, "关闭落盘后内存统计仍工作")
        c.ok(off.usage()["persisted"] is False, "persisted 标记为 False")
    finally:
        os.environ.pop("GATEWAY_USAGE_LOG", None)

    # ---------------------------------------------------------------- 写失败
    print("\n[9] 写不进去不影响统计本身")
    bad = A.Activity(path=Path("/proc/definitely/not/writable/usage.jsonl"))
    bad.record(model="m", account="a", credit=0.1)
    c.ok(bad.usage()["totals"]["all"]["requests"] == 1, "磁盘不可写时内存统计照常")
    c.ok(bad.usage()["persisted"] is False, "并诚实地把 persisted 标成 False")

    # ---------------------------------------------------------------- 测试流量
    print("\n[10] 测试流量不计入真实用量")
    p5 = tmp / "测试.jsonl"
    a6 = A.Activity(path=p5)
    a6.record(model="real", account="u", credit=1, tokens=10)
    A.mark_test(True)
    try:
        a6.record(model="definitely-not-a-model", account="u", ok=False, error="注入的失败")
    finally:
        A.mark_test(False)
    a6.record(model="real2", account="u", credit=1, tokens=10)
    u6 = a6.usage()
    c.ok(u6["totals"]["all"]["requests"] == 2, "测试流量不计入请求数", u6["totals"]["all"])
    c.ok(u6["totals"]["all"]["errors"] == 0, "测试注入的失败不拉高失败率")
    c.ok(u6["test_requests"] == 1, "单独记了 1 条测试流量", u6["test_requests"])
    c.ok(all(m["model"] != "definitely-not-a-model" for m in u6["models"]),
         "假模型不出现在模型明细", [m["model"] for m in u6["models"]])
    c.ok(any(r.get("test") for r in a6.recent(10)), "最近请求列表仍能看到它（带标记）")
    c.ok(A.Activity(path=p5).usage()["test_requests"] == 1, "标记也持久化，重启后仍排除")

    # ---------------------------------------------------------------- 分位数
    print("\n[11] 延迟分位数（面板的分布条依赖它）")
    from gateway.activity import histogram_percentile, _bucket_index, BUCKET_COUNT

    # 直方图估算是**保守**的：返回桶上界，所以只会偏大、不会偏小。
    # 这条性质很重要 —— 把慢报成快，比把快报成慢危险得多。
    h = [0] * BUCKET_COUNT
    for ms in [800, 900, 1000, 1200, 1500, 2000, 2500, 3000, 3500, 4000]:
        h[_bucket_index(ms)] += 1
    p50, p90 = histogram_percentile(h, 0.50), histogram_percentile(h, 0.90)
    real = sorted([800, 900, 1000, 1200, 1500, 2000, 2500, 3000, 3500, 4000])
    c.ok(p50 >= real[4] and p50 <= real[5] * 1.6, f"p50 落在真实区间附近", f"p50={p50}")
    c.ok(p90 >= real[8], "p90 不小于真实 p90（保守）", f"p90={p90} 真实={real[8]}")
    c.ok(histogram_percentile([0] * BUCKET_COUNT, 0.9) == 0, "空直方图返回 0 而不是崩")

    # 端到端：极端值不该被稀释掉
    p6 = tmp / "分位.jsonl"
    a7 = A.Activity(path=p6)
    for ms in [800, 900, 1000, 1200, 1500, 2000, 2500, 3000, 3500, 4000]:
        a7.record(model="fast", account="u1", total_ms=ms, credit=0.1)
    for ms in [20000, 60000, 143540]:
        a7.record(model="slow", account="u2", total_ms=ms, credit=0.1)
    u7 = a7.usage(days=7)
    rows = {m["model"]: m for m in u7["models"]}
    c.ok(rows["fast"]["p50_ms"] <= 2000, "快模型的中位数在 2s 内", rows["fast"]["p50_ms"])
    c.ok(rows["slow"]["p99_ms"] >= 120000,
         "慢模型的 p99 把 143s 的长尾暴露出来", rows["slow"]["p99_ms"])
    c.ok(rows["fast"]["p99_ms"] < rows["slow"]["p50_ms"],
         "两个模型的分位数确实拉开（分布条画得出区别）")
    # 143540 若退化成 900000 就是失真，这里卡住上界
    c.ok(rows["slow"]["p99_ms"] <= 300000,
         "长尾不会因为桶太粗而虚报到 15 分钟", rows["slow"]["p99_ms"])

    print("\n[12] 分位数 + 逐日序列跨重启不丢")
    u8 = A.Activity(path=p6).usage(days=7)
    r8 = {m["model"]: m for m in u8["models"]}
    c.ok(r8["fast"]["p50_ms"] == rows["fast"]["p50_ms"], "重载后 p50 一致")
    c.ok(r8["slow"]["p99_ms"] == rows["slow"]["p99_ms"], "重载后 p99 一致")

    # 逐日序列：前端直接把 daily 与 days 配对画趋势线，长度必须严格对齐
    print("\n[13] 行内趋势线的逐日序列")
    c.ok(all(len(m["daily"]) == len(u7["days"]) for m in u7["models"]),
         "每个模型的 daily 长度 == days 长度")
    c.ok(all(len(m["daily"]) == len(u7["days"]) for m in u7["accounts"]),
         "每个账号的 daily 长度 == days 长度")
    fast_daily = rows["fast"]["daily"]
    c.ok(sum(fast_daily) == rows["fast"]["requests"],
         "daily 求和等于该模型的请求总数", f"{sum(fast_daily)} vs {rows['fast']['requests']}")
    c.ok(fast_daily[-1] == 10, "今天的桶是 10", fast_daily[-1])
    c.ok(all(v == 0 for v in fast_daily[:-1]), "更早的日子补 0（不跳过、不错位）")

    # 测试流量不该污染趋势线
    print("\n[17] 重启后 seq 不重复（否则前端列表 key 撞车）")
    p9 = tmp / "序号.jsonl"
    a11 = A.Activity(path=p9)
    a11.record(model="m", account="u", total_ms=1)
    a11.record(model="m", account="u", total_ms=1)
    first = [i["seq"] for i in a11.recent(10)]
    # recent() 是**倒序**（最新在前），所以这里是 [2, 1]
    c.ok(sorted(first) == [1, 2], "首次运行 seq 从 1 递增", first)
    # 关键：模拟进程重启（新建 Activity 读同一个文件），新记录必须接续而不是从 1 重来
    a12 = A.Activity(path=p9)
    a12.record(model="m", account="u", total_ms=1)
    seqs = [i["seq"] for i in a12.recent(10)]
    c.ok(len(seqs) == len(set(seqs)), "重启后 seq 全局唯一", seqs)
    c.ok(max(seqs) == 3, "新记录接续历史最大 seq（不是从 1 重来）", seqs)

    print("\n[15] 每日模型明细带 tokens（折线图 + hover 明细）")
    p8 = tmp / "明细.jsonl"
    a9 = A.Activity(path=p8)
    # 同一天里两个模型的 tokens 差距要能体现"谁吃得多"
    a9.record(model="big", account="u1", total_ms=100, credit=0, tokens=1000)
    a9.record(model="big", account="u1", total_ms=100, credit=0, tokens=500)
    a9.record(model="small", account="u1", total_ms=100, credit=0, tokens=7)
    u10 = a9.usage(days=3)
    day = u10["days"][-1]
    byname = {m["model"]: m for m in day["models"]}
    c.ok(set(byname) == {"big", "small"}, "当天明细含两个模型", sorted(byname))
    c.ok(byname["big"]["tokens"] == 1500, "big 的 tokens 累加正确", byname["big"]["tokens"])
    c.ok(byname["small"]["tokens"] == 7, "small 的 tokens 正确", byname["small"]["tokens"])
    c.ok(byname["big"]["requests"] == 2, "big 的请求数仍正确", byname["big"]["requests"])
    # 折线画的是当日总 token，明细必须能加总还原它 —— 对不上就说明口径裂了
    c.ok(sum(m["tokens"] for m in day["models"]) == day["tokens"],
         "明细 tokens 之和 == 当日总 tokens",
         f"{sum(m['tokens'] for m in day['models'])} vs {day['tokens']}")
    c.ok(sum(m["requests"] for m in day["models"]) == day["requests"],
         "明细请求数之和 == 当日总请求数")
    # 按 tokens 降序：hover 面板最关心"谁最耗 token"
    c.ok([m["model"] for m in day["models"]] == ["big", "small"],
         "明细按 tokens 降序", [m["model"] for m in day["models"]])

    print("\n[16] 明细同样排除测试流量 + 跨重启不丢")
    a10 = A.Activity(path=p8)
    A.mark_test(True)
    try:
        a10.record(model="big", account="u1", total_ms=100, credit=0, tokens=99999)
    finally:
        A.mark_test(False)
    u11 = a10.usage(days=3)
    d2 = u11["days"][-1]
    c.ok(all(m["tokens"] == 0 or m["model"] != "big" or m["tokens"] == 1500 for m in d2["models"]),
         "测试流量的 tokens 不进明细",
         {m["model"]: m["tokens"] for m in d2["models"]})
    reloaded = A.Activity(path=p8).usage(days=3)
    c.ok(reloaded["days"][-1]["models"][0]["tokens"] == 1500,
         "重载后明细 tokens 一致", reloaded["days"][-1]["models"][0]["tokens"])

    print("\n[14] 趋势线同样排除测试流量")
    p7 = tmp / "趋势.jsonl"
    a8 = A.Activity(path=p7)
    a8.record(model="m1", account="u1", total_ms=100, credit=0)
    A.mark_test(True)
    try:
        a8.record(model="m1", account="u1", total_ms=100, credit=0)
    finally:
        A.mark_test(False)
    u9 = a8.usage(days=3)
    m1 = [m for m in u9["models"] if m["model"] == "m1"][0]
    c.ok(sum(m1["daily"]) == 1, "测试流量不进趋势线", sum(m1["daily"]))

    print(f"\n通过 {c.p} / {c.p + c.f}")
    return 1 if c.f else 0


if __name__ == "__main__":
    raise SystemExit(main())
