"""故障注入测试：验证账号池的换号契约。

目标项「多账号轮换」在只有 1 个真实账号时无法自然触发，这里注入一个
「token 无效但未过期」的假账号，迫使网关走换号路径。

跑的正是 server._chat 用的同一条链路：
    pick -> open_chat_stream -> report_failure -> pick(exclude) -> open_chat_stream

用法：
    python3 tests/test_failover.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway import pool as pool_mod, upstream  # noqa: E402
from gateway.creds import Credential  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s %(name)-16s %(message)s")

FAKE_UIN = "000000000000"
FAKE_TOKEN = "eyJhbGciOiJIUzI1NiJ9." + ("A" * 1200) + ".fake"


class Check:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def ok(self, cond: bool, label: str, detail: str = "") -> None:
        if cond:
            self.passed += 1
            print(f"  ✅ {label}")
        else:
            self.failed += 1
            print(f"  ❌ {label} {detail}")


def main() -> int:
    c = Check()
    p = pool_mod.Pool()
    p.reload(deep=False)

    real = None
    for cred in p.list_accounts():
        if cred.region == "cn" and cred.token_valid:
            real = cred
            break
    if real is None:
        print("没有可用的真实国内版账号，无法测试")
        return 2
    print(f"真实账号: uin={real.uin}\n")

    # --- 注入假账号，并让它在排序里排第一（积分更高）---
    fake = Credential(
        uin=FAKE_UIN, uid="fake-uid", domain="www.workbuddy.cn",
        access_token=FAKE_TOKEN, refresh_token="",
        expires_at_ms=int(time.time() * 1000) + 30 * 24 * 3600 * 1000,
        source="injected-test", persistable=False,
    )
    p._creds[FAKE_UIN] = fake
    p._credits[FAKE_UIN] = 9_999_999
    p._credits[real.account_id] = 1731

    print("场景 1：假账号排在第一位，应当被选中")
    picked = p.pick("deepseek-v4-flash")
    c.ok(picked.uin == FAKE_UIN, "优先选中假账号（证明排序生效）", f"实际={picked.uin}")

    print("\n场景 2：假账号发请求，应当失败并归类为可换号错误")
    exc = None
    try:
        resp, _, _ = upstream.open_chat_stream(
            fake, {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "hi"}]}
        )
        resp.close()
    except upstream.UpstreamError as e:
        exc = e
    c.ok(exc is not None, "假 token 被上游拒绝")
    if exc is not None:
        c.ok(exc.kind in ("auth", "policy", "server", "client"),
             f"错误被分类（kind={exc.kind}, status={exc.status}）")
    else:
        c.ok(False, "错误被分类", "居然成功了？")

    print("\n场景 3：report_failure 应当要求换号")
    should_rotate = p.report_failure(fake, exc)
    c.ok(should_rotate is True, "报告失败后要求换号")

    print("\n场景 4：排除假账号后，应当换到真实账号并成功")
    second = p.pick("deepseek-v4-flash", exclude={FAKE_UIN})
    c.ok(second.uin == real.uin, "换到真实账号", f"实际={second.uin}")

    text = ""
    resp, _, _ = upstream.open_chat_stream(
        second, {"model": "deepseek-v4-flash", "messages": [{"role": "user", "content": "只回复：换号成功"}]}
    )
    try:
        buf = b""
        import json as _json
        for _ in range(600):
            chunk = resp.read(512)
            if not chunk or b"[DONE]" in buf:
                break
            buf += chunk
        for line in buf.split(b"\n"):
            if line.startswith(b"data:") and b"[DONE]" not in line:
                try:
                    d = _json.loads(line[5:])["choices"][0].get("delta", {})
                    text += d.get("content") or ""
                except Exception:  # noqa: BLE001
                    pass
    finally:
        resp.close()
    c.ok(bool(text), f"真实账号回答：{text!r}")

    print("\n场景 5：client 类错误不应换号（换谁都一样）")
    fake2 = Credential(uin="1", uid="u", domain="www.workbuddy.cn",
                       access_token=real.access_token, refresh_token="",
                       expires_at_ms=real.expires_at_ms)
    should = p.report_failure(fake2, upstream.UpstreamError("client", "bad request", 400))
    c.ok(should is False, "client 错误不换号")

    print("\n场景 6：credit 类错误应把账号移出池子")
    fake3 = Credential(uin="2", uid="u", domain="www.workbuddy.cn",
                       access_token=real.access_token, refresh_token="",
                       expires_at_ms=real.expires_at_ms)
    should = p.report_failure(fake3, upstream.UpstreamError("credit", "积分不足", 403, 14018))
    c.ok(should is True and fake3.exhausted is True, "credit 错误标记为积分耗尽并换号")

    print("\n场景 7：注入的假账号不得被写进自有库（回归）")
    import json as _j
    from gateway import config as _cfg
    saved = {}
    if _cfg.ACCOUNTS_FILE.is_file():
        saved = (_j.loads(_cfg.ACCOUNTS_FILE.read_text(encoding="utf-8")).get("accounts") or {})
    c.ok(FAKE_UIN not in saved, "假账号未落盘", f"库里的键={list(saved.keys())}")
    c.ok(all(k != "1" and k != "2" for k in saved), "场景5/6 的临时账号也未落盘",
         f"库里的键={list(saved.keys())}")

    print(f"\n{'='*46}\n通过 {c.passed} / {c.passed + c.failed}\n{'='*46}")
    return 0 if c.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
