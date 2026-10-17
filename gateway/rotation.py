"""分层轮换调度器：让号池不再「薅羊毛」。

实现契约来源：`docs/账号轮换策略设计.md`（本文与代码冲突时以该文档为准）。

## 不变量（INVARIANT）

旧实现 `Pool.candidates()` 按 `-(credits)` 排序，`pick()` 取第一个，于是每个请求都打到
积分最高的那个号，直到它被限流或耗尽——这不是轮换，是「优先级队列」，高额度号承受
100% 负载被最先打空，全局吞吐被单号限流卡死。

本模块把「选谁」拆成三层，**顺序不可调换**：

  Layer 1  硬过滤（HARD FILTER）—— 只决定「能不能用」/「该不该先用」，不决定随机偏好
      a. `account_id in exclude`（本次请求已试过的号）；
      b. `not cred.available()`（耗尽 / 冷却中 / 凭据不可用）；
      c. `state.in_flight >= MAX_CONCURRENCY`（并发已达上限）；
      d. `now - state.last_used_at < MIN_INTERVAL_S`（最小间隔未到）；
      e. **基础套餐分级**（BUCKET TIER，2026-10-17 修正，见下）：若还有账号
         基础套餐余额为正，则剔除「基础套餐已耗尽」的账号；**整池都耗尽时该条不生效**。
      兜底：若**所有**候选都卡在 (d)，退化为只保留 `last_used_at` **最早**的那一个，
      而不是返回 None——否则单账号池或高并发突发会直接选不出号。

      为什么 Layer 1 必须是硬过滤而不是「扣分项」？因为 (c)(d) 是**保护账号**的约束。
      一旦降级成权重，高额度账号的 credit_share 会再次压过它们，权重随机永远保留一个
      非零概率去踩并发上限和最小间隔，于是退化成原问题（只是降低了踩的频率）。
      硬过滤把「不可能成功/不该打」的候选从解空间彻底移除，才让 Layer 3 的随机性
      只作用在**都还健康**的账号之间。一句话：Layer 1 保证正确性，Layer 2/3 只在
      正确性之上的可行域里做公平与偏好。

      ⚠️ (e) 是**分级**而不是「绝对值过滤」：它的判据是「池子里还有没有别人没耗尽」，
      所以 (i) 月末全员耗尽时不会有任何好被踢出池子（不会把「优先」偷偷变成「只准」）；
      (ii) 但只要有任何一个号还剩基础套餐，耗尽的那批就真的一个请求都分不到。

  Layer 2  时间片轮转 —— 保证**公平**（治「薅羊毛」的主力）
      `oldest = min(last_used_at)`，宽度 `W = max(SLICE_S, 2 * p95_recent_latency)`
      （延迟不可得时取 SLICE_S）。
      剔除 `last_used_at > oldest + W` 的候选——「刚被用过、而别人已经等了一整个
      时间片以上」的号本轮让位。`explore=True` 时跳过本层。
      收窄到只剩 1 个候选时**直接返回**，不再做无意义的随机化。

      **收窄是承重的**：它把「都没等够一个时间片」的那批账号删掉，剩下的才是
      「已经等过了、该轮到它们」的可行域。所以本层**不许**用 `or alive` 之类的兜底
      把收窄结果丢掉——那会让本层变成空操作。见 `_time_slice` 的说明。

  Layer 3  加权随机 —— 在**都健康且都已轮到位**的账号间做偏好
      ```
      credit_share = credits[c] / Σ credits[alive]
      runway_share = runway(c) / Σ runway(alive)
      coolness     = 1 - (now - last_used_at[c]) / max_age      # 越久没用越该用
      base_share   = base_credits[c] / Σ base_credits[alive]   # 基础套餐占比（新增）
      weight = W_BASE * base_share + W_CREDIT * credit_share
             + W_RUNWAY * runway_share + W_COOL * coolness
      ```
      权重全为 0（或权重和归零）时退化为**等概率**——
      **绝不因为「权重算不出来」而返回 None**。权重只影响偏好，不影响可得性。

## 基础套餐积分优先（2026-10-17，同日修正）

WorkBuddy 的余额是两个桶（见 `upstream.fetch_credits` 的长注释）：
**基础套餐积分**（月度订阅，月底清零）与**平台奖励积分**（一次性赠送包，过期晚得多）。
同样是 1 分，两种钱的价值并不相同：基础积分**今天不用、明天就清零**，是沉没成本；
奖励积分可以留到下个月。所以「优先使用基础套餐积分」在调度上的正确含义是：

> 优先把请求派给「基础积分还有剩」的号；只有在**池子里再没有基础积分了**的时候，
> 才让「基础已耗尽、只剩奖励积分」的号承接流量。

### 第一版为什么没生效（必须记住的教训）

第一版把这条策略实现成 Layer 3 的一个加权项 `base_share`（`W_BASE=1.5`）。
上线后实测：一个基础套餐已经归零的号仍然分到 **~16%** 的流量，和非零的号一样多。
原因有两层，**都不是权重没算对**：

1. **加权项只能改变「概率」，不能改变「资格」。** `base_share` 只是一个比例项，
   在 6 个号的池子里 `W_BASE=1.5` 只占约 5 分之 1 的权重，耗尽号照样有稳定概率
   被抽中。把 `W_BASE` 调到 20 也只把份额从 14% 压到 10%——**永远压不到 0**。
   实测回放（生产参数、真实余额、6s 串行节拍）：
   `W_BASE=1.5 → 14.4%`，`W_BASE=20 → 10.4%`，`W_BASE=50 → 9.7%`。
2. **它被安排在 Layer 2 之后，而 Layer 2 在串行流量下会吞掉整个候选集。**
   `SLICE_S=30` 而池子里每个号每轮都被用到，于是「最近 30 秒」必然覆盖**全部**
   账号，`cutoff = oldest + 30` 一个人也删不掉。要么第 2 层是空操作，要么
   （早期用 `or alive` 兜底时）收窄结果被直接丢弃——两种写法都让第 3 层拿到全量。

结论：**「先用哪种钱」是资格问题，不是偏好问题**，必须放在 Layer 1。
旧版那段「只进 Layer 3，绝不进 Layer 1」的理由（"月末会把只剩奖励积分的号全踢出池子"）
本身是对的，但它推导出的修法是错的——真正需要防的是「全员耗尽时无人可用」，
而这个只要把判据写成**分级**（"有人的时候才踢"）就自然满足了。

### 现在的实现

- **Layer 1 (e)** 做分级硬过滤：只要池子里还有 `base_credits > 0` 的账号，
  就剔除 `base_credits == 0` 的账号；全员耗尽时该条整体不生效，退回全池。
- **Layer 3 `base_share`** 保留，只在**同一级之内**决定偏好（两个号基础都有剩时，
  基础多的略占优）。它是锦上添花，不是这条策略的承重件。
- **`runway` 仍然照旧用总余额。** 用基础桶算续航会得出「奖励再多也马上没油」的
  假结论。`base_share` 只加在「先用谁的钱」上，不改「谁还能撑多久」。

### 两个回滚开关，语义不同（都测过）

- `GATEWAY_ROTATION_BASE_PRIORITY=0` —— 关掉上面的 Layer 1 分级，回到「按总余额
  加权、不考虑基础套餐」。**这才是这条策略真正的回滚开关。**
- `GATEWAY_ROTATION_W_BASE=0` —— 只关掉 Layer 3 的 `base_share` 偏好项。
  第一版曾把它当作整条策略的开关，这是错的：它连"耗尽号该收缩多少"都管不了
  （`base_share` 只是把耗尽号从 1/6 压到约 0.5/5.5，关掉它也就回到 1/6）。

返回 None 的唯一原因：Layer 1 / Layer 2 之后**没有任何候选**。任何「因为分数难看」
而拒绝服务的行为都是 bug。

## 设计约束

- **I3：`pick` 永不抛出**。任何内部异常都退化为返回第一个候选；一次选号异常绝不能
  变成用户可见的 500。
- **I5**：纯计算 + 内存状态，**不做任何 I/O**（不读盘、不发网络请求），可离线单测；
  落盘由 `creds.py` 负责，两者解耦。
- 线程安全：所有对共享状态的读写都在 `threading.RLock` 内。
- `last_used_at` 用 **monotonic**（进程内排序），`last_used_wall` 用 **wall clock**
  （跨重启可比较、由 persistence 落盘）。两者并存，各管一段，不得合并。
- 可注入 `random.Random` 以便测试复现（G7）。
"""
from __future__ import annotations

import logging
import math
import random
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional, Sequence, Set, Tuple

from . import config
from .creds import Credential

log = logging.getLogger("gateway.rotation")

__all__ = [
    "RotationPolicy",
    "RotationState",
    "RotationScheduler",
]


# --------------------------------------------------------------------------
# 策略参数
# --------------------------------------------------------------------------

def _cfg_env_float(name: str, default: float) -> float:
    """从 config 读浮点覆盖值，缺失/非法一律回落默认（G6：零配置可跑）。"""
    raw = getattr(config, name, None)
    if raw is None:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    return val if math.isfinite(val) and val > 0 else default


def _cfg_env_int(name: str, default: int) -> int:
    raw = getattr(config, name, None)
    if raw is None:
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return val if val > 0 else default


def _cfg_env_nonneg_float(raw, default: float) -> float:
    """允许 **0** 的浮点覆盖读取。

    `_cfg_env_float` 会把 <=0 一律当非法值回落默认 —— 对 min_interval 这类
    「必须为正」的参数是对的，但 `W_BASE=0` 是**关闭基础积分优先**的回滚开关，
    用同一个函数读会把「关掉」静默变成「按默认值开着」。缺失/非法/负数仍回落默认。
    """
    if raw is None:
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(val) or val < 0:
        return default
    return val


@dataclass
class RotationPolicy:
    """调度参数。默认值即 §6 表格里的安全默认值。"""

    # Layer 1
    min_interval_s: float = 5.0        # GATEWAY_ROTATION_MIN_INTERVAL_S
    max_concurrency: int = 2           # GATEWAY_ROTATION_MAX_CONCURRENCY
    # Layer 2
    slice_s: float = 30.0              # GATEWAY_ROTATION_SLICE_S
    # Layer 3
    w_credit: float = 1.0              # GATEWAY_ROTATION_W_CREDIT
    w_runway: float = 2.0              # GATEWAY_ROTATION_W_RUNWAY
    w_cool: float = 0.5                # GATEWAY_ROTATION_W_COOL
    # —— 基础套餐积分优先（2026-10-17，同日修正）——
    # base_priority 是**承重开关**：True 时 Layer 1 做分级过滤 ——
    #   只要池子里还有 base_credits>0 的号，base_credits==0 的号就分不到请求；
    #   全员耗尽时该条不生效（"优先"不变成"只准"）。
    # False = 整条策略关闭，回到「只有按总余额加权」的上一版行为。
    base_priority: bool = True         # GATEWAY_ROTATION_BASE_PRIORITY
    # w_base 只影响 Layer 3 的 base_share 偏好（同一级之内基础多的略占优）。
    # 它**不是**这条策略的开关 —— 第一版误把它当开关，实测 W_BASE 从 1.5 拉到 20
    # 也只把耗尽号的份额从 14% 压到 10%，永远压不到 0。见模块顶部。
    w_base: float = 1.5                # GATEWAY_ROTATION_W_BASE
    # runway 的 EMA
    ema_halflife_s: float = 900.0      # GATEWAY_ROTATION_EMA_HALFLIFE_S
    min_rate: float = 0.01             # GATEWAY_ROTATION_MIN_RATE（除零保护兼速率下限）
    # 内部细节
    enabled: bool = True               # GATEWAY_ROTATION_ENABLED=0 回滚到旧行为

    @classmethod
    def from_config(cls) -> "RotationPolicy":
        """优先用 `config.rotation_policy()`（persistence 负责落盘/env）；
        没有该函数就用本 dataclass 默认值 + config 属性覆盖。

        用 getattr 探测是为了不硬依赖 persistence 的工作先落地。
        """
        factory = getattr(config, "rotation_policy", None)
        if callable(factory):
            try:
                pol = factory()
                if isinstance(pol, RotationPolicy):
                    return pol
                if isinstance(pol, dict):
                    base = cls()
                    for key, val in pol.items():
                        if hasattr(base, key):
                            setattr(base, key, val)
                    return base
            except Exception:  # noqa: BLE001 — 配置坏了也要能起服务
                log.warning("rotation_policy() 读取失败，回落默认值", exc_info=True)
        # 回落路径：直接读 config 上的同名常量（名字与 §6 表格一致）。
        enabled_raw = getattr(config, "ROTATION_ENABLED", None)
        enabled = True if enabled_raw is None else bool(enabled_raw)
        base_prio_raw = getattr(config, "ROTATION_BASE_PRIORITY", None)
        base_prio = True if base_prio_raw is None else bool(base_prio_raw)
        return cls(
            min_interval_s=_cfg_env_float("ROTATION_MIN_INTERVAL_S", cls.min_interval_s),
            max_concurrency=_cfg_env_int("ROTATION_MAX_CONCURRENCY", cls.max_concurrency),
            slice_s=_cfg_env_float("ROTATION_SLICE_S", cls.slice_s),
            w_credit=_cfg_env_float("ROTATION_W_CREDIT", cls.w_credit),
            w_runway=_cfg_env_float("ROTATION_W_RUNWAY", cls.w_runway),
            w_cool=_cfg_env_float("ROTATION_W_COOL", cls.w_cool),
            # 注意 w_base 用 _cfg_env_float 会把 0 当成非法值回落默认值，
            # 而 0 恰恰是它的回滚开关 —— 所以这里单独走允许 0 的读取。
            w_base=_cfg_env_nonneg_float(
                getattr(config, "ROTATION_W_BASE", None), cls.w_base),
            ema_halflife_s=_cfg_env_float("ROTATION_EMA_HALFLIFE_S", cls.ema_halflife_s),
            # config 把它暴露为 MIN_RATE（已做地板/天花板钳制）
            min_rate=_cfg_env_float("MIN_RATE", cls.min_rate),
            enabled=enabled,
            base_priority=base_prio,
        )


# --------------------------------------------------------------------------
# 每账号运行期状态
# --------------------------------------------------------------------------

@dataclass
class RotationState:
    """单个账号的运行期可变状态。纯数据，无 I/O。"""

    last_used_at: float = 0.0            # time.monotonic() 基准；0 = 从未使用
    last_used_wall: float = 0.0          # time.time() 基准，跨重启（persistence 落盘）
    in_flight: int = 0                   # 当前在途请求数
    ema_cost: float = 0.0                # 消耗速率 EMA，单位 credits/分钟
    recent_credits: Deque[Tuple[float, float]] = field(default_factory=deque)
    recent_latency: Deque[float] = field(default_factory=deque)

    def rotate_state(self) -> None:
        """reload 时重置运行期字段（凭据内容变了，旧的运行期画像不再可信）。"""
        self.last_used_at = 0.0
        self.last_used_wall = 0.0
        self.in_flight = 0
        self.ema_cost = 0.0
        self.recent_credits.clear()
        self.recent_latency.clear()

    def note_credit(self, wall_ts: float, credit: float, maxlen: int = 64) -> None:
        if self.recent_credits.maxlen != maxlen:
            self.recent_credits = deque(self.recent_credits, maxlen=maxlen)
        self.recent_credits.append((wall_ts, credit))

    def note_latency(self, seconds: float, maxlen: int = 64) -> None:
        if seconds is None or not math.isfinite(seconds) or seconds < 0:
            return
        if self.recent_latency.maxlen != maxlen:
            self.recent_latency = deque(self.recent_latency, maxlen=maxlen)
        self.recent_latency.append(float(seconds))


# --------------------------------------------------------------------------
# 调度器
# --------------------------------------------------------------------------

class RotationScheduler:
    """三层调度的实现。持有 {account_id: RotationState}，线程安全。"""

    def __init__(self, policy: Optional[RotationPolicy] = None,
                 rng: Optional[random.Random] = None) -> None:
        self.policy = policy or RotationPolicy.from_config()
        self._lock = threading.RLock()
        self._states: Dict[str, RotationState] = {}
        self._rng = rng or random.Random()

    # ---------- 状态访问 ----------

    def state(self, cred_id: str) -> RotationState:
        """取（必要时创建）某账号的运行期状态。"""
        with self._lock:
            st = self._states.get(cred_id)
            if st is None:
                st = RotationState()
                self._states[cred_id] = st
            return st

    def rotate_state(self) -> None:
        """重载凭据后**全量**重置运行期状态（连 `last_used_at` 一起清空）。

        ⚠️ **不要**在「重载凭据」这条常规路径上调用它。`rotate_state` 把每个号的
        `last_used_at` 归零，于是所有号的 age 同时变成 `-inf` / 无穷大：
          · Layer 2 的「最旧者」失去意义（大家的年龄一样）；
          · Layer 3 的 `coolness` 对所有人同时变成 1.0（"都最该用"）。
        换句话说，一次 reload 就抹掉了「谁刚被用过」这只对轮换最重要的记忆。
        2026-10-17 实测：`/admin/reload` 之后轮换表现退化成随机。

        常规重载要用 `reset_inflight()`：它只清幽灵在飞计数，**保留**使用历史的排序。

        本方法仍保留给「凭据被整体替换、全池画像不再可信」的场合（例如手动清空）。
        """
        with self._lock:
            for st in self._states.values():
                st.rotate_state()

    def reset_inflight(self) -> None:
        """清空**在飞计数**，保留 `last_used_at` / `last_used_wall` / EMA。

        这是「重载凭据」该用的清理动作（见 `rotate_state` 的长注释）：
          · 账号对象被 reload 重建后，reload 之前占坑、reload 之后那个请求早已不在的
            「幽灵认领」必须清掉，否则槽位被永久吃掉，`pick` 最终永远返回 None；
          · 但**使用历史**必须留下 —— 它正是轮换用来回答「该轮到谁」的东西。

        用 `last_used_wall` 把 `last_used_at` 重新锚定到当前 monotonic 时钟：
        跨进程/长期运行后既有 `last_used_at` 可能已经毫无意义，而 `last_used_wall`
        是 wall clock（可跨重启比较）。换算时**保持相对顺序**：
        `age = now_wall - last_used_wall`，于是 `last_used_at = now_mono - age`。
        这样「刚被用过的号」重载后仍然是「刚被用过」，不会被误判成「最久未用」。
        """
        now_mono = time.monotonic()
        now_wall = time.time()
        with self._lock:
            for st in self._states.values():
                st.in_flight = 0
                if st.last_used_wall > 0.0:
                    age = now_wall - st.last_used_wall
                    if age < 0.0:
                        age = 0.0
                    st.last_used_at = now_mono - age

    def states_snapshot(self) -> Dict[str, dict]:
        """面板/测试观测用：在飞计数泄漏只看这里（§8 风险缓解）。"""
        with self._lock:
            return {
                aid: {
                    "in_flight": st.in_flight,
                    "last_used_at": st.last_used_at,
                    "last_used_wall": st.last_used_wall,
                    "ema_cost": st.ema_cost,
                }
                for aid, st in self._states.items()
            }

    def _prune(self, alive: Sequence[str]) -> None:
        """丢掉已不在池子里的账号状态，避免长期运行内存缓慢增长。"""
        keep = set(alive)
        for key in [k for k in self._states if k not in keep]:
            self._states.pop(key, None)

    # ---------- Layer 1 硬过滤 ----------

    @staticmethod
    def _base_of(cred: Credential, base_credits: Optional[Dict[str, int]]) -> Optional[float]:
        """读某账号的基础套餐余额；**未知返回 None，不返回 0**。

        「未知」与「0」在这里必须分开（与 `Pool.credits_total` 同一条规矩）：
        0 是「这个月的基础套餐已经花完」，None 是「我们还没拉到它的余额」。
        把未知当成 0，一次积分接口抖动就会把整个池子判成「全员耗尽」而关掉分级。
        """
        if not base_credits:
            return None
        val = base_credits.get(cred.account_id)
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            return None
        if not math.isfinite(float(val)):
            return None
        return float(val)

    def _bucket_tier(self, cands: List[Credential],
                     base_credits: Optional[Dict[str, int]]) -> List[Credential]:
        """Layer 1 (e)：基础套餐分级过滤 —— 「优先花月底会清零的钱」。

        ## 判据是**分级**，不是绝对值

        设 `known = [c for c in cands if base(c) is not None]`：
          · 若 `known` 里**有任何一个** `base > 0` → 只保留 `base > 0` 的账号；
            基础余额未知的账号**也保留**（不能因为没拉到余额就饿死它们）。
          · 若 `known` 全为 0（或 `known` 为空 / 根本没传 `base_credits`）→
            本层不生效，原样返回 `cands`。

        这个「有人才踢」的判据就是「优先」与「只准」的分界线：
        月末全员清零时不会有任何账号被踢出池子，`pick` 仍然能选出号；
        但只要池子里还有一分基础积分，已经耗尽的号就**真的**一个请求都分不到
        （而不是像 Layer 3 的权重那样只被压低概率）。

        ## 为什么必须在 Layer 1 而不是 Layer 3

        见模块顶部「基础套餐积分优先 → 第一版为什么没生效」：加权项只是概率，
        永远留着非零的被选中机会；而且它排在 Layer 2 之后，在串行流量下拿到的
        甚至不是收窄后的集合。「先用哪种钱」是资格问题。
        """
        if not self.policy.base_priority:
            return cands
        if not cands:
            return cands
        has_base: List[Credential] = []
        unknown: List[Credential] = []
        for cred in cands:
            val = self._base_of(cred, base_credits)
            if val is None:
                unknown.append(cred)
            elif val > 0.0:
                has_base.append(cred)
        if not has_base:
            # 全员耗尽（或余额未知）：分级不生效。**绝不返回空列表** ——
            # 那会把「优先」变成「只准」，月末直接没有可用账号。
            return cands
        if len(has_base) == len(cands) and not unknown:
            return cands                      # 全都有基底，无事可做
        return has_base + unknown

    def _hard_filter(self, cands: List[Credential], now: float,
                     exclude: Set[str],
                     base_credits: Optional[Dict[str, int]] = None) -> List[Credential]:
        """Layer 1。返回可用候选；全部卡最小间隔时退化为「最早使用的那个」。

        `base_credits` 只用于 (e) 基础套餐分级（见 `_bucket_tier`）；
        不传 = 不做分级，行为与「基础套餐优先」上线前一致。
        """
        pol = self.policy
        out: List[Credential] = []
        interval_blocked: List[Credential] = []
        for cred in cands:
            aid = cred.account_id
            if aid in exclude:
                continue
            if not cred.available():
                continue
            st = self._states.get(aid)
            if st is None:
                out.append(cred)          # 从未用过：不占并发、无间隔约束
                continue
            if st.in_flight >= pol.max_concurrency:
                continue
            if st.last_used_at > 0.0 and (now - st.last_used_at) < pol.min_interval_s:
                interval_blocked.append(cred)
                continue
            out.append(cred)

        if not out and interval_blocked:
            # §3 Layer1 兜底：全被最小间隔挡住时，保留最早使用的那个而不是返回 None。
            # 否则单账号池 / 高并发突发会直接选不出号。
            oldest = min(interval_blocked,
                         key=lambda c: self._last_used_of(c.account_id))
            log.debug("Layer1 兜底：全部候选都在最小间隔内，放宽为最久未用的 %s",
                      oldest.account_id)
            return [oldest]
        # (e) 排在 (a)~(d) 之后：先保证「能用」，再决定「先用谁的钱」。
        # 放在最后还有一个好处：分级只在**已经可用**的集合上比较，
        # 不会因为某个号正在冷却而被误判成「池子里没有基础积分了」。
        return self._bucket_tier(out, base_credits)

    def _last_used_of(self, cred_id: str) -> float:
        st = self._states.get(cred_id)
        # 从未用过按 -inf 参与「谁最久没用」的比较，最优先
        if st is None or st.last_used_at <= 0.0:
            return float("-inf")
        return st.last_used_at

    # ---------- Layer 2 时间片 ----------

    def slice_width(self, cands: Sequence[Credential]) -> float:
        """W = max(SLICE_S, 2 × p95_recent_latency)；延迟不可得时取 SLICE_S。"""
        lat: List[float] = []
        for cred in cands:
            st = self._states.get(cred.account_id)
            if st is not None:
                lat.extend(st.recent_latency)
        if not lat:
            return self.policy.slice_s
        lat.sort()
        idx = min(len(lat) - 1, max(0, math.ceil(0.95 * len(lat)) - 1))
        p95 = lat[idx]
        return max(self.policy.slice_s, 2.0 * p95)

    def _waiting(self, cands: List[Credential], now: float) -> List[Credential]:
        """「已经等够了」的候选 —— 即**本请求真的该轮到它们**的号。

        ## 判据曾经用过 MIN_INTERVAL_S，那是错的（2026-10-17 修正）

        旧实现在这里用 `age >= MIN_INTERVAL_S` 区分「串行」与「突发」，想解决的是
        「串行流量下所有人都早已过了最小间隔，时间片收窄不了任何东西」。但它假设
        「串行 ⇒ 所有人年龄都 >= MIN_INTERVAL_S」，**这个前提在 N 个号的池子里永远
        不成立**：N 个号轮流用，同一个号两次被用之间至少隔 N × 请求间隔。实测 N=6、
        请求间隔 6s 时每号年龄约 36s，远大于 5s ⇒ `waiting == cands` 恒成立
        ⇒ 本层恒等于「什么都不筛」。换成 N=2、间隔 6s（年龄约 12s > 5s）结论相同。
        **第 2 层在生产参数下从来没有筛掉过任何号**，第 3 层因此永远拿到全量候选。

        ## 现在的判据

        用一个**远长于最小间隔**的宽限窗口 `W = slice_width(cands)`（生产值 30s）：
        「等够了」= `age >= W`。语义就是本层要表达的东西：

          · 有号**等满了一个完整时间片** → 本轮在该子集里轮转；
          · 所有号都还没等够（高并发突发、或池子很小） → 子集为空，
            退回全量，交给第 3 层按额度 / 续航 / 基础套餐决定。**不等于**报错或饥饿。

        这样第 2 层只在「确实有人等得太久」时出手，且出手时**真的**收窄；
        不会像旧判据那样恒等地什么都不做。

        为什么 `age` 用 `>=`（等够了）而不是 `<`（还在等）：请求**本可以**用它们，
        才是「该轮到它们了」。写反时会恰好只留下刚用过的那个号。
        """
        if not cands:
            return []
        window = self.slice_width(cands)
        if window <= 0:
            return list(cands)
        return [c for c in cands if now - self._last_used_of(c.account_id) >= window]

    def _time_slice(self, alive: List[Credential]) -> List[Credential]:
        """Layer 2。剔除 last_used_at 比最旧者新出一个 W 以上的候选。

        返回**收窄后**的集合。理论上 `oldest` 自己必然入选（`oldest <= oldest + W`），
        所以不可能返回空列表；真为空只可能是 NaN 之类的脏数据污染了 `last_used_at`。

        ⚠️ **不要**把返回值写成 `sliced or alive` 当作「兜底」。那条兜底会在
        `last_used_at` 被污染时静默把整个集合放回去，让本层变成空操作——
        正是旧版「时间片永远收窄不了任何东西」的成因之一（另一处见 `pick` 里的
        `waiting or alive`）。真出现空集合时退回**最久未用的那一个**：
        既保证非空，又不放弃「最该用的那个」。
        """
        oldest = min(self._last_used_of(c.account_id) for c in alive)
        cutoff = oldest + self.slice_width(alive)
        sliced = [c for c in alive if self._last_used_of(c.account_id) <= cutoff]
        if sliced:
            return sliced
        log.warning("Layer2 收窄结果为空（last_used_at 疑似被污染为 NaN），退回最久未用的那个")
        return [min(alive, key=lambda c: self._last_used_of(c.account_id))]

    # ---------- Layer 3 权重 ----------

    def runway(self, cred_id: str, credits: Dict[str, int]) -> float:
        """续航（分钟）：`credits / max(ema_cost_per_min, MIN_RATE)`。

        无消耗历史且额度未知 → 0.0：**降权，不淘汰**（保守选择）。
        """
        st = self._states.get(cred_id)
        ema = st.ema_cost if st is not None else 0.0
        remaining = credits.get(cred_id)
        if ema <= 0.0 and remaining is None:
            return 0.0
        rate = max(ema, self.policy.min_rate)
        return max(0.0, float(remaining or 0)) / rate

    def _coolness(self, cred: Credential, now: float, oldest: float,
                  newest: float) -> float:
        """`coolness = 1 - (now - last_used_at) / max_age`，钳制到 [0,1]。

        越久没用越该用。max_age 取候选集内 last_used_at 的观测跨度，
        跨度不可得（只有一个候选 / 从未用过）时取 SLICE_S。

        **钳制是必须的**：不钳制时 `now - last_used_at` 一旦超过 max_age（长时间运行的
        进程里必然发生），coolness 会变成很大的负数，直接吃掉 credit/runway 项的权重，
        使健康账号总权重 <= 0 而被静默跳过，甚至污染加权抽样。钳制后语义依旧正确：
        age >= max_age 即「最该用」，取 1.0。
        """
        age = now - self._last_used_of(cred.account_id)
        if age <= 0.0:
            return 1.0                     # 从未用过：最优先
        span = newest - oldest
        max_age = span if span > 0 else self.policy.slice_s
        if max_age <= 0:
            max_age = self.policy.slice_s
        return max(0.0, min(1.0, 1.0 - age / max_age))

    def _weights(self, cands: List[Credential], credits: Dict[str, int],
                 now: float, base_credits: Optional[Dict[str, int]] = None) -> List[float]:
        pol = self.policy
        n = len(cands)
        if n == 0:
            return []

        cred_vals = [max(0.0, float(credits.get(c.account_id) or 0)) for c in cands]
        cred_total = sum(cred_vals)
        cred_share = [v / cred_total for v in cred_vals] if cred_total > 0 else [0.0] * n

        # 基础套餐积分占比（「先花月底会清零的钱」）。缺失/为 0 时整项为 0，
        # 权重退化成上一版的三项式 —— 这就是这条策略的天然回滚路径。
        base_map = dict(base_credits or {})
        base_vals = [max(0.0, float(base_map.get(c.account_id) or 0)) for c in cands]
        base_total = sum(base_vals)
        base_share = [v / base_total for v in base_vals] if base_total > 0 else [0.0] * n

        run_vals = [self.runway(c.account_id, credits) for c in cands]
        run_total = sum(run_vals)
        run_share = [v / run_total for v in run_vals] if run_total > 0 else [0.0] * n

        lus = [self._last_used_of(c.account_id) for c in cands]
        finite = [x for x in lus if x != float("-inf")]
        oldest = min(finite) if finite else 0.0
        newest = max(finite) if finite else 0.0

        out: List[float] = []
        for i, c in enumerate(cands):
            w = (pol.w_base * base_share[i]
                 + pol.w_credit * cred_share[i]
                 + pol.w_runway * run_share[i]
                 + pol.w_cool * self._coolness(c, now, oldest, newest))
            out.append(w if math.isfinite(w) and w > 0.0 else 0.0)
        return out

    # ---------- 主入口 ----------

    def pick(self, cands: List[Credential], credits: Dict[str, int],
             now: float, exclude: Optional[Set[str]] = None,
             explore: bool = False,
             base_credits: Optional[Dict[str, int]] = None) -> Optional[Credential]:
        """三层调度选号，**并原子地占住胜出者的在飞名额**。None 仅当 Layer 1/2 后确无候选。

        参数分工（**不可混用**）：
          · `credits`       —— 可花**总**余额。credit_share 与 runway 都用它。
          · `base_credits`  —— **基础套餐**余额，喂两处（「优先使用基础套餐积分」）：
                               Layer 1 (e) 的分级过滤（承重，决定「资格」）与
                               Layer 3 的 `base_share`（偏好，同级别内微调）。
                               缺省 = 不做基础套餐优先，行为与上一版完全一致。


        ## 为什么 pick 必须自己占坑（claim）

        早先的实现只「检查」`in_flight >= max_concurrency`，真正 +1 发生在调用方
        （`Pool.note_request_started`，由 server.py 在 pick 返回之后调用）。检查与占用
        不原子，于是 N 个并发请求会**全部**通过检查后才各自 +1——并发上限在它唯一
        有意义的场景（并发突发）里完全失效。实测：cap=2、16 线程、pick 与 started
        之间有任何真实间隔时，会授予 16 个名额。

        修法：在**同一把锁**内完成「检查 → 选号 → +1」，胜出者返回时名额已占好。
        因此并发上限是真正的硬约束（doc §3 Layer 1），而不是一个带竞态的提示。

        **并发上限没有兜底**：若所有候选都达上限则返回 None（与「最小间隔」兜底不同），
        调用方据此换区域或报错。上限一旦允许「实在不行就用最闲的」，就等于没有上限。

        `explore=True` 跳过 Layer 2（纯加权随机），用于自检/探活。
        **I3：本方法永不抛出。** 内部异常退化为「返回第一个候选」（此时也会占坑，
        避免调用方释放一个从未占用的名额）。
        """
        exclude = exclude or set()
        cands = list(cands or [])
        if not cands:
            return None
        try:
            with self._lock:
                self._prune([c.account_id for c in cands])

                # --- Layer 1：硬过滤（含 (e) 基础套餐分级）---
                alive = self._hard_filter(cands, now, exclude, base_credits)
                if not alive:
                    return None

                # --- Layer 2：时间片轮转 ---
                # 作用域（见 _waiting）：
                #   · 有号等满了一个完整时间片 → 只在「等够了的那批」里做轮转；
                #   · 没人等够（突发 / 小池）     → 用 Layer 1 全量，交给 Layer 3。
                # 两种情况下都**必须**调用 _time_slice：它本身就是「剔除刚用过、
                # 别人都等了一整片」的收窄。旧版这里的 `waiting or alive` 会在
                # waiting 为空时把全量塞回去，而旧 _waiting 的条件在生产参数下恒真，
                # 两者叠加让本层成了空操作（见模块顶部「第一版为什么没生效」）。
                if explore:
                    pool_c = alive
                else:
                    waiting = self._waiting(alive, now)
                    pool_c = self._time_slice(waiting or alive)

                # --- Layer 3：加权随机 ---
                if len(pool_c) == 1:
                    winner = pool_c[0]      # §3：唯一候选直接返回，不做无意义随机
                else:
                    winner = self._weighted_choice(pool_c, credits, now, base_credits)

                # --- 原子占坑：检查与 +1 在同一个临界区内 ---
                if winner is not None:
                    self.state(winner.account_id).in_flight += 1
                return winner
        except Exception:  # noqa: BLE001 — I3：选号异常绝不能变成 500
            log.exception("轮换调度异常，退化为第一个候选")
            fallback = cands[0]
            try:
                with self._lock:       # 兜底路径同样占坑，保证与释放成对
                    self.state(fallback.account_id).in_flight += 1
            except Exception:  # noqa: BLE001
                pass
            return fallback

    def _weighted_choice(self, pool_c: List[Credential], credits: Dict[str, int],
                         now: float,
                         base_credits: Optional[Dict[str, int]] = None,
                         ) -> Optional[Credential]:
        """Layer 3 加权随机。权重全 0 时退化为等概率（绝不返回 None）。"""
        weights = self._weights(pool_c, credits, now, base_credits)
        total = sum(weights)
        if total <= 0.0:
            return self._rng.choice(pool_c)
        r = self._rng.random() * total
        acc = 0.0
        for c, w in zip(pool_c, weights):
            acc += w
            if r < acc:
                return c
        return pool_c[-1]               # 浮点兜底

    def claim(self, cred_id: str) -> None:
        """显式占用一个在飞名额（`pick` 已自动占，仅在需要补偿时用）。"""
        with self._lock:
            self.state(cred_id).in_flight += 1


    # ---------- pool 回调 ----------

    def mark_used(self, cred_id: str, wall_ts: float) -> None:
        with self._lock:
            st = self.state(cred_id)
            st.last_used_at = time.monotonic()
            st.last_used_wall = wall_ts

    def mark_inflight(self, cred_id: str, delta: int) -> None:
        """并发计数增减；下限夹到 0（重复 release 不能让计数变负）。"""
        with self._lock:
            st = self.state(cred_id)
            st.in_flight = max(0, st.in_flight + int(delta))

    def release_claim(self, cred_id: str) -> bool:
        """释放一个由 `pick()` 占下的名额。返回是否真的释放了。

        **只在确实持有占用时才减 1**，因此：
          - 重复调用 / 对从未占用的账号调用都是安全的空操作（不会把别的在飞请求
            的名额偷走）；
          - `Pool.release` 可以无条件调用，调用方不需要关心 `started` 标志位。
        """
        with self._lock:
            st = self._states.get(cred_id)
            if st is None or st.in_flight <= 0:
                return False
            st.in_flight -= 1
            return True

    def in_flight_of(self, cred_id: str) -> int:
        """权威在飞计数（Layer 1 读的就是它）。"""
        with self._lock:
            st = self._states.get(cred_id)
            return st.in_flight if st is not None else 0

    def note_credit(self, cred_id: str, wall_ts: float, credit: float,
                    elapsed_s: float = 0.0) -> None:
        """记录一次消耗并更新 EMA（credits/分钟）。

        halflife 语义：alpha = 1 - 0.5 ** (elapsed / halflife)，
        于是「刚刚突然开始大额消耗」的号会**立刻**降权（§3）。
        """
        try:
            credit = float(credit or 0.0)
        except (TypeError, ValueError):
            return
        with self._lock:
            st = self.state(cred_id)
            st.note_credit(wall_ts, credit)
            span = elapsed_s if elapsed_s and elapsed_s > 0 else 60.0
            if not math.isfinite(span) or span <= 0:
                span = 60.0
            rate = max(0.0, credit) / (span / 60.0)
            hl = max(1e-6, self.policy.ema_halflife_s)
            alpha = min(1.0, max(0.0, 1.0 - math.pow(0.5, span / hl)))
            st.ema_cost = (1.0 - alpha) * st.ema_cost + alpha * rate

    def note_latency(self, cred_id: str, seconds: float) -> None:
        """记录一次延迟样本，供 Layer 2 的 `2 × p95` 宽度使用。"""
        with self._lock:
            self.state(cred_id).note_latency(seconds)
