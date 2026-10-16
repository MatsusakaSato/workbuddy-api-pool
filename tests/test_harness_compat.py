"""harness 兼容性探针：用真实客户端会发的请求形状去打网关，找兼容缺口。

覆盖 Cursor / Cline / Continue / Claude Code / 各类 SDK 的常见行为：
  system + 多轮、tools 声明、stream_options.include_usage、
  max_tokens 截断、未知模型、畸形请求、并发流。

用法：python3 tests/test_harness_compat.py [base_url]
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8788").rstrip("/")


def api_key() -> str:
    env = ROOT / ".env"
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.startswith("GATEWAY_API_KEY="):
            return line.split("=", 1)[1].strip()
    return os.environ.get("GATEWAY_API_KEY", "")


KEY = api_key()


def post(path: str, body, stream: bool = False, timeout: int = 180):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body, ensure_ascii=False)
    if isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
                 # 告诉网关这是测试流量：单独记账，别混进真实用量统计
                 "X-Gateway-Test": "1"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    if not stream:
        return resp.status, resp.read().decode("utf-8", "replace")
    # 流式：读到达 [DONE] 或超时
    buf = b""
    while True:
        chunk = resp.read(1024)
        if not chunk:
            break
        buf += chunk
        if b"[DONE]" in buf:
            break
    resp.close()
    return resp.status, buf.decode("utf-8", "replace")


def parse_sse(text: str):
    """返回 (拼接内容, finish_reason, usage, 是否出现 tool_calls, [DONE] 次数)"""
    content, finish, usage, saw_tools, done = "", None, None, False, 0
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            done += 1
            continue
        try:
            d = json.loads(payload)
        except Exception:  # noqa: BLE001
            continue
        if d.get("usage"):
            usage = d["usage"]
        for c in d.get("choices") or []:
            delta = c.get("delta") or {}
            content += delta.get("content") or ""
            if delta.get("tool_calls"):
                saw_tools = True
            if c.get("finish_reason"):
                finish = c["finish_reason"]
    return content, finish, usage, saw_tools, done


class R:
    def __init__(self):
        self.p = self.f = 0
        self.notes = []

    def ok(self, cond, label, detail=""):
        if cond:
            self.p += 1
            print(f"  ✅ {label}")
        else:
            self.f += 1
            print(f"  ❌ {label} {detail}")
            self.notes.append(label)


def main() -> int:
    r = R()
    print(f"目标网关: {BASE}\n")

    # A. system + 多轮
    print("A. system + 多轮对话")
    st, tx = post("/v1/chat/completions", {
        "model": "deepseek-v4-flash", "stream": True,
        "messages": [
            {"role": "system", "content": "你是一个只回答数字的助手。"},
            {"role": "user", "content": "1+1=?"},
            {"role": "assistant", "content": "2"},
            {"role": "user", "content": "再加 3 呢？只回答数字。"},
        ],
    }, stream=True)
    c, fin, usage, _, done = parse_sse(tx)
    r.ok(st == 200 and bool(c), f"多轮有回复：{c[:40]!r}")
    r.ok(done == 1, f"[DONE] 恰好 1 次（实际 {done}）")

    # B. tools 声明透传
    print("\nB. tools / function calling 透传")
    st, tx = post("/v1/chat/completions", {
        "model": "deepseek-v4-flash", "stream": True,
        "messages": [{"role": "user", "content": "北京现在天气怎么样？请调用工具查询。"}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "查询某城市天气",
                "parameters": {"type": "object",
                               "properties": {"city": {"type": "string"}},
                               "required": ["city"]},
            },
        }],
        "tool_choice": "auto",
    }, stream=True)
    c, fin, usage, saw_tools, done = parse_sse(tx)
    r.ok(st == 200, f"带 tools 的请求未被拒（HTTP {st}）", tx[:160] if st != 200 else "")
    r.ok(st != 200 or bool(c) or saw_tools, "有内容或 tool_calls 返回")
    if saw_tools:
        r.ok(fin == "tool_calls", f"finish_reason=tool_calls（实际 {fin}）")
        print("     → 上游原生支持 function calling")
    else:
        print(f"     → 上游未返回 tool_calls（内容 {c[:40]!r}），tools 被忽略但不报错")

    # C. stream_options.include_usage
    print("\nC. stream_options.include_usage")
    st, tx = post("/v1/chat/completions", {
        "model": "deepseek-v4-flash", "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [{"role": "user", "content": "只回复：usage"}],
    }, stream=True)
    c, fin, usage, _, done = parse_sse(tx)
    r.ok(st == 200, f"HTTP {st}")
    r.ok(usage is not None, f"usage 出现在流里（{usage}）")

    # D. max_tokens 截断
    print("\nD. max_tokens 生效")
    st, tx = post("/v1/chat/completions", {
        "model": "deepseek-v4-flash", "stream": True, "max_tokens": 8,
        "messages": [{"role": "user", "content": "从1数到100，每个数字用逗号隔开。"}],
    }, stream=True)
    c, fin, usage, _, done = parse_sse(tx)
    r.ok(st == 200, f"HTTP {st}")
    r.ok(len(c) < 100, f"输出被截断（长度 {len(c)}，finish={fin}）", f"内容 {c[:60]!r}")

    # E. 未知模型
    print("\nE. 未知模型的错误形状")
    st, tx = post("/v1/chat/completions", {
        "model": "definitely-not-a-model", "messages": [{"role": "user", "content": "hi"}]})
    try:
        d = json.loads(tx)
        shape_ok = isinstance(d.get("error"), dict) and "message" in d["error"]
    except Exception:  # noqa: BLE001
        shape_ok = False
    r.ok(shape_ok, f"返回 OpenAI 形状的 error（HTTP {st}）", tx[:160])
    if shape_ok:
        print(f"     → {json.loads(tx)['error']['code']}: {json.loads(tx)['error']['message'][:90]}")

    # F. 畸形 JSON
    print("\nF. 畸形请求体")
    st, tx = post("/v1/chat/completions", b"{not json")
    r.ok(st == 400, f"HTTP 400（实际 {st}）", tx[:120])

    # G. 缺 model
    print("\nG. 缺 model 字段")
    st, tx = post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}]})
    r.ok(st == 400, f"HTTP 400（实际 {st}）", tx[:120])

    # H. 并发流
    print("\nH. 并发两条流")
    results = {}

    def worker(name, prompt):
        t0 = time.time()
        st, tx = post("/v1/chat/completions", {
            "model": "deepseek-v4-flash", "stream": True,
            "messages": [{"role": "user", "content": prompt}]}, stream=True)
        c, fin, _, _, done = parse_sse(tx)
        results[name] = (st, c, round(time.time() - t0, 1))

    t1 = threading.Thread(target=worker, args=("a", "只回复：甲"))
    t2 = threading.Thread(target=worker, args=("b", "只回复：乙"))
    t1.start(); t2.start(); t1.join(); t2.join()
    r.ok(all(v[0] == 200 and v[1] for v in results.values()),
         f"两条流都成功：{ {k: (v[0], v[1][:12], f'{v[2]}s') for k, v in results.items()} }")

    print(f"\n{'='*52}\n通过 {r.p} / {r.p + r.f}\n{'='*52}")
    if r.notes:
        print("失败项：")
        for n in r.notes:
            print("  -", n)
    return 0 if r.f == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
