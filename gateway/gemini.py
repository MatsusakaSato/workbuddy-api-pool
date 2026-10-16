"""Gemini 通道。

Gemini 是**独立的一条上游**，与 WorkBuddy 账号池解耦。两种凭据二选一：

  ① `GEMINI_API_KEY`（AI Studio 官方 key）—— 走 generativelanguage.googleapis.com
  ② 本机 Antigravity 客户端的登录凭据 —— 走 daily-cloudcode-pa.googleapis.com（免费档）
     详见 antigravity.py。两条都没有时，路由会退回 WorkBuddy 国际版账号。

历史上 ② 曾被判定不可用（403 SUBSCRIPTION_REQUIRED / UNSUPPORTED_CLIENT）。**该结论已作废**：
打开 Antigravity 客户端完成 provisioning 后，免费档即为 currentTier，可正常生成内容。
当时真正的失败原因是**出口地区**（香港不被 Gemini 支持），换非香港节点即通。

协议转换：OpenAI Chat Completions  <->  Gemini generateContent
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Dict, Iterator, List, Optional

from . import config

log = logging.getLogger("gateway.gemini")

STREAM_SUFFIX = ":streamGenerateContent"
FINISH_MAP = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "OTHER": "stop",
}


class GeminiError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


def available() -> bool:
    if config.GEMINI_API_KEY:
        return True
    from . import antigravity  # 延迟导入，避免与 antigravity.py 循环
    return antigravity.available()


def backend() -> Dict[str, str]:
    """当前**实际生效**的 Gemini 后端。

    面板必须显示真实上游，而不是「配了哪个变量」——否则会出现
    界面写着 generativelanguage.googleapis.com、实际却走 Antigravity 的错信息。
    """
    if config.GEMINI_API_KEY:
        return {"name": "aistudio", "base": config.GEMINI_BASE}
    from . import antigravity
    if antigravity.available():
        return {"name": "antigravity", "base": antigravity.ENDPOINT}
    return {"name": "none", "base": ""}


def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": config.GEMINI_API_KEY,
        "Accept": "application/json",
    }


def _url(model: str, method: str = STREAM_SUFFIX) -> str:
    name = model.split("/", 1)[1] if model.startswith("models/") else model
    return f"{config.GEMINI_BASE}/models/{name}{method}?alt=sse"


# --------------------------------------------------------------------------
# OpenAI -> Gemini
# --------------------------------------------------------------------------

def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(part.get("text") or "")
            elif isinstance(part, str):
                out.append(part)
        return "".join(out)
    return ""


def to_gemini_body(body: dict) -> dict:
    """把 OpenAI 请求体翻译成 Gemini generateContent 请求体。"""
    contents: List[dict] = []
    system_parts: List[dict] = []

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        text = _text_of(msg.get("content"))
        if role == "system":
            if text:
                system_parts.append({"text": text})
            continue
        # Gemini 只有 user / model 两个角色
        grole = "model" if role == "assistant" else "user"
        if not text:
            continue
        contents.append({"role": grole, "parts": [{"text": text}]})

    gen: Dict[str, object] = {}
    for src, dst in (("temperature", "temperature"), ("top_p", "topP"),
                     ("max_tokens", "maxOutputTokens"),
                     ("max_completion_tokens", "maxOutputTokens")):
        if isinstance(body.get(src), (int, float)):
            gen[dst] = body[src]
    if isinstance(body.get("stop"), (str, list)):
        stops = [body["stop"]] if isinstance(body["stop"], str) else body["stop"]
        gen["stopSequences"] = [s for s in stops if isinstance(s, str)][:5]

    out: Dict[str, object] = {"contents": contents}
    if system_parts:
        out["systemInstruction"] = {"parts": system_parts}
    if gen:
        out["generationConfig"] = gen
    return out


# --------------------------------------------------------------------------
# Gemini SSE -> OpenAI SSE
# --------------------------------------------------------------------------

def _chunk(cid: str, model: str, created: int, delta: dict, finish: Optional[str]) -> bytes:
    payload = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8")


def stream_openai(body: dict) -> Iterator[bytes]:
    """调用 Gemini 流式接口，产出 **OpenAI 格式** 的 SSE 行（不含结尾空行）。"""
    if not config.GEMINI_API_KEY:
        from . import antigravity
        if antigravity.available():
            yield from antigravity.stream_openai(body)
            return
    if not available():
        raise GeminiError("未配置 GEMINI_API_KEY（也没有可用的 Antigravity 凭据）", 503)
    model = body.get("model") or ""
    payload = to_gemini_body(body)
    req = urllib.request.Request(
        _url(model), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_headers(), method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=config.UPSTREAM_TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise GeminiError(f"Gemini HTTP {exc.code}: {detail}", exc.code) from exc
    except Exception as exc:  # noqa: BLE001
        raise GeminiError(f"Gemini 连接失败: {exc}") from exc

    cid = "chatcmpl-gemini-" + str(int(time.time() * 1000))
    created = int(time.time())
    sent_role = False
    try:
        yield _chunk(cid, model, created, {"role": "assistant", "content": ""}, None)
        sent_role = True
        buffer = b""
        while True:
            chunk = resp.read(2048)
            if not chunk:
                break
            buffer += chunk
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                line = line.strip()
                if not line.startswith(b"data:"):
                    continue
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    doc = json.loads(raw.decode("utf-8", "replace"))
                except Exception:  # noqa: BLE001
                    continue
                for cand in doc.get("candidates") or []:
                    parts = ((cand.get("content") or {}).get("parts")) or []
                    text = "".join(p.get("text") or "" for p in parts if isinstance(p, dict))
                    if text:
                        yield _chunk(cid, model, created, {"content": text}, None)
                    reason = cand.get("finishReason")
                    if reason:
                        yield _chunk(cid, model, created, {}, FINISH_MAP.get(reason, "stop"))
                usage = doc.get("usageMetadata")
                if usage and usage.get("candidatesTokenCount") is not None:
                    meta = {
                        "id": cid, "object": "chat.completion.chunk", "created": created,
                        "model": model, "choices": [],
                        "usage": {
                            "prompt_tokens": usage.get("promptTokenCount", 0),
                            "completion_tokens": usage.get("candidatesTokenCount", 0),
                            "total_tokens": usage.get("totalTokenCount", 0),
                        },
                    }
                    yield b"data: " + json.dumps(meta, ensure_ascii=False).encode("utf-8")
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
    if not sent_role:
        yield _chunk(cid, model, created, {"role": "assistant", "content": ""}, None)
    yield b"data: [DONE]"


_MODELS_CACHE: Dict[str, object] = {"at": 0.0, "data": []}
_MODELS_TTL_S = 600


def list_models(force: bool = False) -> List[dict]:
    """列出可用的 Gemini 模型（带 10 分钟缓存）。失败返回空列表。"""
    if not config.GEMINI_API_KEY:
        from . import antigravity
        if antigravity.available():
            return antigravity.list_models(force)
        return []
    now = time.time()
    if not force and _MODELS_CACHE["data"] and now - float(_MODELS_CACHE["at"]) < _MODELS_TTL_S:
        return list(_MODELS_CACHE["data"])  # type: ignore[arg-type]
    req = urllib.request.Request(config.GEMINI_BASE + "/models?pageSize=200",
                                 headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            doc = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        log.debug("Gemini 模型列表拉取失败：%s", exc)
        return list(_MODELS_CACHE["data"])  # type: ignore[arg-type]
    out = []
    for m in doc.get("models") or []:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or ""
        methods = m.get("supportedGenerationMethods") or []
        if "generateContent" not in methods:
            continue
        mid = name.split("/", 1)[1] if name.startswith("models/") else name
        if not mid:
            continue
        out.append({"id": mid, "object": "model", "created": 0, "owned_by": "google-gemini"})
    _MODELS_CACHE["at"] = now
    _MODELS_CACHE["data"] = out
    log.info("Gemini 模型目录：%d 个", len(out))
    return out


__all__ = ["GeminiError", "available", "stream_openai", "list_models", "to_gemini_body"]
