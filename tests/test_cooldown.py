"""限流重置时间解析测试。

上游 429 不带 Retry-After 头，重置时间在响应体的中文文案里，形如：
  {"code":6004,"msg":"…将在 2026-09-30 02:30:30 UTC+8 重置…"}
解析错了会导致账号被冷却过久（浪费额度）或过短（继续撞墙），所以锁死。

用法：python3 tests/test_cooldown.py
"""
from __future__ import annotations

import calendar
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway import upstream  # noqa: E402


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


def fmt(epoch: float, offset_h: int = 8) -> str:
    """把 epoch 格式化成上游那种带 UTC 偏移的写法。注意负偏移要写成 UTC-5 而不是 UTC+-5。"""
    t = time.gmtime(epoch + offset_h * 3600)
    sign = "+" if offset_h >= 0 else "-"
    return time.strftime("%Y-%m-%d %H:%M:%S", t) + f" UTC{sign}{abs(offset_h)}"


def main() -> int:
    c = Check()
    now = time.time()

    print("1. 带 UTC+8 偏移的正常文案")
    body = '{"code":6004,"msg":"当前模型使用人数较多，将在 %s 重置，请稍后再试"}' % fmt(now + 1800)
    got = upstream.parse_reset_seconds(body, now)
    c.ok(got is not None and 1790 <= got <= 1810, f"解析出 ≈1800 秒（实际 {got}）")

    print("\n2. 不带偏移的写法（按本地时区理解）")
    local = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now + 900))
    got = upstream.parse_reset_seconds(f"将在 {local} 重置", now)
    c.ok(got is not None and 890 <= got <= 910, f"解析出 ≈900 秒（实际 {got}）")

    print("\n3. UTC-5 负偏移必须正确处理（曾是最容易写反的地方）")
    body = "将在 %s 重置" % fmt(now + 3600, offset_h=-5)
    got = upstream.parse_reset_seconds(body, now)
    c.ok(got is not None and 3590 <= got <= 3610, f"解析出 ≈3600 秒（实际 {got}）")

    print("\n4. 已过去的时间不应产生冷却")
    body = "将在 %s 重置" % fmt(now - 600)
    c.ok(upstream.parse_reset_seconds(body, now) is None, "过去时间返回 None")

    print("\n5. 乱码/无关文案应安全返回 None")
    for junk in ["", "too many requests", '{"code":429}', "将在 不是时间 重置", "2026-13-45 99:99:99"]:
        c.ok(upstream.parse_reset_seconds(junk, now) is None, f"安全忽略: {junk[:28]!r}")

    print("\n6. 上限保护：上游写错年份也不能把账号封死")
    body = "将在 %s 重置" % fmt(now + 400 * 24 * 3600)
    got = upstream.parse_reset_seconds(body, now)
    c.ok(got == 86400.0, f"被截到 1 天（实际 {got}）")

    print("\n7. Retry-After 头优先于响应体")
    class H:
        def get(self, k):
            return "120" if k == "Retry-After" else None
    got = upstream._retry_after_from(H(), "将在 %s 重置" % fmt(now + 99999))
    c.ok(got == 120.0, f"用头的值（实际 {got}）")

    print("\n8. 没有头时回落到响应体")
    class H2:
        def get(self, k):
            return None
    got = upstream._retry_after_from(H2(), "将在 %s 重置" % fmt(now + 300))
    c.ok(got is not None and 290 <= got <= 310, f"用体的值（实际 {got}）")

    print("\n9. UpstreamError 携带 retry_after_s")
    e = upstream.UpstreamError("rate", "被限流", 429, 6004, retry_after_s=42.0)
    c.ok(e.retry_after_s == 42.0 and e.as_dict()["retry_after_s"] == 42.0,
         "字段可透出（供 report_failure 使用）")

    print("\n10. 账号池真的用上了解析出的重置时间")
    from gateway import pool as pool_mod
    from gateway.creds import Credential
    p = pool_mod.Pool()
    cred = Credential(uin="t", uid="u", domain="www.workbuddy.cn",
                      access_token="x", expires_at_ms=int((now + 3600) * 1000))
    t0 = time.monotonic()
    p.report_failure(cred, upstream.UpstreamError("rate", "限流", 429, 6004, retry_after_s=1234.0))
    remaining = cred.cooldown_until - t0
    c.ok(1225 <= remaining <= 1240, f"冷却按上游给的 1234 秒设置（实际 {remaining:.0f}s）")

    cred2 = Credential(uin="t2", uid="u", domain="www.workbuddy.cn",
                       access_token="x", expires_at_ms=int((now + 3600) * 1000))
    t0 = time.monotonic()
    p.report_failure(cred2, upstream.UpstreamError("rate", "限流", 429, 6004))
    remaining2 = cred2.cooldown_until - t0
    # 浮点比较：cooldown_until 是在 t0 之后取 monotonic 算的，所以会略大于 600
    c.ok(abs(remaining2 - pool_mod.config.RATE_LIMIT_COOLDOWN_S) < 0.5,
         f"上游没给时间时退回默认 {pool_mod.config.RATE_LIMIT_COOLDOWN_S}s（实际 {remaining2:.2f}s）")

    print(f"\n{'='*46}\n通过 {c.p} / {c.p + c.f}\n{'='*46}")
    return 0 if c.f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
