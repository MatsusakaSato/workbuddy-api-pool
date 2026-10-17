"""开发热重载的守卫测试。

守的是两类**真实发生过**的事故，都能让"进程总是崩溃"：

  1. 单实例锁失效 → 残留多个 --dev 监视器 → 每个都重启自己的 worker
     → 多个 worker 抢 8788 → 输的抛 OSError: Address already in use。
     实测过一次残留 4 个监视器，2 次重载撞出 2 次崩溃。

  2. 锁文件被后来者以 "w" 打开截断 → 报错时读不到对方 PID，
     用户完全不知道该杀谁。

不依赖 hupper，也不碰真实 state/。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway import server as S  # noqa: E402


class Counter:
    def __init__(self):
        self.p = 0
        self.f = 0

    def ok(self, cond, label, detail=""):
        if cond:
            self.p += 1
            print(f"  ✅ {label}" + (f"  {detail}" if detail else ""))
        else:
            self.f += 1
            print(f"  ❌ {label}  {detail}")


def main() -> int:
    c = Counter()
    tmp = Path(tempfile.mkdtemp(prefix="reload-guard-"))
    lock = tmp / "sub" / ".dev-reload.lock"

    print("\n[1] 首个实例能拿到锁")
    first = S._acquire_single_instance_lock(lock)
    c.ok(first is not None, "首个实例拿到锁")
    c.ok(lock.exists(), "锁文件已创建", str(lock.name))

    print("\n[2] 锁文件里写的是持锁进程 PID")
    c.ok(first is not None, "前置条件：已持锁")
    if first is not None:
        first.seek(0)
        content = first.read().strip()
        c.ok(content == str(os.getpid()), "锁文件记录了 PID", f"[{content}]")

    print("\n[3] 第二个实例必须被拒绝（而不是抢端口）")
    second = S._acquire_single_instance_lock(lock)
    c.ok(second is None, "第二个实例拿不到锁")

    print("\n[4] 后来者不得截断锁文件（否则读不到对方 PID）")
    # 这是真出现过的 bug：用 open("w") 会在 flock 失败前就把文件清空。
    if first is not None:
        first.seek(0)
        still = first.read().strip()
        c.ok(still == str(os.getpid()), "失败者没有抹掉持有者 PID", f"[{still}]")
        c.ok(len(still) > 0, "锁文件不是 0 字节")

    print("\n[5] 释放后可以被重新获取")
    if first is not None:
        first.close()
    third = S._acquire_single_instance_lock(lock)
    c.ok(third is not None, "前一个释放后新实例能拿到锁")
    if third is not None:
        third.close()

    print("\n[6] 不同锁文件互不影响")
    a = S._acquire_single_instance_lock(tmp / "a.lock")
    b = S._acquire_single_instance_lock(tmp / "b.lock")
    c.ok(a is not None and b is not None, "两个不同锁文件都能拿到")
    for f in (a, b):
        if f is not None:
            f.close()

    print("\n[7] --dev 判定：命令行与环境变量")
    old_argv, old_env = sys.argv[:], os.environ.get("GATEWAY_DEV")
    try:
        sys.argv = ["gateway.server"]
        os.environ.pop("GATEWAY_DEV", None)
        c.ok(S._dev_enabled() is False, "默认不开启（生产路径零第三方依赖）")
        sys.argv = ["gateway.server", "--dev"]
        c.ok(S._dev_enabled() is True, "命令行 --dev 生效")
        sys.argv = ["gateway.server"]
        os.environ["GATEWAY_DEV"] = "1"
        c.ok(S._dev_enabled() is True, "环境变量 GATEWAY_DEV=1 生效")
        os.environ["GATEWAY_DEV"] = "0"
        c.ok(S._dev_enabled() is False, "GATEWAY_DEV=0 视为关闭")
    finally:
        sys.argv = old_argv
        if old_env is None:
            os.environ.pop("GATEWAY_DEV", None)
        else:
            os.environ["GATEWAY_DEV"] = old_env

    print("\n[8] devdeps 路径注入只认项目内的 .devdeps")
    S._install_devdeps()
    devdeps = Path(S.__file__).resolve().parent.parent / ".devdeps"
    if devdeps.is_dir():
        c.ok(str(devdeps) in sys.path, ".devdeps 已加入 sys.path")
    else:
        c.ok(True, "（未安装 .devdeps，跳过）")

    print(f"\n通过 {c.p} / {c.p + c.f}")
    return 1 if c.f else 0


if __name__ == "__main__":
    raise SystemExit(main())
