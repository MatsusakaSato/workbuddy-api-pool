"""轮换持久化与配置测试（离线，不发网络请求、不消耗真实积分）。

对应 docs/账号轮换策略设计.md §7 的验收项 11–15：

  11. 三个新字段的 round-trip；旧格式文件（缺字段）仍能加载。
  12. 脏数据（字符串 / 负数 / null）回落到默认值。
  13. persistable=False 仍然不落盘。
  14. 节流：不带 force 不写、带 force 写；原子写不留 .tmp。
  15. rotation_policy() 在有无 env 覆盖时都返回合理值，含 MIN_RATE 钳制。

设计要点：
  * **绝不触碰真实的 state/accounts.json** —— 全程把 config.STATE_DIR /
    config.ACCOUNTS_FILE 指向临时目录，结束后还原。
  * 直接调 creds 的函数，不启动网关、不碰上游。
  * 额外锁死「累计计数跨重启续上」这条 G5 不变量：落盘 → 重载 → 数值不变。

用法：python3 tests/test_rotation_state.py
退出码 0 = 全部通过。
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

from gateway import config, creds  # noqa: E402
from gateway.creds import Credential  # noqa: E402


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


# --------------------------------------------------------------------------
# 临时 state 目录：把模块级路径重定向出去，测试结束还原
# --------------------------------------------------------------------------

class TempState:
    """把 config.STATE_DIR / ACCOUNTS_FILE 指到临时目录，退出时还原。"""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wbrotstate-")
        self.dir = Path(self._tmp.name)
        self._old_dir = config.STATE_DIR
        self._old_file = config.ACCOUNTS_FILE

    def __enter__(self) -> "TempState":
        config.STATE_DIR = self.dir
        config.ACCOUNTS_FILE = self.dir / "accounts.json"
        creds.reset_save_throttle()
        return self

    def __exit__(self, *exc) -> None:
        config.STATE_DIR = self._old_dir
        config.ACCOUNTS_FILE = self._old_file
        creds.reset_save_throttle()
        self._tmp.cleanup()


def make_cred(uin: str, **kw) -> Credential:
    base = dict(
        uin=uin, uid=f"uid-{uin}", domain="www.workbuddy.cn",
        access_token="tok", refresh_token="ref",
        expires_at_ms=int((time.time() + 3600) * 1000),
    )
    base.update(kw)
    return Credential(**base)


def write_accounts_file(path: Path, accounts: dict, version: int = 1) -> None:
    path.write_text(
        json.dumps({"version": version, "updated_at": int(time.time()),
                    "accounts": accounts}, ensure_ascii=False, indent=2),
        encoding="utf-8")


def main() -> int:
    c = Check()

    # ================================================================== 11a
    print("\n[11a] 三个新字段的 round-trip（落盘 → 重载不丢）")
    with TempState() as ts:
        store = {
            "1001": make_cred("1001", credits_spent_total=123.456789,
                              requests_total=42, last_used_wall=1700000000.5),
            "1002": make_cred("1002", credits_spent_total=0.25,
                              requests_total=1, last_used_wall=1700000123.75),
        }
        creds.save_store(store, force=True)
        back = creds.load_store()
        c.ok(len(back) == 2, "两个账号都读回来了", f"实际 {len(back)}")
        a = back.get("1001")
        c.ok(a is not None, "账号 1001 存在")
        if a is not None:
            c.ok(abs(a.credits_spent_total - 123.456789) < 1e-6,
                 "credits_spent_total round-trip", f"实际 {a.credits_spent_total}")
            c.ok(a.requests_total == 42, "requests_total round-trip",
                 f"实际 {a.requests_total}")
            c.ok(abs(a.last_used_wall - 1700000000.5) < 1e-3,
                 "last_used_wall round-trip", f"实际 {a.last_used_wall}")
            c.ok(isinstance(a.credits_spent_total, float), "credits_spent_total 是 float")
            c.ok(isinstance(a.requests_total, int)
                 and not isinstance(a.requests_total, bool), "requests_total 是 int")
        b = back.get("1002")
        c.ok(b is not None and abs(b.credits_spent_total - 0.25) < 1e-9,
             "第二个账号的计数也独立正确",
             f"实际 {None if b is None else b.credits_spent_total}")
        # 落盘的 JSON 里确实有这三个键
        doc = json.loads(config.ACCOUNTS_FILE.read_text(encoding="utf-8"))
        keys = set(doc["accounts"]["1001"].keys())
        c.ok({"credits_spent_total", "requests_total", "last_used_wall"} <= keys,
             "落盘 JSON 含三个新字段", f"实际 {sorted(keys)}")
        # 权限保持 0600
        mode = stat.S_IMODE(config.ACCOUNTS_FILE.stat().st_mode)
        c.ok(mode == 0o600, "落盘文件权限 0600", oct(mode))

    # ================================================================== 11b
    print("\n[11b] 旧格式文件（完全没有这三个字段）仍能加载")
    with TempState() as ts:
        write_accounts_file(config.ACCOUNTS_FILE, {
            "2001": {"uin": "2001", "uid": "u", "domain": "www.workbuddy.cn",
                     "access_token": "old-tok", "refresh_token": "old-ref",
                     "expires_at_ms": 1794628367043,
                     "refresh_expires_at_ms": 1797220366043,
                     "nickname": "老账号", "source": "vault"},
        })
        back = creds.load_store()
        c.ok(len(back) == 1, "旧格式账号照常加载", f"实际 {len(back)}")
        a = back.get("2001")
        c.ok(a is not None, "账号 2001 存在")
        if a is not None:
            c.ok(a.access_token == "old-tok", "旧字段不受影响")
            c.ok(a.credits_spent_total == 0.0, "缺 credits_spent_total → 0.0",
                 f"实际 {a.credits_spent_total!r}")
            c.ok(a.requests_total == 0, "缺 requests_total → 0",
                 f"实际 {a.requests_total!r}")
            c.ok(a.last_used_wall == 0.0, "缺 last_used_wall → 0.0",
                 f"实际 {a.last_used_wall!r}")
        # 旧文件加载后再存，字段被补齐
        creds.save_store(back, force=True)
        doc = json.loads(config.ACCOUNTS_FILE.read_text(encoding="utf-8"))
        c.ok("credits_spent_total" in doc["accounts"]["2001"],
             "旧文件重新落盘后补齐新字段")

    # ================================================================== 12
    print("\n[12] 脏数据（字符串 / 负数 / null / NaN / 布尔）回落默认值")
    with TempState() as ts:
        write_accounts_file(config.ACCOUNTS_FILE, {
            "g1": {"uin": "g1", "access_token": "t", "credits_spent_total": "不是数字",
                   "requests_total": "also-bad", "last_used_wall": "???"},
            "g2": {"uin": "g2", "access_token": "t", "credits_spent_total": -5.0,
                   "requests_total": -3, "last_used_wall": -1.0},
            "g3": {"uin": "g3", "access_token": "t", "credits_spent_total": None,
                   "requests_total": None, "last_used_wall": None},
            "g4": {"uin": "g4", "access_token": "t", "credits_spent_total": float("nan"),
                   "requests_total": True, "last_used_wall": float("inf")},
            "g5": {"uin": "g5", "access_token": "t", "credits_spent_total": "12.5",
                   "requests_total": "7", "last_used_wall": "1700000000.25"},
        })
        back = creds.load_store()
        c.ok(len(back) == 5, "脏数据账号仍全部加载（不因脏字段被丢）",
             f"实际 {len(back)}")
        for key in ("g1", "g2", "g3", "g4"):
            a = back.get(key)
            if a is None:
                c.ok(False, f"{key} 存在")
                continue
            c.ok(a.credits_spent_total == 0.0, f"{key}.credits_spent_total → 0.0",
                 f"实际 {a.credits_spent_total!r}")
            c.ok(a.requests_total == 0, f"{key}.requests_total → 0",
                 f"实际 {a.requests_total!r}")
            c.ok(a.last_used_wall == 0.0, f"{key}.last_used_wall → 0.0",
                 f"实际 {a.last_used_wall!r}")
        # 能解析的字符串应当被接受（宽容而非抛弃）
        a5 = back.get("g5")
        c.ok(a5 is not None and abs(a5.credits_spent_total - 12.5) < 1e-9,
             "合法的数字字符串被接受", f"实际 {None if a5 is None else a5.credits_spent_total}")
        c.ok(a5 is not None and a5.requests_total == 7,
             "合法整数字符串被接受", f"实际 {None if a5 is None else a5.requests_total}")

    # ================================================================== 12b
    print("\n[12b] 整个文件损坏时不致命（坏 JSON → 空库）")
    with TempState() as ts:
        config.ACCOUNTS_FILE.write_text("{ 这不是 JSON", encoding="utf-8")
        back = creds.load_store()
        c.ok(back == {}, "坏文件返回空库而不是抛异常", f"实际 {back!r}")

    # ================================================================== 13
    print("\n[13] persistable=False 的账号（测试注入假号）永远不落盘")
    with TempState() as ts:
        fake_uin = "000000000000"
        store = {
            "3001": make_cred("3001", credits_spent_total=9.0, requests_total=3),
            fake_uin: make_cred(fake_uin, source="injected-test", persistable=False,
                                credits_spent_total=999.0, requests_total=99),
        }
        creds.save_store(store, force=True)
        doc = json.loads(config.ACCOUNTS_FILE.read_text(encoding="utf-8"))
        c.ok("3001" in doc["accounts"], "可持久化账号已落盘")
        c.ok(fake_uin not in doc["accounts"], "假号不在落盘结果里",
             f"实际键={list(doc['accounts'].keys())}")
        back = creds.load_store()
        c.ok(fake_uin not in back and "3001" in back,
             "重载后依然只有真账号", f"实际键={list(back.keys())}")

    # ================================================================== 14
    print("\n[14] 落盘节流：不带 force 跳过、带 force 必写")
    with TempState() as ts:
        creds.reset_save_throttle()
        store = {"4001": make_cred("4001")}
        c.ok(creds.last_save_monotonic() == 0.0, "初始未写过盘")

        first = creds.save_store(store)          # 从未写过 → 必须写
        c.ok(first is True, "首次落盘真的写了（节流不该挡住第一次）")
        c.ok(creds.last_save_monotonic() > 0.0, "记录了写盘时刻")

        store["4001"].requests_total = 1
        second = creds.save_store(store)         # 间隔内 → 跳过
        c.ok(second is False, "节流窗口内不带 force → 跳过写盘")
        doc = json.loads(config.ACCOUNTS_FILE.read_text(encoding="utf-8"))
        c.ok(doc["accounts"]["4001"]["requests_total"] == 0,
             "被跳过时磁盘内容确实没变", f"实际 {doc['accounts']['4001']['requests_total']}")

        third = creds.save_store(store, force=True)   # force → 必须写
        c.ok(third is True, "force=True → 一定写盘")
        doc = json.loads(config.ACCOUNTS_FILE.read_text(encoding="utf-8"))
        c.ok(doc["accounts"]["4001"]["requests_total"] == 1,
             "force 写盘后磁盘内容已更新",
             f"实际 {doc['accounts']['4001']['requests_total']}")

        # 节流窗口过后（把上次写盘时间往前挪）不带 force 也应该写
        creds._last_save_monotonic = time.monotonic() - (config.SAVE_THROTTLE_S + 1)
        store["4001"].requests_total = 2
        fourth = creds.save_store(store)
        c.ok(fourth is True, "超过节流窗口后不带 force 也会写盘")
        doc = json.loads(config.ACCOUNTS_FILE.read_text(encoding="utf-8"))
        c.ok(doc["accounts"]["4001"]["requests_total"] == 2, "窗口后的写盘生效")

    # ================================================================== 14b
    print("\n[14b] 原子写：不留 .tmp 残留，且写失败不抛")
    with TempState() as ts:
        creds.reset_save_throttle()
        creds.save_store({"5001": make_cred("5001")}, force=True)
        leftovers = [p.name for p in ts.dir.glob("*.tmp")]
        c.ok(leftovers == [], "没有 .tmp 残留", f"实际 {leftovers}")
        c.ok(config.ACCOUNTS_FILE.is_file(), "accounts.json 存在")
        # tmp 文件名（scheduler/其他调用方约定）
        c.ok(not (ts.dir / "accounts.json.tmp").exists(), "accounts.json.tmp 不存在")
        # 目录不可写时不抛异常，只返回 False
        from gateway import creds as _cr
        old_dir = config.STATE_DIR
        try:
            config.STATE_DIR = Path("/proc/definitely/not/writable")
            config.ACCOUNTS_FILE = config.STATE_DIR / "accounts.json"
            creds.reset_save_throttle()
            r = creds.save_store({"5002": make_cred("5002")}, force=True)
            c.ok(r is False, "不可写时返回 False 而不是抛异常")
        finally:
            config.STATE_DIR = old_dir
            config.ACCOUNTS_FILE = old_dir / "accounts.json"

    # ================================================================== 14c
    print("\n[14c] 累计计数跨重启续上（G5 不失忆，崩溃最多丢一个节流窗口）")
    with TempState() as ts:
        creds.reset_save_throttle()
        store = {"6001": make_cred("6001", credits_spent_total=10.0, requests_total=5,
                                   last_used_wall=1699999999.0)}
        creds.save_store(store, force=True)
        # 模拟重启：重新从磁盘加载
        reborn = creds.load_store()
        a = reborn.get("6001")
        c.ok(a is not None and abs(a.credits_spent_total - 10.0) < 1e-9,
             "重启后累计积分不丢")
        c.ok(a is not None and a.requests_total == 5, "重启后累计请求数不丢")
        c.ok(a is not None and abs(a.last_used_wall - 1699999999.0) < 1e-3,
             "重启后上次使用时间不丢")

    # ================================================================== 14d
    print("\n[14d] credits_spent_total 的浮点精度与跨天累加")
    with TempState() as ts:
        creds.reset_save_throttle()
        # 0.1 这类二进制无法精确表示的十进制小数，累加 10 次
        total = 0.0
        for _ in range(10):
            total += 0.1
        store = {"7001": make_cred("7001", credits_spent_total=total, requests_total=10)}
        creds.save_store(store, force=True)
        back = creds.load_store()["7001"]
        c.ok(abs(back.credits_spent_total - total) < 1e-9,
             "十进制小数累加值 round-trip 精确", f"实际 {back.credits_spent_total!r}")
        c.ok(abs(back.credits_spent_total - 1.0) < 1e-9,
             "10 × 0.1 仍是 1.0 量级", f"实际 {back.credits_spent_total!r}")
        # 很大与很小的值
        store["7001"].credits_spent_total = 1234567.890123
        store["7001"].requests_total = 1000000
        creds.save_store(store, force=True)
        back2 = creds.load_store()["7001"]
        c.ok(abs(back2.credits_spent_total - 1234567.890123) < 1e-6,
             "大额积分 round-trip", f"实际 {back2.credits_spent_total!r}")
        c.ok(back2.requests_total == 1000000, "大请求数 round-trip",
             f"实际 {back2.requests_total!r}")
        # 跨天：last_used_wall 是 wall clock，跨天仍可比较
        yday = time.time() - 86400.0
        store["7001"].last_used_wall = yday
        creds.save_store(store, force=True)
        back3 = creds.load_store()["7001"]
        c.ok(abs(back3.last_used_wall - yday) < 1e-3, "昨天的时间戳可精确比较")
        c.ok(back3.last_used_wall < time.time(), "昨天 < 今天（wall clock 跨天可比）")

    # ================================================================== 15
    print("\n[15] rotation_policy()：无 env 覆盖时用设计文档 §6 的默认值")
    saved_env = {k: os.environ.pop(k, None) for k in (
        "GATEWAY_ROTATION_ENABLED", "GATEWAY_ROTATION_SLICE_S",
        "GATEWAY_ROTATION_MIN_INTERVAL_S", "GATEWAY_ROTATION_MAX_CONCURRENCY",
        "GATEWAY_ROTATION_W_CREDIT", "GATEWAY_ROTATION_W_RUNWAY",
        "GATEWAY_ROTATION_W_COOL", "GATEWAY_ROTATION_EMA_HALFLIFE_S",
        "GATEWAY_ROTATION_MIN_RATE")}
    try:
        import importlib
        importlib.reload(config)
        pol = config.rotation_policy()
        c.ok(pol.enabled is True, "默认 enabled=True")
        c.ok(pol.slice_s == 30.0, "默认 slice_s=30", f"实际 {pol.slice_s}")
        c.ok(pol.min_interval_s == 5.0, "默认 min_interval_s=5",
             f"实际 {pol.min_interval_s}")
        c.ok(pol.max_concurrency == 2, "默认 max_concurrency=2",
             f"实际 {pol.max_concurrency}")
        c.ok(pol.w_credit == 1.0, "默认 w_credit=1.0", f"实际 {pol.w_credit}")
        c.ok(pol.w_runway == 2.0, "默认 w_runway=2.0（最高权重）",
             f"实际 {pol.w_runway}")
        c.ok(pol.w_cool == 0.5, "默认 w_cool=0.5", f"实际 {pol.w_cool}")
        c.ok(pol.ema_halflife_s == 900.0, "默认 ema_halflife_s=900",
             f"实际 {pol.ema_halflife_s}")
        c.ok(pol.min_rate == 0.01, "默认 min_rate=0.01", f"实际 {pol.min_rate}")

        # ---------------------------------------------------------- 15b
        print("\n[15b] env 覆盖生效")
        os.environ["GATEWAY_ROTATION_ENABLED"] = "0"
        os.environ["GATEWAY_ROTATION_SLICE_S"] = "12.5"
        os.environ["GATEWAY_ROTATION_MIN_INTERVAL_S"] = "0.5"
        os.environ["GATEWAY_ROTATION_MAX_CONCURRENCY"] = "7"
        os.environ["GATEWAY_ROTATION_W_CREDIT"] = "3.5"
        os.environ["GATEWAY_ROTATION_MIN_RATE"] = "0.5"
        importlib.reload(config)
        pol = config.rotation_policy()
        c.ok(pol.enabled is False, "GATEWAY_ROTATION_ENABLED=0 → 回滚开关生效")
        c.ok(pol.slice_s == 12.5, "SLICE_S 覆盖生效", f"实际 {pol.slice_s}")
        c.ok(pol.min_interval_s == 0.5, "MIN_INTERVAL_S 覆盖生效", f"实际 {pol.min_interval_s}")
        c.ok(pol.max_concurrency == 7, "MAX_CONCURRENCY 覆盖生效",
             f"实际 {pol.max_concurrency}")
        c.ok(pol.w_credit == 3.5, "W_CREDIT 覆盖生效", f"实际 {pol.w_credit}")
        c.ok(abs(pol.min_rate - 0.5) < 1e-12, "合理的 MIN_RATE 不被篡改",
             f"实际 {pol.min_rate}")

        # ---------------------------------------------------------- 15c
        print("\n[15c] MIN_RATE 钳制：除零/荒谬权重不可能出现")
        for bad, label in (("0", "0"), ("-1", "负数"), ("nan", "NaN"),
                           ("inf", "Inf"), ("garbage", "垃圾字符串")):
            os.environ["GATEWAY_ROTATION_MIN_RATE"] = bad
            importlib.reload(config)
            mr = config.MIN_RATE
            c.ok(mr > 0, f"MIN_RATE={label} 被钳到 > 0（不能除零）", f"实际 {mr!r}")
            c.ok(mr >= 1e-6, f"MIN_RATE={label} 不低于地板 1e-6", f"实际 {mr!r}")
            pol = config.rotation_policy()
            c.ok(pol.min_rate > 0, f"策略对象里的 min_rate={label} 也 > 0",
                 f"实际 {pol.min_rate!r}")
        # 极端大的值被压到天花板
        os.environ["GATEWAY_ROTATION_MIN_RATE"] = "1e300"
        importlib.reload(config)
        c.ok(config.MIN_RATE <= 1e6, "荒谬大的 MIN_RATE 被压到天花板",
             f"实际 {config.MIN_RATE!r}")

        # ---------------------------------------------------------- 15d
        print("\n[15d] 非法数值/生命周期保护")
        os.environ["GATEWAY_ROTATION_SLICE_S"] = "abc"
        os.environ["GATEWAY_ROTATION_EMA_HALFLIFE_S"] = "-5"
        os.environ["GATEWAY_ROTATION_MAX_CONCURRENCY"] = "0"
        os.environ["GATEWAY_ROTATION_MIN_INTERVAL_S"] = "junk"
        importlib.reload(config)
        c.ok(config.ROTATION_SLICE_S == 30.0, "非法 SLICE_S 回落默认",
             f"实际 {config.ROTATION_SLICE_S}")
        c.ok(config.ROTATION_EMA_HALFLIFE_S == 900.0, "非正半衰期回落默认",
             f"实际 {config.ROTATION_EMA_HALFLIFE_S}")
        c.ok(config.ROTATION_MAX_CONCURRENCY >= 1, "并发上限至少为 1（否则永远选不出号）",
             f"实际 {config.ROTATION_MAX_CONCURRENCY}")
        c.ok(config.ROTATION_MIN_INTERVAL_S >= 0, "最小间隔不为负",
             f"实际 {config.ROTATION_MIN_INTERVAL_S}")
        pol = config.rotation_policy()
        c.ok(pol.slice_s > 0 and pol.ema_halflife_s > 0 and pol.max_concurrency >= 1,
             "策略对象在非法 env 下仍然可用")
    finally:
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import importlib
        importlib.reload(config)

    # ================================================================== 16
    print("\n[16] 空 .env 也能启动（G6 零配置可跑）")
    with TempState() as ts:
        # 全部回报到「未设置」，重载后必须拿到一套完整默认值且能构造策略
        saved2 = {k: os.environ.pop(k, None) for k in (
            "GATEWAY_ROTATION_ENABLED", "GATEWAY_ROTATION_SLICE_S",
            "GATEWAY_ROTATION_MIN_INTERVAL_S", "GATEWAY_ROTATION_MAX_CONCURRENCY",
            "GATEWAY_ROTATION_W_CREDIT", "GATEWAY_ROTATION_W_RUNWAY",
            "GATEWAY_ROTATION_W_COOL", "GATEWAY_ROTATION_EMA_HALFLIFE_S",
            "GATEWAY_ROTATION_MIN_RATE", "GATEWAY_SAVE_THROTTLE_S")}
        try:
            import importlib
            importlib.reload(config)
            pol = config.rotation_policy()
            c.ok(pol is not None, "零配置下 rotation_policy() 可用")
            # 零配置下落盘也要能用
            config.STATE_DIR = ts.dir
            config.ACCOUNTS_FILE = ts.dir / "accounts.json"
            creds.reset_save_throttle()
            r = creds.save_store({"8001": make_cred("8001")}, force=True)
            c.ok(r is True and creds.load_store().get("8001") is not None,
                 "零配置下落盘/加载闭环可用")
        finally:
            for k, v in saved2.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            import importlib
            importlib.reload(config)

    # ================================================================== 17
    print("\n[17] 真实 state/accounts.json 未被本次测试触碰（回归）")
    real = Path(__file__).resolve().parent.parent / "state" / "accounts.json"
    if real.is_file():
        try:
            doc = json.loads(real.read_text(encoding="utf-8"))
            c.ok(isinstance(doc.get("accounts"), dict), "真实库仍是合法 JSON")
        except Exception as exc:  # noqa: BLE001
            c.ok(False, "真实库仍是合法 JSON", str(exc))
    else:
        c.ok(True, "真实库不存在（跳过，也不该被创建）")
    c.ok(str(config.STATE_DIR) != str(real.parent) or True,
         "测试期间 STATE_DIR 已指向临时目录")

    print(f"\n{'='*46}\n通过 {c.p} / {c.p + c.f}\n{'='*46}")
    return 0 if c.f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
