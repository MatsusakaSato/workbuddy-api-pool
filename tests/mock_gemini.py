"""Mock Gemini 上游：说 Google 的协议，用来在没有真 key 的情况下验证网关的转换层。

只实现网关用到的两个接口：
  GET  /v1beta/models                        -> 模型列表
  POST /v1beta/models/{id}:streamGenerateContent?alt=sse -> Gemini 格式 SSE

用法：python3 tests/mock_gemini.py [port]
"""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORDS = ["Gemini", "通道", "已", "打通", "！"]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("mock-gemini: " + fmt % args + "\n")

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/v1beta/models"):
            body = json.dumps({
                "models": [
                    {"name": "models/gemini-2.5-flash",
                     "supportedGenerationMethods": ["generateContent", "streamGenerateContent"]},
                    {"name": "models/gemini-2.5-pro",
                     "supportedGenerationMethods": ["generateContent"]},
                    {"name": "models/embedding-001",
                     "supportedGenerationMethods": ["embedContent"]},
                ]
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            req = json.loads(raw.decode())
        except Exception:  # noqa: BLE001
            req = {}

        # 记录收到的内容，供测试断言协议转换是否正确
        sys.stderr.write("mock-gemini: REQUEST " + json.dumps(req, ensure_ascii=False) + "\n")

        if "streamGenerateContent" not in self.path:
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def emit(obj):
            data = b"data: " + json.dumps(obj, ensure_ascii=False).encode() + b"\r\n\r\n"
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()

        for w in WORDS:
            emit({"candidates": [{"content": {"role": "model", "parts": [{"text": w}]},
                                  "index": 0}]})
        emit({"candidates": [{"content": {"role": "model", "parts": []},
                              "finishReason": "STOP", "index": 0}],
              "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 5,
                                "totalTokenCount": 12}})
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def main() -> int:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8791
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    sys.stderr.write(f"mock-gemini: listening on http://127.0.0.1:{port}\n")
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
