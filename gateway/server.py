"""HTTP 层：OpenAI 兼容路由 + 本地管理页。仅绑 127.0.0.1，纯标准库。"""
from __future__ import annotations

import json
import logging
import mimetypes
import re
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import config, gemini, upstream
from . import __version__ as GATEWAY_VERSION
from . import antigravity
from .activity import ACTIVITY, mark_test
from .pool import Pool
from .upstream import UpstreamError

log = logging.getLogger("gateway.server")

POOL = Pool()
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
            self._json(200, {"accounts": POOL.checkin_overview()})
        elif path == "/admin/settings":
            self._json(200, _settings())
        elif path == "/admin/models":
            self._json(200, {"data": POOL.models_by_region() + _gemini_models()})
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
                if not ids and not body.get("all"):
                    self._error(400, "半自动模式：请显式给出 account_ids，或用 all=true 明确表示领全部",
                                "confirmation_required")
                    return
                POOL.ensure_loaded()
                self._json(200, {"results": POOL.claim_checkin(ids)})
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
        try:
            resp, _, _ = upstream.open_chat_stream(
                cred, {"model": _strip_region(model), "stream": True,
                       "messages": [{"role": "user", "content": "回复一个字：好"}]})
        except UpstreamError as exc:
            self._json(200, {"ok": False, "stage": "upstream", "model": model,
                             "account": cred.uin, "error": exc.message,
                             "kind": exc.kind})
            return

        text, credit, tokens = "", None, 0
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
        ACTIVITY.record(model=_strip_region(model), account=cred.uin,
                        upstream="workbuddy-" + cred.region + " (自检)", stream=True,
                        latency_ms=latency, total_ms=latency, credit=credit,
                        tokens=tokens, ok=True)
        self._json(200, {
            "ok": True, "model": model, "account": cred.uin,
            "region": cred.region, "reply": text.strip()[:40],
            "latency_ms": latency, "tokens": tokens, "credit": credit,
        })

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
            if not _has_global_account():
                self._error(
                    503,
                    "gemini-* / agy/* 当前没有可用通道。三选一："
                    "① 打开并登录 Antigravity 客户端（免费档，本机凭据直接可用，推荐）；"
                    "② 在 .env 里填 GEMINI_API_KEY（AI Studio key）；"
                    "③ 登录一个 WorkBuddy 国际版账号后点 /admin/reload。"
                    "注意：若已登录 Antigravity 仍报「User location is not supported」，"
                    "那是出口地区问题（香港不被支持），需让 daily-cloudcode-pa.googleapis.com 走非香港节点。",
                    "gemini_not_configured", "upstream_error",
                )
                return
            # 有国际版账号则退回 WorkBuddy 通道

        tried: set = set()
        last_exc: Optional[UpstreamError] = None

        for attempt in range(config.MAX_ROTATIONS):
            try:
                cred = POOL.pick(model, exclude=tried)
            except UpstreamError as exc:
                msg = (f"所有账号都失败了，最后一个错误: {last_exc.message}"
                       if last_exc is not None else exc.message)
                ACTIVITY.record(model=_strip_region(model), upstream="workbuddy",
                                stream=want_stream, ok=False, error=msg,
                                total_ms=int((time.monotonic() - t0) * 1000))
                self._error(503, msg, exc.kind, "upstream_error")
                return
            tried.add(cred.account_id)

            upstream_body = dict(body)
            upstream_body["model"] = _strip_region(model)
            try:
                resp, _, _ = upstream.open_chat_stream(cred, upstream_body)
            except UpstreamError as exc:
                last_exc = exc
                retry = POOL.report_failure(cred, exc)
                log.warning("账号 %s 尝试%d失败(%s): %s", cred.uin, attempt + 1, exc.kind, exc.message[:160])
                # 失败的尝试也要进日志——失败恰恰是最该看见的
                ACTIVITY.record(
                    model=_strip_region(model), account=cred.uin or cred.uid,
                    upstream="workbuddy-" + cred.region, stream=want_stream,
                    total_ms=int((time.monotonic() - t0) * 1000),
                    ok=False, error=f"[{exc.kind}] {exc.message}",
                )
                if not retry:
                    self._error(502 if exc.kind != "client" else 400, exc.message,
                                exc.kind, "upstream_error")
                    return
                continue

            POOL.report_success(cred)
            log.info("账号 %s 命中 model=%s stream=%s", cred.uin, model, want_stream)
            ttfb_ms = int((time.monotonic() - t0) * 1000)
            try:
                if want_stream:
                    credit, tokens = self._relay_stream(resp, cred, model)
                else:
                    credit, tokens = self._collect_and_reply(resp, cred, model)
                ACTIVITY.record(
                    model=_strip_region(model), account=cred.uin or cred.uid,
                    upstream="workbuddy-" + cred.region, stream=want_stream,
                    latency_ms=ttfb_ms, total_ms=int((time.monotonic() - t0) * 1000),
                    credit=credit, tokens=tokens, ok=True,
                )
            except Exception as exc:  # noqa: BLE001
                ACTIVITY.record(
                    model=_strip_region(model), account=cred.uin or cred.uid,
                    upstream="workbuddy-" + cred.region, stream=want_stream,
                    latency_ms=ttfb_ms, total_ms=int((time.monotonic() - t0) * 1000),
                    ok=False, error=str(exc),
                )
                raise
            finally:
                try:
                    resp.close()
                except Exception:  # noqa: BLE001
                    pass
            return

        last_msg = last_exc.message if last_exc else "未知错误"
        ACTIVITY.record(model=_strip_region(model), upstream="workbuddy", stream=want_stream,
                        total_ms=int((time.monotonic() - t0) * 1000), ok=False, error=last_msg)
        self._error(503, f"换号 {config.MAX_ROTATIONS} 次仍失败: {last_msg}",
                    "all_accounts_failed", "upstream_error")

    def _chat_gemini(self, body: dict, want_stream: bool) -> None:
        """走 Google AI Studio。上游异常必须在发响应头之前处理掉。"""
        t0 = time.monotonic()
        upstream_body = dict(body)
        upstream_body["model"] = _strip_region(body.get("model") or "")
        gen = gemini.stream_openai(upstream_body)
        try:
            first = next(gen)  # 真正发起请求；失败会在这里抛出
        except StopIteration:
            first = None
        except gemini.GeminiError as exc:
            status = 503 if exc.status == 503 else 502
            ACTIVITY.record(model=upstream_body["model"], upstream="google-gemini",
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
                ACTIVITY.record(model=upstream_body["model"], upstream="google-gemini",
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
            ACTIVITY.record(model=upstream_body["model"], upstream="google-gemini",
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
        ACTIVITY.record(model=upstream_body["model"], upstream="google-gemini",
                        stream=False, latency_ms=ttfb_ms,
                        total_ms=int((time.monotonic() - t0) * 1000), tokens=tokens, ok=True)
        self._json(200, _assemble_completion(chunks, upstream_body["model"]))

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


def _strip_region(model: str) -> str:
    if model.startswith("cn/") or model.startswith("global/"):
        return model.split("/", 1)[1]
    return model


def _is_gemini_model(model: str) -> bool:
    m = _strip_region(model).lower()
    return m.startswith("gemini") or m.startswith(antigravity.AGY_PREFIX)


def _has_global_account() -> bool:
    return any(c.region == "global" for c in POOL.list_accounts())


def _all_models() -> List[dict]:
    """WorkBuddy 各区域目录 + Gemini 目录（若已配置）。"""
    models = POOL.all_model_ids()
    if gemini.available():
        seen = {m["id"] for m in models}
        models.extend(m for m in gemini.list_models() if m["id"] not in seen)
    return models


def _gemini_models() -> List[dict]:
    if not gemini.available():
        return []
    return [
        {"id": m["id"], "name": m["id"], "region": "global",
         "context_window": None, "max_tokens": None, "credits": None, "cost": None,
         "owned_by": m.get("owned_by") or "google-gemini"}
        for m in gemini.list_models()
    ]


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


def main() -> int:
    _setup_logging()
    api_key = config.ensure_api_key()
    log.info("网关启动：http://%s:%d", config.HOST, config.PORT)
    log.info("API key：%s…%s（完整值见 .env）", api_key[:9], api_key[-4:])
    log.info("base_url 填：http://%s:%d/v1", config.HOST, config.PORT)

    POOL.reload(deep=True)
    snap = POOL.snapshot()
    for a in snap["accounts"]:
        log.info("账号 %s (%s) 可用=%s token至=%s",
                 a["uin"], a["region"], a["available"], a["expires_at"])

    server = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
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

    threading.Thread(target=keeper, daemon=True).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("收到中断，退出")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
