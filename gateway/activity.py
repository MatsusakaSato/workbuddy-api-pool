"""请求活动记录 + 用量统计。

两层结构：

1. **内存环形缓冲**（MAX_RECORDS 条）—— 供面板「最近请求」列表，够用就行。
2. **追加式 JSONL**（state/usage.jsonl）—— 让用量统计跨重启累积。

为什么要有第 2 层：第 1 层重启即归零，所以「本月消耗多少积分」这类问题在
只有内存记录时根本答不了——那正是要统计的东西。

只记元数据（模型、命中账号、耗时、积分、tokens、结果），
**不记提示词与回复内容**——这是刻意的，避免把用户对话落到磁盘。
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Optional

from . import config

log = logging.getLogger("gateway.activity")

MAX_RECORDS = 200
RETENTION_DAYS = 90

"""
延迟直方图：桶边界（毫秒）。

为什么要存直方图而不是只存 sum/max：面板要用**分布条**展示延迟，
需要 p50/p90/p99。而 p99 无法从"总和 + 最大值"反推 —— 必须有分布信息。
又因为聚合是**落盘累积**的（重启不丢），不能存原始样本（会无限增长），
所以存一个定长直方图：18 个桶，按毫秒量级近似对数分布。

桶边界的取法：1s 以下切得细（推理首字延迟集中在这里），
1s 以上按"秒级"切到 60s，再往上按分钟级粗切。
理由是运维判断只需要区分"常态 / 偏慢 / 事故"三档，
而 120s 与 900s 之间如果只有一个桶，一个 143s 的卡顿会被报成 900s —— 明显失真。

末桶 1800s（30 分钟）之后单独一桶收溢出值，保证不丢计数。
"""
LATENCY_BUCKETS = (100, 200, 400, 700, 1000, 1500, 2000, 3000, 4000, 6000,
                   8000, 10000, 15000, 20000, 30000, 45000, 60000,
                   90000, 120000, 180000, 300000, 600000, 1800000)
BUCKET_COUNT = len(LATENCY_BUCKETS) + 1   # 最后一桶收 >900s 的溢出值

__all__ = ["Activity", "ACTIVITY", "MAX_RECORDS", "RETENTION_DAYS", "mark_test",
           "LATENCY_BUCKETS", "BUCKET_COUNT", "histogram_percentile"]


def _bucket_index(ms: int) -> int:
    """毫秒 → 桶下标。线性扫 18 个桶足够快，且避免二分带来的边界歧义。"""
    for i, edge in enumerate(LATENCY_BUCKETS):
        if ms <= edge:
            return i
    return BUCKET_COUNT - 1


def histogram_percentile(hist: List[int], q: float) -> int:
    """从直方图估算分位数，返回该桶的**上界**（保守：宁可报大不报小）。

    hist 为定长列表；q 取 0.5 / 0.9 / 0.99。空直方图返回 0。
    """
    total = sum(hist)
    if total <= 0:
        return 0
    # 目标排名（1-based）。用 ceil 让 q=0.5 且 n=2 时取到第 1 个，符合直觉。
    rank = max(1, math.ceil(q * total))
    seen = 0
    for i, count in enumerate(hist):
        seen += count
        if seen >= rank:
            # 返回桶上界。溢出桶没有上界，用最大边界的 2 倍兜底。
            if i >= len(LATENCY_BUCKETS):
                return LATENCY_BUCKETS[-1] * 2
            return LATENCY_BUCKETS[i]
    return LATENCY_BUCKETS[-1] * 2


# 回归测试会真的打网关（harness 套件约 13 次真实请求，还有一次故意用不存在的模型）。
# 这些流量如果混进用量统计，失败率会被凭空拉高、「本月消耗」也不再是你真实用掉的。
# 所以测试流量单独记账，默认不计入聚合。
_local = threading.local()


def mark_test(flag: bool = True) -> None:
    """标记当前线程正在处理的是测试流量。每个请求开头都要重设，线程是复用的。"""
    _local.test = bool(flag)


def _is_test() -> bool:
    return bool(getattr(_local, "test", False))


def _day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _daily_cell(cell: object) -> Dict[str, int]:
    """把 _daily 里的一格归一成 {"requests", "tokens"}。

    存在的意义是**向后兼容**：升级前落盘的 _daily 值（如果曾被序列化）是裸 int，
    只表示请求数。这里统一吸收两种形状，避免读旧数据时抛异常。
    未知形状一律当 0 处理而不是崩溃 —— 聚合数据坏一格不该让整个面板打不开。
    """
    if isinstance(cell, dict):
        return {
            "requests": int(cell.get("requests") or 0),
            "tokens": int(cell.get("tokens") or 0),
        }
    if isinstance(cell, (int, float)) and not isinstance(cell, bool):
        return {"requests": int(cell), "tokens": 0}
    return {"requests": 0, "tokens": 0}


def _default_path() -> Optional[Path]:
    """落盘位置。GATEWAY_USAGE_LOG=0 可整体关掉（测试或不想留痕时用）。"""
    if os.environ.get("GATEWAY_USAGE_LOG", "1").strip() in ("0", "false", "no"):
        return None
    return config.STATE_DIR / "usage.jsonl"


class Activity:
    def __init__(self, maxlen: int = MAX_RECORDS, path: Optional[Path] = None) -> None:
        self._lock = threading.Lock()
        self._items: Deque[Dict] = deque(maxlen=maxlen)
        self._seq = 0
        self.total_requests = 0
        self.total_errors = 0
        self.total_credits = 0.0
        self.test_requests = 0
        self.started_at = time.time()
        self._path = Path(path) if path is not None else _default_path()
        self._by_day: Dict[str, Dict] = {}
        self._by_model: Dict[str, Dict] = {}
        self._by_account: Dict[str, Dict] = {}
        # 行内趋势线需要「某模型 / 某账号在每一天」的请求数。
        # 存成 {(维度, 名称): {日期: 次数}}，键是字符串以便直接落盘进 JSON。
        # 只存请求数：趋势线表达的是"在变多还是变少"，不需要完整聚合。
        self._daily: Dict[str, Dict[str, int]] = {}
        self._write_failed = False
        self._loaded_records = 0
        self._first_at: Optional[float] = None
        self._last_at: Optional[float] = None
        self._load()

    # ------------------------------------------------------------------ 落盘
    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        cutoff = time.time() - RETENTION_DAYS * 86400
        kept: List[Dict] = []
        dropped = 0
        try:
            with self._path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    ts = rec.get("ts")
                    if not isinstance(ts, (int, float)):
                        continue
                    if ts < cutoff:
                        dropped += 1
                        continue
                    kept.append(rec)
        except OSError as exc:
            log.warning("用量记录读取失败：%s", exc)
            return
        max_seq = 0
        for rec in kept:
            # 记录历史最大序号。seq 在内存里自增、不落盘，若不在载入时恢复，
            # 重启后新记录会从 1 重新开始，和文件里已有记录的 seq 撞车。
            # 前端拿 seq 当列表 key，撞车会导致 React 复用/丢弃节点
            # （症状：请求日志明明有 200 条，只渲染出十几行）。
            s = rec.get("seq")
            if isinstance(s, int) and s > max_seq:
                max_seq = s
            self._apply(rec)
            self._items.append(self._view(rec))
        self._seq = max_seq
        self._loaded_records = len(kept)
        if dropped:
            self._rewrite(kept)
            log.info("用量记录：载入 %d 条，清理 %d 条超过 %d 天的旧记录",
                     len(kept), dropped, RETENTION_DAYS)
        else:
            log.info("用量记录：载入 %d 条", len(kept))

    def _rewrite(self, recs: List[Dict]) -> None:
        """整理文件（丢掉过期记录）。先写临时文件再原子替换，避免写坏。"""
        if self._path is None:
            return
        tmp = self._path.with_name(self._path.name + ".tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                for rec in recs:
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except OSError as exc:
            log.warning("用量记录整理失败：%s", exc)

    def _append(self, rec: Dict) -> None:
        if self._path is None or self._write_failed:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fresh = not self._path.exists()
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if fresh:
                os.chmod(self._path, 0o600)
        except OSError as exc:
            # 统计写不进去不该影响请求本身，也不该每请求刷屏
            self._write_failed = True
            log.warning("用量记录写入失败，本次会话不再重试：%s", exc)

    # ------------------------------------------------------------------ 聚合
    @staticmethod
    def _blank() -> Dict:
        return {"requests": 0, "errors": 0, "credits": 0.0, "tokens": 0,
                "latency_sum": 0, "latency_max": 0,
                # 定长延迟直方图，用于估算 p50/p90/p99（见 LATENCY_BUCKETS 的说明）
                "latency_hist": [0] * BUCKET_COUNT}

    @staticmethod
    def _bump(bucket: Dict, rec: Dict) -> None:
        bucket["requests"] += 1
        if not rec.get("ok", True):
            bucket["errors"] += 1
        bucket["credits"] += float(rec.get("credit") or 0)
        bucket["tokens"] += int(rec.get("tokens") or 0)
        ms = int(rec.get("total_ms") or rec.get("latency_ms") or 0)
        bucket["latency_sum"] += ms
        if ms > bucket["latency_max"]:
            bucket["latency_max"] = ms
        # 旧记录（本次升级前落盘的）可能没有 hist 键 —— 补上再累加，不让老数据崩
        hist = bucket.get("latency_hist")
        if not isinstance(hist, list) or len(hist) != BUCKET_COUNT:
            hist = [0] * BUCKET_COUNT
            bucket["latency_hist"] = hist
        hist[_bucket_index(ms)] += 1

    def _apply(self, rec: Dict) -> None:
        ts = rec["ts"]
        if rec.get("test"):
            self.test_requests += 1        # 只记数，不进任何聚合
            return
        self.total_requests += 1
        if not rec.get("ok", True):
            self.total_errors += 1
        self.total_credits += float(rec.get("credit") or 0)
        if self._first_at is None or ts < self._first_at:
            self._first_at = ts
        if self._last_at is None or ts > self._last_at:
            self._last_at = ts
        for table, key in ((self._by_day, _day_of(ts)),
                           (self._by_model, rec.get("model") or "—"),
                           (self._by_account, rec.get("account") or "—")):
            bucket = table.get(key)
            if bucket is None:
                bucket = table[key] = self._blank()
            self._bump(bucket, rec)

        # 行内趋势线 + 每日 hover 明细用：记录 (维度:名称) 在当天的请求数与 tokens。
        # 这里**故意不受 test 流量影响** —— 测试流量在上面已经 return 掉了，
        # 所以趋势线画的是真实用量，与表格里的 totals 口径一致。
        #
        # 值用 dict 而不是裸 int：既要请求数又要 tokens。旧记录（升级前落盘的）
        # 是裸 int，_daily 的读取处统一用 _daily_slot() 兼容，不会因为版本差异炸掉。
        day = _day_of(ts)
        tokens = int(rec.get("tokens") or 0)
        for dim, name in (("model", rec.get("model") or "—"),
                          ("account", rec.get("account") or "—")):
            slot = self._daily.setdefault(f"{dim}\t{name}", {})
            cell = slot.get(day)
            if not isinstance(cell, dict):
                # 旧格式（裸 int = 请求数）就地升级，保留原有计数
                cell = {"requests": int(cell or 0), "tokens": 0}
                slot[day] = cell
            cell["requests"] += 1
            cell["tokens"] += tokens

    @staticmethod
    def _view(rec: Dict) -> Dict:
        return {
            "seq": rec.get("seq"),
            "at": rec["ts"],
            "time": time.strftime("%H:%M:%S", time.localtime(rec["ts"])),
            "model": rec.get("model", ""),
            "account": rec.get("account", ""),
            "upstream": rec.get("upstream", ""),
            "stream": bool(rec.get("stream")),
            "latency_ms": int(rec.get("latency_ms") or 0),
            "total_ms": int(rec.get("total_ms") or 0),
            "credit": rec.get("credit") or 0,
            "tokens": int(rec.get("tokens") or 0),
            "ok": bool(rec.get("ok", True)),
            "error": rec.get("error", ""),
            "test": bool(rec.get("test")),
        }

    # ------------------------------------------------------------------ 写入
    def record(self, *, model: str = "", account: str = "", upstream: str = "workbuddy",
               stream: bool = False, latency_ms: int = 0, total_ms: int = 0,
               credit: Optional[float] = None, tokens: int = 0, ok: bool = True,
               error: str = "") -> None:
        ts = time.time()
        with self._lock:
            self._seq += 1
            rec = {
                "ts": ts,
                "seq": self._seq,
                "model": model,
                "account": account,
                "upstream": upstream,
                "stream": bool(stream),
                "latency_ms": int(latency_ms),
                "total_ms": int(total_ms or latency_ms),
                "credit": round(float(credit), 4) if credit else 0,
                "tokens": int(tokens or 0),
                "ok": bool(ok),
                "error": (error or "")[:200],
                "test": _is_test(),
            }
            self._apply(rec)
            self._items.append(self._view(rec))
        self._append(rec)

    # ------------------------------------------------------------------ 读取
    def recent(self, limit: int = 50) -> List[Dict]:
        with self._lock:
            items = list(self._items)
        items.reverse()  # 最新在前
        return items[:limit]

    def stats(self) -> Dict:
        with self._lock:
            return {
                "total_requests": self.total_requests,
                "total_errors": self.total_errors,
                "total_credits": round(self.total_credits, 4),
                "uptime_s": int(time.time() - self.started_at),
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(self.started_at)),
                "persisted": self._path is not None and not self._write_failed,
                "loaded_records": self._loaded_records,
                "test_requests": self.test_requests,
            }

    @staticmethod
    def _fold(buckets) -> Dict:
        out = Activity._blank()
        for b in buckets:
            out["requests"] += b["requests"]
            out["errors"] += b["errors"]
            out["credits"] += b["credits"]
            out["tokens"] += b["tokens"]
            out["latency_sum"] += b["latency_sum"]
            out["latency_max"] = max(out["latency_max"], b["latency_max"])
            # 直方图逐桶相加；缺键的旧桶当全 0 处理
            hist = b.get("latency_hist")
            if isinstance(hist, list) and len(hist) == BUCKET_COUNT:
                for i, count in enumerate(hist):
                    out["latency_hist"][i] += count
        return out

    @staticmethod
    def _shape(b: Dict) -> Dict:
        n = b["requests"] or 0
        hist = b.get("latency_hist")
        if not isinstance(hist, list) or len(hist) != BUCKET_COUNT:
            hist = [0] * BUCKET_COUNT
        return {
            "requests": n,
            "errors": b["errors"],
            "error_rate": round(b["errors"] / n, 4) if n else 0,
            "credits": round(b["credits"], 4),
            "tokens": b["tokens"],
            "avg_ms": round(b["latency_sum"] / n) if n else 0,
            "max_ms": b["latency_max"],
            # 面板的分布条用这三个值画形状。注意它们是**桶上界**的保守估计，
            # 不是精确分位点 —— 直方图精度决定，够用来判断"是常态还是事故"。
            "p50_ms": histogram_percentile(hist, 0.50),
            "p90_ms": histogram_percentile(hist, 0.90),
            "p99_ms": histogram_percentile(hist, 0.99),
        }

    def usage(self, days: int = 30) -> Dict:
        """用量统计。全部来自落盘累积的聚合，重启不丢。

        注意口径：totals 按时间窗口，models/accounts 是**保留期内全部**——面板上要写明，
        否则「今天消耗」和「模型消耗」对不上会让人以为是 bug。
        """
        with self._lock:
            by_day = {k: dict(v) for k, v in self._by_day.items()}
            by_model = {k: dict(v) for k, v in self._by_model.items()}
            by_account = {k: dict(v) for k, v in self._by_account.items()}
            daily = {k: dict(v) for k, v in self._daily.items()}
            first_at, last_at = self._first_at, self._last_at
            totals_all = {"requests": self.total_requests,
                          "errors": self.total_errors,
                          "credits": round(self.total_credits, 4),
                          "tokens": sum(b["tokens"] for b in by_day.values())}

        today = _day_of(time.time())
        days = max(1, min(int(days or 30), RETENTION_DAYS))

        # 逐日「按模型」拆分。柱/折线图的 hover 要回答"这天是谁在跑、耗了多少 token"，
        # 而 totals 只给了当天的总数。直接从 _daily 反推，不新增存储：
        # _daily 的键是 "model\t<名称>"，值是该模型逐日的 {"requests", "tokens"}。
        model_daily: Dict[str, Dict[str, Dict[str, int]]] = {}
        for key, slot in daily.items():
            dim, _, name = key.partition("\t")
            if dim != "model":
                continue
            for d, cell in slot.items():
                model_daily.setdefault(d, {})[name] = _daily_cell(cell)

        # 连续日期序列，缺的补 0 —— 否则柱状图会把没有请求的日子跳过，趋势是假的
        series: List[Dict] = []
        now = time.time()
        for i in range(days - 1, -1, -1):
            d = _day_of(now - i * 86400)
            b = by_day.get(d) or self._blank()
            per_model = model_daily.get(d) or {}
            series.append({
                "date": d,
                "requests": b["requests"],
                "errors": b["errors"],
                "credits": round(b["credits"], 4),
                "tokens": b["tokens"],
                # 按 tokens 降序 —— hover 面板里用户最关心"谁吃掉了最多 token"，
                # 而请求数排序会让一个高频但极短的小模型排在大模型前面，误导判断。
                # 前端直接顺序渲染即可，不必再排一次。不含零值项。
                "models": [
                    {"model": name, "requests": cell["requests"], "tokens": cell["tokens"]}
                    for name, cell in sorted(
                        per_model.items(), key=lambda kv: (-kv[1]["tokens"], -kv[1]["requests"], kv[0])
                    )
                    if cell["requests"] > 0
                ],
            })

        def window(n: int) -> Dict:
            keys = {s["date"] for s in series[-n:]} if series else set()
            return self._shape(self._fold([by_day.get(k) or self._blank() for k in keys]))

        by_day_all = list(by_day.values())
        totals = {
            # 「今天」与 days=1 是同一个口径：只看当天的自然日。
            # 保留 today 这个旧键，客户端/脚本可能已在用。
            "today": self._shape(by_day.get(today) or self._blank()),
            "d1": window(1),
            "d7": window(7),
            "d30": window(30),
            "all": self._shape(self._fold(by_day_all)),
        }
        totals["all"]["requests"] = totals_all["requests"]
        totals["all"]["errors"] = totals_all["errors"]
        totals["all"]["credits"] = totals_all["credits"]

        def table(src: Dict[str, Dict], key: str, dim: str) -> List[Dict]:
            rows = []
            for name, b in src.items():
                row = self._shape(b)
                row[key] = name
                # 行内趋势线：该实体在这段窗口内的逐日请求数，与 days 等长且对齐。
                # 对齐很重要 —— 前端直接把两个数组配对画图，不做日期匹配。
                # 用 _daily_cell 归一，兼容升级前的裸 int 形状。
                slot = daily.get(f"{dim}\t{name}") or {}
                row["daily"] = [_daily_cell(slot.get(s["date"], 0))["requests"] for s in series]
                rows.append(row)
            rows.sort(key=lambda r: (-r["credits"], -r["requests"]))
            return rows

        return {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "retention_days": RETENTION_DAYS,
            "persisted": self._path is not None and not self._write_failed,
            "path": str(self._path) if self._path else None,
            "loaded_records": self._loaded_records,
            "test_requests": self.test_requests,
            "first_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first_at)) if first_at else None,
            "last_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last_at)) if last_at else None,
            "totals": totals,
            "days": series,
            "models": table(by_model, "model", "model"),
            "accounts": table(by_account, "account", "account"),
        }


ACTIVITY = Activity()
