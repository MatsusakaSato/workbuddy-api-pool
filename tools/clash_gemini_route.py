#!/usr/bin/env python3
"""给 Clash Verge 加一条「Google AI 走美国节点」的定向规则。

安全设计：
  1. 改任何文件前先备份
  2. 改完用 verge-mihomo -t 预检；不通过就立刻回滚
  3. 预检通过才 reload；reload 后验证组是否真的出现
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.parse

CFG = pathlib.Path.home() / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev"
MIHOMO = "/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo"
SOCK = "/tmp/verge/verge-mihomo.sock"
SECRET = "set-your-secret"

GROUPS_FILE = CFG / "profiles/gNH0JD3r9Lnv.yaml"   # 三毛机场 的 groups 扩展
RULES_FILE = CFG / "profiles/rg25K13XG1sQ.yaml"    # 三毛机场 的 rules 扩展
RUNTIME = CFG / "clash-verge.yaml"

GROUP_NAME = "Gemini-US"
#: 关键：不能用 url-test。节点标签 ≠ 真实出口地区——实测 8 个「美国」节点里
#: 有 2 个（美国堪萨斯、美国洛杉矶-3）真实出口过不了 Gemini，而 url-test 按延迟
#: 选，恰好挑中了坏的那个，表现为间歇性「User location is not supported」。
#: 所以用 fallback（不按延迟漂移）+ 过滤掉已知不可用的节点。
GROUP_BLOCK = f"""- name: {GROUP_NAME}
  type: fallback
  include-all: true
  filter: "(?i)(美国|洛杉矶|凤凰城)"
  exclude-filter: "(堪萨斯|洛杉矶-3)"
  url: http://www.gstatic.com/generate_204
  interval: 300
"""

#: 扩展配置里的组定义（与 GROUP_BLOCK 保持一致）
GROUP_YAML = (
    f"  - name: {GROUP_NAME}\n"
    "    type: fallback\n"
    "    include-all: true\n"
    '    filter: "(?i)(美国|洛杉矶|凤凰城)"\n'
    '    exclude-filter: "(堪萨斯|洛杉矶-3)"\n'
    "    url: http://www.gstatic.com/generate_204\n"
    "    interval: 300\n"
)

AI_DOMAINS = [
    "generativelanguage.googleapis.com",   # AI Studio / Gemini API
    "daily-cloudcode-pa.googleapis.com",   # Antigravity 实际端点
    "cloudcode-pa.googleapis.com",         # Code Assist 备用端点
]
RULES_BLOCK = "".join(f"- DOMAIN-SUFFIX,{d},{GROUP_NAME}\n" for d in AI_DOMAINS)

BACKUPS: list[tuple[pathlib.Path, pathlib.Path]] = []


def backup(p: pathlib.Path) -> pathlib.Path:
    dst = p.with_suffix(p.suffix + ".bak-gemini")
    if not dst.exists():
        shutil.copy2(p, dst)
    BACKUPS.append((p, dst))
    return dst


def rollback() -> None:
    print("\n!! 回滚 !!")
    for orig, bak in BACKUPS:
        if bak.exists():
            shutil.copy2(bak, orig)
            print(f"   已还原 {orig.name}")
    reload_core()
    print("   回滚完成")


def validate() -> tuple[bool, str]:
    r = subprocess.run([MIHOMO, "-t", "-d", str(CFG), "-f", str(RUNTIME)],
                       capture_output=True, text=True, timeout=90)
    out = (r.stdout or "") + (r.stderr or "")
    ok = "test is successful" in out
    return ok, out.strip().splitlines()[-1] if out.strip() else "(无输出)"


def clash(method: str, path: str, body=None):
    cmd = ["curl", "-s", "-X", method, "--unix-socket", SOCK,
           "-H", f"Authorization: Bearer {SECRET}", "-H", "Content-Type: application/json"]
    if body is not None:
        cmd += ["-d", json.dumps(body, ensure_ascii=False)]
    cmd.append("http://localhost" + path)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def reload_core() -> bool:
    out = clash("PUT", "/configs?force=true", {"path": str(RUNTIME)})
    return "error" not in out.lower()


def main() -> int:
    for p in (GROUPS_FILE, RULES_FILE, RUNTIME):
        if not p.is_file():
            print(f"找不到 {p}")
            return 2

    print("① 备份")
    for p in (GROUPS_FILE, RULES_FILE, RUNTIME):
        print(f"   {p.name} -> {backup(p).name}")

    print("\n② 写入扩展配置（订阅更新不会冲掉）")
    GROUPS_FILE.write_text(
        "# Profile Enhancement Groups Template for Clash Verge\n"
        "# 由网关按需添加：让 Google AI 域名走美国节点（香港被 Gemini 地理封锁）\n\n"
        "prepend:\n" + GROUP_YAML + "\nappend: []\n\ndelete: []\n", encoding="utf-8")
    RULES_FILE.write_text(
        "# Profile Enhancement Rules Template for Clash Verge\n"
        "# 由网关按需添加：Google AI 走美国节点\n\n"
        "prepend:\n" + "".join(f"  - DOMAIN-SUFFIX,{d},{GROUP_NAME}\n" for d in AI_DOMAINS) +
        "\nappend: []\n\ndelete: []\n", encoding="utf-8")
    print(f"   groups -> {GROUPS_FILE.name}")
    print(f"   rules  -> {RULES_FILE.name}")

    print("\n③ 同步到运行时配置（立即生效）")
    raw = RUNTIME.read_text(encoding="utf-8")
    lines = raw.splitlines(keepends=True)
    out: list[str] = []
    g_done = r_done = False
    skipping_group = False
    for ln in lines:
        # 幂等：先剔除上一次插入的 Gemini-US 组，避免重复定义
        if ln.startswith(f"- name: {GROUP_NAME}"):
            skipping_group = True
            continue
        if skipping_group:
            # 顶层列表项（- xxx）或顶层键（rules: 之类）即表示这一组结束了
            if ln.startswith("- ") or (ln.strip() and not ln[0].isspace()):
                skipping_group = False
            else:
                continue
        if ln.startswith(f"- DOMAIN-SUFFIX,") and f",{GROUP_NAME}" in ln:
            continue  # 幂等：旧规则已存在则替换
        out.append(ln)
        if not g_done and ln.startswith("proxy-groups:"):
            out.append(GROUP_BLOCK)
            g_done = True
        elif not r_done and ln.startswith("rules:"):
            out.append(RULES_BLOCK)
            r_done = True
    if not (g_done and r_done):
        print(f"   锚点没找到 (proxy-groups={g_done}, rules={r_done}) → 放弃")
        return 3
    RUNTIME.write_text("".join(out), encoding="utf-8")
    n = sum(1 for x in out if x.strip() == f"- name: {GROUP_NAME}")
    print(f"   已写入 {n} 个组定义（应为 1）+ 3 条规则")

    print("\n④ 预检配置")
    ok, msg = validate()
    print(f"   {'✅ 通过' if ok else '❌ 不通过'}: {msg}")
    if not ok:
        rollback()
        return 4

    print("\n⑤ reload 内核")
    print(f"   {'✅' if reload_core() else '❌'} 已 reload")
    time.sleep(3)

    print("\n⑥ 验证")
    grp = clash("GET", f"/proxies/{urllib.parse.quote(GROUP_NAME)}")
    try:
        d = json.loads(grp)
        members = d.get("all", [])
        print(f"   组 {GROUP_NAME}: type={d.get('type')} 成员 {len(members)} 个, 当前={d.get('now')!r}")
        for m in members[:8]:
            print(f"      - {m}")
    except Exception:
        print(f"   ❌ 组没出现: {grp[:200]}")
        rollback()
        return 5
    return 0


def _gemini_probe() -> tuple[int | None, str]:
    """用 Antigravity 凭据发一个最小请求，判断当前节点能否过 Gemini。"""
    import base64
    import os
    import urllib.error
    import urllib.request

    tok_path = pathlib.Path.home() / ".gemini/jetski-standalone-oauth-token"
    try:
        tok = json.loads(tok_path.read_text(encoding="utf-8"))
        at = tok["token"]["access_token"]
    except Exception as exc:  # noqa: BLE001
        return None, f"读不到 Antigravity token: {exc}"
    body = {"model": "gemini-2.5-flash", "project": "aicode-consumers",
            "request": {"contents": [{"role": "user", "parts": [{"text": "回：通"}]}],
                        "generationConfig": {"maxOutputTokens": 10}}}
    req = urllib.request.Request(
        "https://daily-cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + at, "Content-Type": "application/json",
                 "User-Agent": "antigravity/2.23.1"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
        return 200, ""
    except urllib.error.HTTPError as exc:
        try:
            msg = json.loads(exc.read().decode()).get("error", {}).get("message", "")
        except Exception:  # noqa: BLE001
            msg = "?"
        return exc.code, msg[:70]
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:70]


def probe_and_pin() -> int:
    """逐个实测组内节点的真实出口能否过 Gemini，并固定到第一个可用节点。"""
    d = json.loads(clash("GET", f"/proxies/{urllib.parse.quote(GROUP_NAME)}"))
    members = d.get("all", [])
    original = d.get("now")
    if not members:
        print(f"组 {GROUP_NAME} 里没有成员")
        return 6
    print(f"组 {GROUP_NAME}：{len(members)} 个节点，原选中={original!r}\n")
    good: list[str] = []
    try:
        for i, node in enumerate(members, 1):
            clash("PUT", f"/proxies/{urllib.parse.quote(GROUP_NAME)}", {"name": node})
            time.sleep(1.2)
            st, msg = _gemini_probe()
            if st == 200:
                good.append(node)
                print(f"  ✅ [{i}/{len(members)}] {node}")
            else:
                print(f"  ❌ [{i}/{len(members)}] {node}  HTTP {st} {msg}")
    finally:
        target = good[0] if good else original
        if target:
            clash("PUT", f"/proxies/{urllib.parse.quote(GROUP_NAME)}", {"name": target})
        print(f"\n  已固定到: {target!r}")
    if good:
        print(f"  可用节点 {len(good)}/{len(members)}：{good}")
        if len(good) < len(members):
            bad = [m for m in members if m not in good]
            print(f"  ⚠️ 应加入 exclude-filter 的不可用节点：{bad}")
        return 0
    print("  ❌ 组内没有节点能过 Gemini")
    return 7


if __name__ == "__main__":
    if "--probe" in sys.argv:
        sys.exit(probe_and_pin())
    sys.exit(main())
