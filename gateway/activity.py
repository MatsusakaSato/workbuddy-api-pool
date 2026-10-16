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

__all__ = ["Activity", "ACTIVITY", "MAX_RECORDS", "RETENTION_DAYS", "mark_test"]

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
        for rec in kept:
            self._apply(rec)
            self._items.append(self._view(rec))
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
                "latency_sum": 0, "latency_max": 0}

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
        return out

    @staticmethod
    def _shape(b: Dict) -> Dict:
        n = b["requests"] or 0
        return {
            "requests": n,
            "errors": b["errors"],
            "error_rate": round(b["errors"] / n, 4) if n else 0,
            "credits": round(b["credits"], 4),
            "tokens": b["tokens"],
            "avg_ms": round(b["latency_sum"] / n) if n else 0,
            "max_ms": b["latency_max"],
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
            first_at, last_at = self._first_at, self._last_at
            totals_all = {"requests": self.total_requests,
                          "errors": self.total_errors,
                          "credits": round(self.total_credits, 4),
                          "tokens": sum(b["tokens"] for b in by_day.values())}

        today = _day_of(time.time())
        days = max(1, min(int(days or 30), RETENTION_DAYS))

        # 连续日期序列，缺的补 0 —— 否则柱状图会把没有请求的日子跳过，趋势是假的
        series: List[Dict] = []
        now = time.time()
        for i in range(days - 1, -1, -1):
            d = _day_of(now - i * 86400)
            b = by_day.get(d) or self._blank()
            series.append({
                "date": d,
                "requests": b["requests"],
                "errors": b["errors"],
                "credits": round(b["credits"], 4),
                "tokens": b["tokens"],
            })

        def window(n: int) -> Dict:
            keys = {s["date"] for s in series[-n:]} if series else set()
            return self._shape(self._fold([by_day.get(k) or self._blank() for k in keys]))

        by_day_all = list(by_day.values())
        totals = {
            "today": self._shape(by_day.get(today) or self._blank()),
            "d7": window(7),
            "d30": window(30),
            "all": self._shape(self._fold(by_day_all)),
        }
        totals["all"]["requests"] = totals_all["requests"]
        totals["all"]["errors"] = totals_all["errors"]
        totals["all"]["credits"] = totals_all["credits"]

        def table(src: Dict[str, Dict], key: str) -> List[Dict]:
            rows = []
            for name, b in src.items():
                row = self._shape(b)
                row[key] = name
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
            "models": table(by_model, "model"),
            "accounts": table(by_account, "account"),
        }


ACTIVITY = Activity()
