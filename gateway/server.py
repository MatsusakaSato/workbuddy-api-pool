"""HTTP 层：OpenAI 兼容路由 + 本地管理页。仅绑 127.0.0.1，纯标准库。"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import checkin, config, creds, gemini, upstream
from . import __version__ as GATEWAY_VERSION
from . import antigravity
from .activity import ACTIVITY, mark_test
from .pool import Pool
from .upstream import UpstreamError

log = logging.getLogger("gateway.server")

POOL = Pool()
# 全自动签到调度器。它在自己的后台线程里定时跑，见 serve() 里的 start()。
# 状态落盘在 state/checkin.json —— 与账号库分开：签到记录丢了最多重领一次，
# 不该和凭据共用文件、互相牵连。
AUTO_CHECKIN = checkin.AutoCheckin(POOL)
STATIC_DIR = Path(__file__).resolve().parent / "static"

# 只接受回环 Host（配合注入 key，防 DNS rebinding）
_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


# --------------------------------------------------------------------------
# SSE 拼装（非流式客户端用）
# --------------------------------------------------------------------------

def _assemble_completion(chunks: List[dict], model: str) -> dict:
    """把 OpenAI SSE 分片拼成一个完整 chat.completion。"""
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    tool_calls: Dict[int, dict] = {}
    finish_reason = "stop"
    cid = ""
    created = int(time.time())
    usage = None

    for ev in chunks:
        if not isinstance(ev, dict):
            continue
        cid = ev.get("id") or cid
        created = ev.get("created") or created
        if ev.get("usage"):
            usage = ev["usage"]
        for ch in ev.get("choices") or []:
            delta = ch.get("delta") or {}
            if isinstance(delta.get("content"), str):
                content_parts.append(delta["content"])
            if isinstance(delta.get("reasoning_content"), str):
                reasoning_parts.append(delta["reasoning_content"])
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index", 0)
                slot = tool_calls.setdefault(idx, {"id": "", "type": "function",
                                                   "function": {"name": "", "arguments": ""}})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["function"]["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["function"]["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                finish_reason = ch["finish_reason"]

    message: Dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if tool_calls:
        message["tool_calls"] = [tool_calls[k] for k in sorted(tool_calls)]

    out = {
        "id": cid or "chatcmpl-local",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
    }
    if usage:
        out["usage"] = usage
    return out


def _iter_upstream_events(resp):
    """逐行解析上游 SSE，yield (raw_line, parsed_or_None)。"""
    buffer = b""
    while True:
        chunk = resp.read(2048)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, _, buffer = buffer.partition(b"\n")
            stripped = line.strip()
            if not stripped.startswith(b"data:"):
                continue
            payload = stripped[5:].strip()
            if payload == b"[DONE]":
                yield line, None
                continue
            try:
                yield line, json.loads(payload.decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                yield line, None


def _parse_sse_payload(line: bytes) -> Optional[dict]:
    """把一行 `data: {...}` 解析成 dict；`[DONE]` 或非 JSON 返回 None。"""
    stripped = line.strip()
    if not stripped.startswith(b"data:"):
        return None
    payload = stripped[5:].strip()
    if not payload or payload == b"[DONE]":
        return None
    try:
        return json.loads(payload)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# Handler
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "WorkBuddyGateway/" + "0.1"
    protocol_version = "HTTP/1.1"

    # ---------- 工具 ----------

    def log_message(self, fmt, *args):  # 收敛默认访问日志
        log.debug("%s - %s", self.address_string(), fmt % args)

    def _json(self, status: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except BrokenPipeError:
            pass

    def _error(self, status: int, message: str, code: str = "invalid_request_error", etype: str = "invalid_request_error") -> None:
        self._json(status, {"error": {"message": message, "type": etype, "code": code}})

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _json_body(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise ValueError("请求体不是合法 JSON")

    def _is_test_request(self) -> bool:
        """回归测试打的请求带 X-Gateway-Test: 1 —— 统计时单独记账，不混进你的真实用量。"""
        return self.headers.get("X-Gateway-Test", "").strip() not in ("", "0")

    def _host_ok(self) -> bool:
        """只接受回环 Host。防止 DNS rebinding：外部页面把自己的域名解析到 127.0.0.1，
        就能以同源身份读走注入在面板里的 key。"""
        raw = (self.headers.get("Host") or "").strip().lower()
        if not raw:
            return True  # 本地命令行工具（HTTP/1.0）可能不带 Host
        host = raw
        if host.startswith("["):            # [::1]:8788
            host = host.split("]")[0] + "]"
        elif ":" in host:
            host = host.rsplit(":", 1)[0]
        return host in _ALLOWED_HOSTS

    def _authorized(self) -> bool:
        if self.path.split("?")[0] == "/healthz":
            return True
        header = self.headers.get("Authorization") or ""
        m = re.match(r"^Bearer\s+(.+)$", header.strip(), re.I)
        if not m:
            self._error(401, "缺少 Authorization: Bearer <API_KEY>", "invalid_api_key", "authentication_error")
            return False
        import hmac
        if not hmac.compare_digest(m.group(1), config.API_KEY):
            self._error(401, "API key 不正确", "invalid_api_key", "authentication_error")
            return False
        return True

    # ---------- 路由 ----------

    def do_GET(self):  # noqa: N802
        mark_test(self._is_test_request())
        if not self._host_ok():
            self._error(403, "只接受来自本机回环地址的访问", "forbidden", "permission_error")
            return
        path = self.path.split("?")[0]
        if path == "/":
            self._dashboard()
            return
        # 控制台前端的静态资源（web/ 的构建产物：带 hash 的 JS / CSS / woff2 字体）。
        # 和 / 一样不需要 key：浏览器加载页面时不会带 Authorization 头。
        # Host 校验已在上面做过。
        if path.startswith("/static/"):
            self._static(path[len("/static/"):])
            return
        if not self._authorized():
            return
        if path == "/healthz":
            self._json(200, {"ok": True, "accounts": len(POOL.list_accounts())})
        elif path == "/v1/models":
            self._json(200, {"object": "list", "data": _all_models()})
        elif path == "/admin/status":
            POOL.ensure_loaded()
            self._json(200, POOL.snapshot())
        elif path == "/admin/checkin":
            POOL.ensure_loaded()
            # auto 块是"自动签到现在什么状态"（纯内存/落盘状态，不发上游请求），
            # 前端按 account_id 关联到每个账号行上。
            self._json(200, {"accounts": POOL.checkin_overview(), **AUTO_CHECKIN.view()})
        elif path == "/admin/settings":
            self._json(200, _settings())
        elif path == "/admin/models":
            self._json(200, {"data": POOL.models_overview() + _gemini_models()})
        elif path == "/admin/log":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                limit = int((qs.get("limit") or ["50"])[0])
            except ValueError:
                limit = 50
            self._json(200, {"stats": ACTIVITY.stats(), "items": ACTIVITY.recent(limit)})
        elif path == "/admin/usage":
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            try:
                days = int((qs.get("days") or ["30"])[0])
            except ValueError:
                days = 30
            self._json(200, ACTIVITY.usage(days))
        else:
            self._error(404, f"no such route: GET {path}", "not_found")

    def do_POST(self):  # noqa: N802
        mark_test(self._is_test_request())
        if not self._host_ok():
            self._error(403, "只接受来自本机回环地址的访问", "forbidden", "permission_error")
            return
        path = self.path.split("?")[0]
        if not self._authorized():
            return
        try:
            if path == "/v1/chat/completions":
                self._chat()
            elif path == "/admin/checkin/claim":
                body = self._json_body()
                ids = body.get("account_ids") or []
                # 空请求仍然拒绝：这是"必须明确表态才能领"这条保护留下的唯一痕迹
                # （前端已经不再有勾选表单，这个接口现在服务于「立即签到」按钮与脚本）。
                if not ids and not body.get("all"):
                    self._error(400, "请显式给出 account_ids，或用 all=true 表示立即签到全部账号",
                                "confirmation_required")
                    return
                POOL.ensure_loaded()
                if ids:
                    results = POOL.claim_checkin(ids)
                    AUTO_CHECKIN.note_manual(results)
                else:
                    # all=true：走与自动调度**同一条**路径（先查状态、只领今天没领的），
                    # 而不是无脑对每个账号打一次领取接口 —— 后者对已领过的账号必然报错，
                    # 「立即签到」会得到一堆假失败。
                    out = AUTO_CHECKIN.run_once(reason="manual", with_rows=True)
                    if out is None:
                        # 自动签到被关掉（GATEWAY_AUTO_CHECKIN=0）时 run_once 直接返回 None。
                        # 此时「立即签到」仍然要能用 —— 这正是回滚后用户唯一的领取入口。
                        results = POOL.claim_checkin([])
                        AUTO_CHECKIN.note_manual(results)
                    else:
                        results = [_claim_result(r) for r in out.get("rows", [])]
                self._json(200, {"results": results, **AUTO_CHECKIN.view()})
            elif path == "/admin/reload":
                self._json(200, POOL.reload(deep=True))
            elif path == "/admin/selftest":
                self._selftest()
            else:
                self._error(404, f"no such route: POST {path}", "not_found")
        except ValueError as exc:
            self._error(400, str(exc))
        except UpstreamError as exc:
            self._error(502, exc.message, exc.kind, "upstream_error")
        except Exception as exc:  # noqa: BLE001
            log.exception("未处理异常")
            self._error(500, f"内部错误: {exc}", "internal_error", "server_error")

    def _selftest(self) -> None:
        """端到端自检：真发一条极短请求，报告链路是否通。会消耗极少量积分。"""
        body = self._json_body()
        model = body.get("model") or "deepseek-v4-flash"
        t0 = time.monotonic()
        try:
            cred = POOL.pick(model)
        except UpstreamError as exc:
            self._json(200, {"ok": False, "stage": "pick", "model": model,
                             "error": exc.message})
            return
        # 自检也真的发上游请求，同样要占一个槽位并在所有路径释放
        started = _note_started(cred)
        text, credit, tokens = "", None, 0
        try:
            try:
                resp, _, _ = upstream.open_chat_stream(
                    cred, {"model": model, "stream": True,
                           "messages": [{"role": "user", "content": "回复一个字：好"}]})
            except UpstreamError as exc:
                self._json(200, {"ok": False, "stage": "upstream", "model": model,
                                 "account": cred.uin, "error": exc.message,
                                 "kind": exc.kind})
                return

            try:
                for _, ev in _iter_upstream_events(resp):
                    if not ev:
                        continue
                    if ev.get("usage"):
                        credit = ev["usage"].get("credit")
                        tokens = int(ev["usage"].get("total_tokens") or 0)
                    for ch in ev.get("choices") or []:
                        text += (ch.get("delta") or {}).get("content") or ""
            finally:
                resp.close()
            latency = int((time.monotonic() - t0) * 1000)
            # 自检也真的花了积分，所以要记账，否则「本次消耗」会少算
            POOL.record_usage(cred, {"credit": credit, "total_tokens": tokens})
            ACTIVITY.record(model=model, account=cred.uin,
                            upstream="workbuddy (自检)", stream=True,
                            latency_ms=latency, total_ms=latency, credit=credit,
                            tokens=tokens, ok=True)
            self._json(200, {
                "ok": True, "model": model, "account": cred.uin,
                "reply": text.strip()[:40],
                "latency_ms": latency, "tokens": tokens, "credit": credit,
            })
        finally:
            # 无论成功、上游失败还是客户端断开，槽位都必须释放
            _note_finished(cred, started, credit=credit,
                           elapsed_s=time.monotonic() - t0)

    # ---------- 对话 ----------

    def _chat(self) -> None:
        t0 = time.monotonic()
        body = self._json_body()
        model = body.get("model") or ""
        if not model:
            self._error(400, "缺少 model 字段")
            return
        POOL.ensure_loaded()
        want_stream = bool(body.get("stream"))

        # --- Gemini：独立上游，与号池解耦 ---
        if _is_gemini_model(model):
            if gemini.available():
                self._chat_gemini(body, want_stream)
                return
            self._error(
                503,
                "gemini-* / agy/* 当前没有可用通道。二选一："
                "① 打开并登录 Antigravity 客户端（免费档，本机凭据直接可用，推荐）；"
                "② 在 .env 里填 GEMINI_API_KEY（AI Studio key）。"
                "注意：若已登录 Antigravity 仍报「User location is not supported」，"
                "那是出口地区问题（香港不被支持），需让 daily-cloudcode-pa.googleapis.com 走非香港节点。",
                "gemini_not_configured", "upstream_error",
            )
            return

        tried: set = set()
        last_exc: Optional[UpstreamError] = None

        for attempt in range(config.MAX_ROTATIONS):
            try:
                cred = POOL.pick(model, exclude=tried)
            except UpstreamError as exc:
                msg = (f"所有账号都失败了，最后一个错误: {last_exc.message}"
                       if last_exc is not None else exc.message)
                ACTIVITY.record(model=model, upstream="workbuddy",
                                stream=want_stream, ok=False, error=msg,
                                total_ms=int((time.monotonic() - t0) * 1000))
                self._error(503, msg, exc.kind, "upstream_error")
                return
            tried.add(cred.account_id)

            # 在飞记账：`pick()` 已经原子占好名额，这里开始就必须保证「释放恰好一次」。
            # 从占坑到下面 try 之间还有几行代码（_note_started / 构造 body），
            # 其中任何异常都会让名额永久泄漏（设计文档 §2.1 的坑 3），
            # 所以把这段也纳入 try，用 except 兜底释放。
            upstream_body = dict(body)
            started = False  # 先绑定：确保 except 分支引用它时不会 UnboundLocalError
            try:
                started = _note_started(cred)
                resp, _, _ = upstream.open_chat_stream(cred, upstream_body)
            except UpstreamError as exc:
                last_exc = exc
                retry = POOL.report_failure(cred, exc)
                log.warning("账号 %s 尝试%d失败(%s): %s", cred.uin, attempt + 1, exc.kind, exc.message[:160])
                # 失败的尝试也要进日志——失败恰恰是最该看见的
                ACTIVITY.record(
                    model=model, account=cred.uin or cred.uid,
                    upstream="workbuddy", stream=want_stream,
                    total_ms=int((time.monotonic() - t0) * 1000),
                    ok=False, error=f"[{exc.kind}] {exc.message}",
                )
                _note_finished(cred, started)
                if not retry:
                    self._error(502 if exc.kind != "client" else 400, exc.message,
                                exc.kind, "upstream_error")
                    return
                continue

            POOL.report_success(cred)
            log.info("账号 %s 命中 model=%s stream=%s", cred.uin, model, want_stream)
            ttfb_ms = int((time.monotonic() - t0) * 1000)
            credit: Optional[float] = None
            tokens = 0
            try:
                if want_stream:
                    credit, tokens = self._relay_stream(resp, cred, model)
                else:
                    credit, tokens = self._collect_and_reply(resp, cred, model)
                ACTIVITY.record(
                    model=model, account=cred.uin or cred.uid,
                    upstream="workbuddy", stream=want_stream,
                    latency_ms=ttfb_ms, total_ms=int((time.monotonic() - t0) * 1000),
                    credit=credit, tokens=tokens, ok=True,
                )
            except Exception as exc:  # noqa: BLE001
                ACTIVITY.record(
                    model=model, account=cred.uin or cred.uid,
                    upstream="workbuddy", stream=want_stream,
                    latency_ms=ttfb_ms, total_ms=int((time.monotonic() - t0) * 1000),
                    ok=False, error=str(exc),
                )
                raise
            finally:
                try:
                    resp.close()
                except Exception:  # noqa: BLE001
                    pass
                # 释放槽位 + 落盘计数。放在最外层 finally：断流/异常/正常返回全覆盖。
                _note_finished(cred, started, credit=credit,
                               elapsed_s=time.monotonic() - t0)
            return

        last_msg = last_exc.message if last_exc else "未知错误"
        ACTIVITY.record(model=model, upstream="workbuddy", stream=want_stream,
                        total_ms=int((time.monotonic() - t0) * 1000), ok=False, error=last_msg)
        self._error(503, f"换号 {config.MAX_ROTATIONS} 次仍失败: {last_msg}",
                    "all_accounts_failed", "upstream_error")

    def _chat_gemini(self, body: dict, want_stream: bool) -> None:
        """走 Gemini 独立上游（AI Studio 或本机 Antigravity 免费档）。

        上游异常必须在发响应头之前处理掉。
        """
        t0 = time.monotonic()
        model = body.get("model") or ""
        gen = gemini.stream_openai(body)
        try:
            first = next(gen)  # 真正发起请求；失败会在这里抛出
        except StopIteration:
            first = None
        except gemini.GeminiError as exc:
            status = 503 if exc.status == 503 else 502
            ACTIVITY.record(model=model, upstream="google-gemini",
                            stream=want_stream, ok=False, error=exc.message,
                            total_ms=int((time.monotonic() - t0) * 1000))
            self._error(status, exc.message, "gemini_error", "upstream_error")
            return
        ttfb_ms = int((time.monotonic() - t0) * 1000)

        if want_stream:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Gateway-Upstream", "google-gemini")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            tokens = 0
            try:
                # 注意：first 是已经取出但还没发出去的那一片，必须先写，否则会丢内容
                if first is not None:
                    self._write_chunk(first + b"\n\n")
                    first = None
                for line in gen:
                    parsed = _parse_sse_payload(line)
                    if parsed and parsed.get("usage"):
                        tokens = int(parsed["usage"].get("total_tokens") or tokens or 0)
                    self._write_chunk(line + b"\n\n")
                self._end_chunks()
            except (BrokenPipeError, ConnectionResetError):
                log.info("客户端提前断开（gemini）")
            except Exception as exc:  # noqa: BLE001
                log.warning("gemini 流式中断: %s", exc)
                try:
                    self._end_chunks()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                gen.close()
                ACTIVITY.record(model=model, upstream="google-gemini",
                                stream=True, latency_ms=ttfb_ms,
                                total_ms=int((time.monotonic() - t0) * 1000),
                                tokens=tokens, ok=True)
            return

        # 非流式：把 OpenAI 分片拼回完整响应
        chunks = []
        try:
            if first is not None:
                parsed = _parse_sse_payload(first)
                if parsed is not None:
                    chunks.append(parsed)
            for line in gen:
                parsed = _parse_sse_payload(line)
                if parsed is not None:
                    chunks.append(parsed)
        except gemini.GeminiError as exc:
            ACTIVITY.record(model=model, upstream="google-gemini",
                            ok=False, error=exc.message,
                            total_ms=int((time.monotonic() - t0) * 1000))
            self._error(502, exc.message, "gemini_error", "upstream_error")
            return
        finally:
            gen.close()
        tokens = 0
        for c in chunks:
            if c.get("usage"):
                tokens = int(c["usage"].get("total_tokens") or tokens or 0)
        ACTIVITY.record(model=model, upstream="google-gemini",
                        stream=False, latency_ms=ttfb_ms,
                        total_ms=int((time.monotonic() - t0) * 1000), tokens=tokens, ok=True)
        self._json(200, _assemble_completion(chunks, model))

    def _relay_stream(self, resp, cred, model: str):
        """标准 SSE 透传到客户端（chunked）。返回 (credit, tokens)。"""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Gateway-Account", cred.uin or cred.uid)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        credit: Optional[float] = None
        tokens = 0
        try:
            for raw, ev in _iter_upstream_events(resp):
                if ev is None and raw.strip()[5:].strip() == b"[DONE]":
                    continue  # 上游的结束标记由我们统一补发
                if isinstance(ev, dict) and ev.get("usage"):
                    POOL.record_usage(cred, ev["usage"])
                    u = ev["usage"]
                    try:
                        if u.get("credit") is not None:
                            credit = float(u["credit"])
                    except (TypeError, ValueError):
                        pass
                    tokens = int(u.get("total_tokens") or tokens or 0)
                self._write_chunk(raw.strip() + b"\n\n")
            self._write_chunk(b"data: [DONE]\n\n")
            self._end_chunks()
        except (BrokenPipeError, ConnectionResetError):
            log.info("客户端提前断开，已中断上游")
        except Exception as exc:  # noqa: BLE001
            log.warning("流式中断: %s", exc)
            try:
                self._write_chunk(b'data: {"error":{"message":"stream aborted"}}\n\n')
                self._end_chunks()
            except Exception:  # noqa: BLE001
                pass
        return credit, tokens

    def _write_chunk(self, data: bytes) -> None:
        self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
        self.wfile.flush()

    def _end_chunks(self) -> None:
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _collect_and_reply(self, resp, cred, model: str):
        """非流式客户端：内部流式取完再拼成完整 JSON。返回 (credit, tokens)。"""
        chunks = []
        credit: Optional[float] = None
        tokens = 0
        for _, ev in _iter_upstream_events(resp):
            if not ev:
                continue
            if ev.get("usage"):
                POOL.record_usage(cred, ev["usage"])
                u = ev["usage"]
                try:
                    if u.get("credit") is not None:
                        credit = float(u["credit"])
                except (TypeError, ValueError):
                    pass
                tokens = int(u.get("total_tokens") or tokens or 0)
            chunks.append(ev)
        self._json(200, _assemble_completion(chunks, model))
        return credit, tokens

    # ---------- 本地管理页 ----------

    def _static(self, rel: str) -> None:
        """面板静态资源。只允许 STATIC_DIR 之下的真实文件，挡掉目录穿越。"""
        root = STATIC_DIR.resolve()
        try:
            target = (root / urllib.parse.unquote(rel)).resolve()
        except (OSError, ValueError):
            self._error(400, "bad asset path", "invalid_request_error")
            return
        if target != root and root not in target.parents:
            self._error(403, "asset path escapes static dir", "forbidden", "permission_error")
            return
        if not target.is_file():
            self._error(404, f"no such asset: {rel}", "not_found")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        try:
            data = target.read_bytes()
        except OSError:
            self._error(404, f"no such asset: {rel}", "not_found")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def _dashboard(self) -> None:
        """面板本体。钥匙由服务端注入（已做 Host 校验，仅回环可达），打开即可用。"""
        path = STATIC_DIR / "dashboard.html"
        try:
            html = path.read_text(encoding="utf-8")
        except OSError:
            html = ("<!doctype html><meta charset=utf-8>"
                    "<h1>dashboard.html 缺失</h1>"
                    "<p>预期位置：gateway/static/dashboard.html</p>")
        # 整体替换（含引号），页面上该处写的是 "__GATEWAY_KEY__"
        html = html.replace('"__GATEWAY_KEY__"', json.dumps(config.API_KEY))
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except BrokenPipeError:
            pass


def _is_gemini_model(model: str) -> bool:
    """gemini-* 与 Antigravity 加前缀的 agy/* 都走 Gemini 独立上游。"""
    m = (model or "").lower()
    return m.startswith("gemini") or m.startswith(antigravity.AGY_PREFIX)


# --------------------------------------------------------------------------
# 轮换记账（选号 → 上游调用 → 释放槽位）
#
# 契约（docs/账号轮换策略设计.md §9）：pool 暴露
#   Pool.note_request_started(cred) / Pool.note_request_finished(cred)
# scheduler 负责实现这两个名字；这里只调用，不另造名字。
# 它们可能还没落地（跨队友并行），所以调用点全部容错：
#   记账是**优化**，绝不能因为钩子缺失把一次用户的请求打挂。
# --------------------------------------------------------------------------

def _note_started(cred) -> bool:
    """标记一次请求开始。返回是否真的记上了（供 release 判断）。"""
    fn = getattr(POOL, "note_request_started", None)
    if not callable(fn):
        return False
    try:
        fn(cred)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("note_request_started 异常（已忽略）: %s", exc)
        return False


def _note_finished(cred, started: bool, credit: Optional[float] = None,
                   elapsed_s: float = 0.0) -> None:
    """释放槽位 + 更新跨重启累计计数 + 节流落盘。

    **必须在 finally 中调用**：漏掉任意一条路径都会让该账号的在飞计数永久 +1，
    池子被逐渐"缩小"直到没有可用账号（设计文档 §2 I4）。

    **释放无条件于 `started`**（设计文档 §2.1 的坑 2）：
    `pick()` 已经原子占好名额，而 `_note_started` 在钩子缺失/抛异常时会返回 False，
    此时若跳过释放，槽位就永久泄漏——实测 3 账号 × cap 10 后 `pick()` 永远返回 None。
    释放本身是幂等且 claim-aware 的（只释放真正占着的名额，不会偷走别的在飞请求的槽位），
    所以无条件调用是安全的。
    """
    fn = getattr(POOL, "note_request_finished", None)
    if callable(fn):
        try:
            fn(cred)
        except Exception as exc:  # noqa: BLE001
            log.warning("note_request_finished 异常（已忽略）: %s", exc)
    # 累计计数（跨重启，设计文档 §5）。last_used_wall 用 wall clock，
    # 与调度器进程内的 monotonic last_used_at 各管一段。
    try:
        cred.requests_total = int(getattr(cred, "requests_total", 0)) + 1
        cred.last_used_wall = time.time()
        if credit is not None:
            cred.credits_spent_total = (
                float(getattr(cred, "credits_spent_total", 0.0)) + float(credit))
    except Exception as exc:  # noqa: BLE001
        log.warning("累计计数更新失败（已忽略）: %s", exc)
        return
    # 把这次真实消耗喂给调度器的 EMA，runway（设计文档 §3 第 3 层）才真正生效。
    # 不喂的话 ema_cost 恒为 0，runway 对所有账号都退化到 MIN_RATE，
    # 于是 runway_share 退化成 credit_share 排序——G3「先烧零头」不起作用。
    # 只对真实消耗调用（credit 非 0），避免把免费/自检请求算成高消费。
    if credit:
        try:
            fn_usage = getattr(POOL, "note_usage", None)
            if callable(fn_usage):
                fn_usage(cred, float(credit), float(elapsed_s or 0.0))
        except Exception as exc:  # noqa: BLE001
            log.warning("轮换 EMA 更新失败（已忽略）: %s", exc)
    # 延迟样本：让 Layer 2 的宽度 `max(SLICE_S, 2 × p95)` 用上真实数据。
    if elapsed_s:
        try:
            fn_lat = getattr(POOL, "note_latency", None)
            if callable(fn_lat):
                fn_lat(cred, float(elapsed_s))
        except Exception as exc:  # noqa: BLE001
            log.warning("轮换延迟更新失败（已忽略）: %s", exc)
    # 热路径**不带 force**：靠节流避免每个请求重写文件；
    # 后台 keeper() 与退出路径会 force 刷，磁盘状态不会落后太久。
    if getattr(cred, "persistable", True):
        try:
            creds.save_store(POOL._creds)
        except Exception as exc:  # noqa: BLE001
            log.warning("计数落盘失败（已忽略）: %s", exc)


def _flush_counters(why: str) -> None:
    """强制把累计计数刷盘。keeper() 周期调用与进程退出时调用。"""
    try:
        creds.save_store(POOL._creds, force=True)
        log.info("累计计数已刷盘（%s）", why)
    except Exception as exc:  # noqa: BLE001
        log.warning("累计计数刷盘失败（%s）: %s", why, exc)


def _all_models() -> List[dict]:
    """WorkBuddy 目录 + Gemini 目录（若已配置）。"""
    models = POOL.all_model_ids()
    if gemini.available():
        seen = {m["id"] for m in models}
        models.extend(m for m in gemini.list_models() if m["id"] not in seen)
    return models


def _gemini_models() -> List[dict]:
    """面板用：把 Gemini / Antigravity 的模型并进同一份清单。"""
    if not gemini.available():
        return []
    return [
        {"id": m["id"], "name": m["id"],
         "context_window": None, "max_tokens": None, "credits": None, "cost": None,
         "owned_by": m.get("owned_by") or "google-gemini"}
        for m in gemini.list_models()
    ]


def _claim_result(row: dict) -> dict:
    """把自动签到一轮里的账号明细，翻译成 `/admin/checkin/claim` 的既有响应形状。

    前端与既有脚本读的是 `{account_id, ok, credited?, reason?}`，这里保持它不变；
    多带一个 `action` 让人能区分"真的领到了"和"今天本来就领过"——
    两者 ok 都是 True，但前者是消息，后者不该被读成"刚刚又领了一份"。
    """
    action = str(row.get("action") or "")
    out: Dict[str, Any] = {"account_id": row.get("account_id"), "uin": row.get("uin"),
                           "action": action}
    if action == "claimed":
        out.update({"ok": True, "credited": row.get("credited")})
    elif action in ("already", "already_local"):
        out.update({"ok": True, "reason": "今天已经领过"})
    else:
        out.update({"ok": False, "reason": row.get("error") or action or "未知结果"})
    return out


def _settings() -> dict:
    """面板「接入」区需要的非敏感配置。**不含 API key。**"""
    base = f"http://{config.HOST}:{config.PORT}"
    return {
        "version": GATEWAY_VERSION,
        "host": config.HOST,
        "port": config.PORT,
        "base_url": base + "/v1",
        "dashboard_url": base + "/",
        "models_total": len(_all_models()),
        "accounts": POOL.account_count(),
        "gemini": {**gemini.backend(), "configured": gemini.available()},
        "limits": {
            "max_rotations": config.MAX_ROTATIONS,
            "rate_limit_cooldown_s": config.RATE_LIMIT_COOLDOWN_S,
            "refresh_margin_h": config.REFRESH_MARGIN_MS // 3600000,
            "upstream_timeout_s": config.UPSTREAM_TIMEOUT,
        },
        # 自动签到的节奏（面板要能回答"它到底会不会自己领、多久一次"）
        "checkin": {
            "auto_enabled": config.AUTO_CHECKIN_ENABLED,
            "interval_s": config.AUTO_CHECKIN_INTERVAL_S,
            "jitter_s": config.AUTO_CHECKIN_JITTER_S,
            "account_gap_s": config.AUTO_CHECKIN_ACCOUNT_GAP_S,
        },
        "paths": {
            "state": str(config.STATE_DIR),
            "desktop_creds": str(config.DESKTOP_AUTH_DIR),
            "log": str(config.LOG_FILE),
        },
    }


# --------------------------------------------------------------------------
# 管理页（纯本地：key 存在浏览器 localStorage，不写进页面）
# --------------------------------------------------------------------------


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------

def _setup_logging() -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    handlers: List[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.FileHandler(config.LOG_FILE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        handlers=handlers,
    )


def serve() -> int:
    """真正跑起来的那个函数：建 server、起后台线程、阻塞在 serve_forever。

    它被拆出来是为了**开发热重载**：hupper 需要在子进程里通过一个
    "可导入的路径"（`gateway.server.serve`）来启动 worker，
    所以这里必须是模块级函数，且不能带必需参数。
    """
    _setup_logging()
    api_key = config.ensure_api_key()
    log.info("网关启动：http://%s:%d", config.HOST, config.PORT)
    log.info("API key：%s…%s（完整值见 .env）", api_key[:9], api_key[-4:])
    log.info("base_url 填：http://%s:%d/v1", config.HOST, config.PORT)

    POOL.reload(deep=True)
    snap = POOL.snapshot()
    for a in snap["accounts"]:
        log.info("账号 %s (%s) 可用=%s token至=%s",
                 a["uin"], a["domain"], a["available"], a["expires_at"])

    # 绑定端口。这里必须给出**人话错误**而不是让它抛 traceback。
    #
    # 起因是一次真实事故：同时有 4 个 --dev 监视器残留（每个都会重启自己的 worker），
    # 每次改文件 4 个 worker 抢同一个端口，输的那个抛 OSError 栈。
    # 现在既做单实例锁（见 _start_dev_reloader），也把这里的报错写清楚。
    try:
        server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    except OSError as exc:
        already = "Address already in use" in str(exc) or getattr(exc, "errno", None) in (48, 98)
        if already:
            log.error(
                "端口 %d 已被占用：本机已经有一个网关在跑。\n"
                "  · 先看是谁占着： lsof -nP -iTCP:%d -sTCP:LISTEN\n"
                "  · 若要换成开发热重载，请先停掉它： ./stop.sh\n"
                "  · 常见原因：此前留下的 --dev 监视器没退干净（一个监视器就会重启出 worker）",
                config.PORT, config.PORT,
            )
        else:
            log.error("绑定 %s:%d 失败：%s", config.HOST, config.PORT, exc)
        return 1

    server.daemon_threads = True

    # 后台定时续期 + 刷新积分，避免 token 过期、也让号池排序用上最新余额
    def keeper():
        while True:
            time.sleep(1800)
            try:
                POOL.refresh_all_tokens()
            except Exception as exc:  # noqa: BLE001
                log.warning("后台续期异常: %s", exc)
            try:
                POOL.refresh_credits()
            except Exception as exc:  # noqa: BLE001
                log.warning("后台积分刷新异常: %s", exc)
            # 累计计数强制刷盘：热路径是节流写的，这里保证磁盘状态不落后太多
            _flush_counters("keeper 周期")

    threading.Thread(target=keeper, daemon=True).start()

    # 全自动签到：自己的线程，与 keeper 分开。
    # 为什么不塞进 keeper：keeper 的循环是 1800 秒一跳，而签到需要"启动后不久
    # 就先跑一轮"（否则刚开机的这一天要等半小时才领）。两者的节奏不同，混在一起
    # 会让 keeper 的周期被签到的轮询牵着走。
    AUTO_CHECKIN.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("收到中断，退出")
    finally:
        server.server_close()
        # 退出前强制刷一次，把节流窗口内未落盘的计数补上（设计文档 §5）
        _flush_counters("进程退出")
    return 0


def _dev_enabled() -> bool:
    """是否处于开发热重载模式。

    两种开启方式：命令行 `--dev`（由 run.sh 透传），或环境变量 `GATEWAY_DEV=1`。
    默认**关闭** —— 生产路径因此不引入任何第三方依赖。
    """
    if os.environ.get("GATEWAY_DEV", "").strip() in ("1", "true", "yes"):
        return True
    return "--dev" in sys.argv


def _install_devdeps() -> None:
    """把项目内的 .devdeps 加进 import 路径。

    为什么不用 pip 装到解释器的 site-packages：打包运行时的 site-packages
    是**只读**的（实测 `os.access(..., W_OK) == False`），而且那属于 DSH 运行时的地盘，
    往上装东西会污染它、并且下次运行时更新就被抹掉。
    放在项目自己的 `.devdeps/` 里，删掉即彻底还原，也不进版本库。
    """
    devdeps = Path(__file__).resolve().parent.parent / ".devdeps"
    if devdeps.is_dir():
        p = str(devdeps)
        if p not in sys.path:
            sys.path.insert(0, p)


_DEV_LOCK = None  # 开发模式单实例锁的文件对象（必须是模块级，见下文注释）


def _acquire_single_instance_lock(lock_path: Optional[Path] = None):
    """开发模式的单实例锁：同一时刻只允许一个 hupper 监视器。

    这是被一次真实事故逼出来的：测试过程中残留了 4 个 `--dev` 监视器，
    每个监视器都会在文件变化时重启自己的 worker，于是 4 个 worker 抢同一个端口，
    3 个以 `OSError: Address already in use` 崩溃 —— 表现就是"进程总是崩溃"。

    监视器很难靠 `pkill` 记得清（worker 才是占端口那个，杀 worker 不会带走监视器），
    所以必须让第二个监视器**自己拒绝启动**，而不是让它起来后跟别人抢。

    返回持锁的文件对象（必须一直持有，不能关闭，否则锁会释放）；拿不到锁返回 None。
    ``lock_path`` 可传入以便测试；默认放在 `state/`（该目录不进版本库）。
    """
    try:
        import fcntl
    except ImportError:
        return None  # 非 POSIX 平台不做保护，放行

    lock_path = lock_path or (Path(__file__).resolve().parent.parent / "state" / ".dev-reload.lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # ⚠️ 必须用 O_RDWR|O_CREAT，**不能**用 open("w")。
        # "w" 会在拿锁之前就把文件截断成 0 字节 ——
        # 于是后来者会把正在运行者的 PID 抹掉，报错信息里就读不到对方是谁。
        # （这个 bug 真出现过：锁文件一直是 0 字节。）
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fh = os.fdopen(fd, "r+")
    except OSError:
        return None

    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # 已被占用：读出对方 PID 便于用户处理
        owner = ""
        try:
            owner = fh.read().strip()
        except OSError:
            pass
        fh.close()
        print(
            "❌ 已经有一个 --dev 实例在运行"
            + (f"，PID {owner}" if owner else "")
            + "。\n"
            "   同时开两个会让两个 worker 抢 8788，输的那个直接崩溃。\n"
            "   停止它： ./stop.sh\n"
            "   查残留： pgrep -fl 'gateway.server'",
            file=sys.stderr,
            flush=True,
        )
        return None

    # 拿到锁后写入自己的 PID（先清空再写，避免残留更长的旧 PID）
    try:
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
    except OSError:
        pass
    return fh


def _start_dev_reloader() -> int:
    """开发模式：用 hupper 监视源码，改动即自动重启 worker。

    为什么用 hupper 而不是自己写轮询循环：
      · 它是 Pylons 出品、Pyramid 官方在用的**通用**重载器（框架无关）；
      · `monitor_factory=None` 时它会自动优选 watchdog / watchman
        （装了就用事件通知，毫秒级触发），都没有则回落轮询 —— 这套逻辑不必自己写；
      · 自己写最容易踩的坑是"监视了整个目录"。

    ⚠️ 关于监视范围：hupper 默认监视**当前工作目录**（整个项目），
    而网关每次请求都会写 `state/usage.jsonl`、`gateway.log`。
    那会导致「写文件 → 判定为改动 → 重启 → 又写文件」的无限重启循环。
    所以这里必须显式忽略掉所有运行时产物目录（state/ 与 .devdeps/）。
    """
    _install_devdeps()
    try:
        import hupper
    except ImportError:
        print(
            "❌ 开启了 --dev（或 GATEWAY_DEV=1）但没找到 hupper。\n"
            "   安装（只需一次，只装在项目内的 .devdeps/，不影响运行时）：\n"
            "     ./run.sh --dev-install\n"
            "   或者不带 --dev 启动 —— 生产路径零第三方依赖。",
            file=sys.stderr,
        )
        return 2

    if hupper.is_active():
        # 已经在受监视的 worker 里了（hupper 会重新执行本模块），
        # 此时应当直接跑服务，而不是再套一层监视器。
        # 注意：worker **不**取单实例锁 —— 锁由监视器持有，
        # 否则每次重载重启 worker 都会撞上自己。
        return serve()

    # 单实例：第二个监视器直接拒绝启动（详见函数注释里的那次事故）。
    # 赋给**模块级**变量而不是局部变量，确保锁的文件对象在整个监视器生命周期内
    # 都有强引用、不会被 GC 回收掉（文件一关闭锁就释放了）。
    global _DEV_LOCK
    _DEV_LOCK = _acquire_single_instance_lock()
    if _DEV_LOCK is None:
        return 3

    # ⚠️ 强制用轮询监视器，**不要**用 watchdog。
    #
    # 这不是保守，是实测结论：macOS 上 watchdog 只能退到 kqueue
    # （fsevents 后端没装成功，日志会打印 "Failed to import fsevents"），
    # 而 kqueue 的文件描述符在 worker 重启后失效 —— 表现为
    # **只有第一次修改会触发重载，之后无论怎么改都没反应**。
    # 轮询监视器实测连续 4 次修改 4 次重载，稳定。
    #
    # 代价是 1 秒的轮询间隔（reload_interval），对本地开发完全可接受，
    # 而"改了没反应、以为代码生效了其实没有"是远比 1 秒延迟严重的问题。
    os.environ.setdefault("HUPPER_DEFAULT_MONITOR", "hupper.polling.PollingFileMonitor")

    # flush=True 很关键：stdout 不是终端时（`nohup ./dev.sh > log`）是**块缓冲**的，
    # 不 flush 的话这些提示会一直卡在缓冲区里，进程活着就永远看不到 ——
    # 用户会以为"热重载没生效"。实测踩过。
    print("🔥 开发热重载已开启", flush=True)
    print("   · 监视 gateway/**/*.py，保存即自动重启（轮询，最多 1s 延迟）", flush=True)
    print("   · 已忽略 state/ 与 .devdeps/（否则写日志会触发自我重启）", flush=True)
    print("   · 单实例锁已持有（state/.dev-reload.lock）", flush=True)
    print("   · 前端 HMR 请用 ./dev.sh，或另开终端：cd web && pnpm run dev", flush=True)

    hupper.start_reloader(
        "gateway.server.serve",
        reload_interval=1,
        # 只看 Python 源码，忽略运行时产物与依赖目录。
        # 注意模式是相对路径的 shell 风格通配，不是绝对前缀。
        ignore_files=[
            "state/*",
            "state/**",
            ".devdeps/*",
            ".devdeps/**",
            "*.jsonl",
            "*.log",
            "web/node_modules/*",
            "web/node_modules/**",
            "gateway/static/*",
            "gateway/static/**",
        ],
    )
    return 0


def main() -> int:
    if _dev_enabled():
        return _start_dev_reloader()
    return serve()


if __name__ == "__main__":
    raise SystemExit(main())
