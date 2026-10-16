"""Antigravity（Google Code Assist 免费档）Gemini 通道。

为什么需要它：本机那个 Google 账号走 AI Studio 需要自备 API key，而 Antigravity
客户端登录后拿到的 Code Assist 免费档（free-tier）本身就是一条可用的 Gemini 上游。
既然客户端已经把 token 放在本地了，网关直接借用即可，不必再申请 key。

两条硬约束（都是实测结论，别踩）：
  1. **出口必须是受支持地区**。香港出口会得到 400「User location is not supported」。
     解决办法是让 daily-cloudcode-pa.googleapis.com 走非香港节点（见 tools/clash_gemini_route.py）。
  2. 免费档**配额有限**。gemini-2.5-pro 经常 503 无容量 / 429 配额耗尽，flash 系列稳定。

我们**不修改客户端自己的 token 文件**；刷新后的 token 只写进网关自己的 state 目录。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from . import config

log = logging.getLogger("gateway.antigravity")

#: 非 Gemini 的 Antigravity 模型（如 claude-*）对外加此前缀，避免与 WorkBuddy 号池同名模型混淆
AGY_PREFIX = "agy/"

ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UA = "antigravity/2.23.1"

#: Antigravity 客户端 token 文件（只读，不动它）
APP_TOKEN_PATH = Path.home() / ".gemini" / "jetski-standalone-oauth-token"
#: 我们自己刷新后的 token 缓存
CACHE_PATH = config.STATE_DIR / "antigravity_token.json"

#: installed-app 类型的公开客户端凭据（客户端二进制里就是这个，非机密）
CLIENT_ID = os.environ.get("ANTIGRAVITY_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("ANTIGRAVITY_CLIENT_SECRET", "").strip()

#: IDE 内部用的模型，不对普通对话开放
_INTERNAL_PREFIXES = ("chat_", "tab_", "tab-")


class AntigravityError(Exception):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.message = message
        self.status = status


# --------------------------------------------------------------------------
# 凭据
# --------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _app_token() -> Tuple[str, str, float]:
    """返回 (access_token, refresh_token, 过期时间戳)。"""
    doc = _read_json(APP_TOKEN_PATH) or {}
    tok = doc.get("token") or {}
    at = tok.get("access_token") or ""
    rt = tok.get("refresh_token") or ""
    exp = 0.0
    raw = tok.get("expiry")
    if isinstance(raw, str):
        try:
            exp = datetime.fromisoformat(raw).timestamp()
        except Exception:  # noqa: BLE001
            exp = 0.0
    return at, rt, exp


def _client_id_from_id_token(doc: dict) -> str:
    """从 id_token 里取 aud，比硬编码更抗客户端升级。"""
    try:
        payload = str(doc.get("id_token") or "").split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("aud") or CLIENT_ID
    except Exception:  # noqa: BLE001
        return CLIENT_ID


def _load_cache() -> Optional[dict]:
    doc = _read_json(CACHE_PATH)
    if doc and doc.get("access_token") and float(doc.get("expires_at") or 0) > time.time() + 120:
        return doc
    return None


def _save_cache(access_token: str, expires_in: int, refresh_token: str) -> None:
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": int(time.time()) + int(expires_in or 3000),
        }), encoding="utf-8")
        import os
        os.chmod(tmp, 0o600)
        tmp.replace(CACHE_PATH)
    except Exception as exc:  # noqa: BLE001
        log.debug("Antigravity token 缓存写入失败：%s", exc)


def _refresh(refresh_token: str, client_id: str = CLIENT_ID) -> Tuple[str, int]:
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            doc = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise AntigravityError(f"刷新 Antigravity token 失败 HTTP {exc.code}: {detail}", exc.code) from exc
    except Exception as exc:  # noqa: BLE001
        raise AntigravityError(f"刷新 Antigravity token 失败: {exc}") from exc
    return doc.get("access_token") or "", int(doc.get("expires_in") or 3000)


def access_token(force: bool = False) -> str:
    """拿到一个可用的 access token。

    优先级：自家缓存 → 客户端文件（客户端在跑时它是最新的）→ 用 refresh_token 自己刷。
    """
    if not force:
        cached = _load_cache()
        if cached:
            return str(cached["access_token"])

    app_at, app_rt, app_exp = _app_token()
    if app_at and app_exp > time.time() + 120:
        return app_at  # 客户端刚刷新过，直接用

    rt = app_rt
    if not rt:
        cached = _read_json(CACHE_PATH) or {}
        rt = str(cached.get("refresh_token") or "")
    if not rt:
        raise AntigravityError(
            "找不到 Antigravity 的 refresh_token。请先打开并登录 Antigravity 客户端。", 503)

    doc = _read_json(APP_TOKEN_PATH) or {}
    at, expires_in = _refresh(rt, _client_id_from_id_token(doc))
    _save_cache(at, expires_in, rt)
    return at


def available() -> bool:
    """只要本地存在 Antigravity 凭据，就认为这条通道可用。"""
    app_at, app_rt, _ = _app_token()
    if app_at or app_rt:
        return True
    cached = _read_json(CACHE_PATH) or {}
    return bool(cached.get("refresh_token"))


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def _headers() -> Dict[str, str]:
    return {
        "Authorization": "Bearer " + access_token(),
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Accept": "*/*",
    }


def _post(path: str, body: dict, timeout: int = 0) -> dict:
    req = urllib.request.Request(
        ENDPOINT + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=_headers(), method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.UPSTREAM_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        msg = detail
        try:
            msg = json.loads(detail).get("error", {}).get("message") or detail
        except Exception:  # noqa: BLE001
            pass
        if "location is not supported" in msg.lower():
            msg = (
                "出口地区不被 Gemini 支持（" + msg + "）。"
                "把 daily-cloudcode-pa.googleapis.com 指向非香港节点即可，"
                "可运行 tools/clash_gemini_route.py 自动加规则。"
            )
        raise AntigravityError(f"Antigravity HTTP {exc.code}: {msg}", exc.code) from exc
    except Exception as exc:  # noqa: BLE001
        raise AntigravityError(f"Antigravity 连接失败: {exc}") from exc


_PROJECT: Dict[str, object] = {"at": 0.0, "id": ""}


def project_id(force: bool = False) -> str:
    """Antigravity 的 GCP 项目 id（免费档通常是 aicode-consumers）。"""
    now = time.time()
    if not force and _PROJECT["id"] and now - float(_PROJECT["at"]) < 3600:
        return str(_PROJECT["id"])
    doc = _post("/v1internal:loadCodeAssist", {"metadata": {"ideType": "ANTIGRAVITY"}})
    pid = doc.get("cloudaicompanionProject") or ""
    if isinstance(pid, dict):  # 有些响应把它包成对象
        pid = pid.get("id") or pid.get("projectId") or ""
    _PROJECT["id"] = str(pid)
    _PROJECT["at"] = now
    return str(pid)


# --------------------------------------------------------------------------
# 模型
# --------------------------------------------------------------------------

_MODELS_CACHE: Dict[str, object] = {"at": 0.0, "data": []}
_MODELS_TTL_S = 600


def _is_internal(mid: str) -> bool:
    low = mid.lower()
    return low.startswith(_INTERNAL_PREFIXES) or "image" in low


def list_models(force: bool = False) -> List[dict]:
    """列出 Antigravity 可对话的模型。非 gemini 的加 `agy/` 前缀。"""
    if not available():
        return []
    now = time.time()
    if not force and _MODELS_CACHE["data"] and now - float(_MODELS_CACHE["at"]) < _MODELS_TTL_S:
        return list(_MODELS_CACHE["data"])  # type: ignore[arg-type]
    try:
        doc = _post("/v1internal:fetchAvailableModels", {"project": project_id()}, timeout=40)
    except AntigravityError as exc:
        log.debug("Antigravity 模型列表拉取失败：%s", exc.message)
        return list(_MODELS_CACHE["data"])  # type: ignore[arg-type]

    raw = doc.get("models")
    ids: List[str] = []
    if isinstance(raw, dict):
        ids = [str(k) for k in raw.keys()]
    elif isinstance(raw, list):
        for m in raw:
            if isinstance(m, dict):
                mid = m.get("id") or m.get("name") or m.get("model")
                if mid:
                    ids.append(str(mid))
            elif isinstance(m, str):
                ids.append(m)

    out: List[dict] = []
    for mid in sorted(set(ids)):
        if _is_internal(mid):
            continue
        exposed = mid if mid.lower().startswith("gemini") else AGY_PREFIX + mid
        out.append({"id": exposed, "object": "model", "created": 0,
                    "owned_by": "google-antigravity"})
    _MODELS_CACHE["at"] = now
    _MODELS_CACHE["data"] = out
    log.info("Antigravity 模型目录：%d 个", len(out))
    return out


def strip_prefix(model: str) -> str:
    return model[len(AGY_PREFIX):] if model.startswith(AGY_PREFIX) else model


def is_prefixed(model: str) -> bool:
    return model.startswith(AGY_PREFIX)


# --------------------------------------------------------------------------
# 生成（产出 OpenAI 格式 SSE）
# --------------------------------------------------------------------------

def stream_openai(body: dict) -> Iterator[bytes]:
    """调用 Antigravity，产出 **OpenAI 格式** 的 SSE 行（不含结尾空行）。"""
    from . import gemini  # 延迟导入，避免与 gemini.py 形成循环

    if not available():
        raise gemini.GeminiError(
            "本机没有 Antigravity 凭据。打开并登录 Antigravity 客户端，或配置 GEMINI_API_KEY。", 503)

    model = strip_prefix(body.get("model") or "")
    inner = gemini.to_gemini_body(body)
    payload = {"model": model, "project": project_id(), "request": inner}
    req = urllib.request.Request(
        ENDPOINT + "/v1internal:streamGenerateContent?alt=sse",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=_headers(), method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=config.UPSTREAM_TIMEOUT)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        msg = detail
        try:
            msg = json.loads(detail).get("error", {}).get("message") or detail
        except Exception:  # noqa: BLE001
            pass
        if "location is not supported" in msg.lower():
            msg = ("出口地区不被 Gemini 支持。让 daily-cloudcode-pa.googleapis.com 走非香港节点，"
                   "可运行 tools/clash_gemini_route.py。原始信息：" + msg)
        raise gemini.GeminiError(f"Antigravity HTTP {exc.code}: {msg}", exc.code) from exc
    except Exception as exc:  # noqa: BLE001
        raise gemini.GeminiError(f"Antigravity 连接失败: {exc}") from exc

    cid = "chatcmpl-agy-" + str(int(time.time() * 1000))
    created = int(time.time())
    yield gemini._chunk(cid, model, created, {"role": "assistant", "content": ""}, None)
    usage_out: Optional[dict] = None
    try:
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
                # Antigravity 比 AI Studio 多包了一层 response
                doc = doc.get("response") or doc
                for cand in doc.get("candidates") or []:
                    parts = ((cand.get("content") or {}).get("parts")) or []
                    text = "".join(
                        p.get("text") or "" for p in parts
                        if isinstance(p, dict) and not p.get("thought")
                    )
                    if text:
                        yield gemini._chunk(cid, model, created, {"content": text}, None)
                    reason = cand.get("finishReason")
                    if reason:
                        yield gemini._chunk(cid, model, created, {},
                                            gemini.FINISH_MAP.get(reason, "stop"))
                # 上游多个 chunk 都会带 usageMetadata，只留最后一份，循环结束后统一发一次
                usage = doc.get("usageMetadata")
                if usage and usage.get("candidatesTokenCount") is not None:
                    usage_out = usage
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
    if usage_out is not None:
        yield b"data: " + json.dumps({
            "id": cid, "object": "chat.completion.chunk", "created": created,
            "model": model, "choices": [],
            "usage": {
                "prompt_tokens": usage_out.get("promptTokenCount", 0),
                "completion_tokens": usage_out.get("candidatesTokenCount", 0),
                "total_tokens": usage_out.get("totalTokenCount", 0),
            },
        }, ensure_ascii=False).encode("utf-8")
    yield b"data: [DONE]"


__all__ = ["AGY_PREFIX", "AntigravityError", "available", "access_token",
           "project_id", "list_models", "stream_openai", "strip_prefix", "is_prefixed"]
