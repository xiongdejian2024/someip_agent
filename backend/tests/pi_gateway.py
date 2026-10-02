"""本地真实 HTTP 网关夹具，Pi 使用实际 Node SDK 读取 SSE；不拦截 HTTPX。"""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


@contextmanager
def gateway(responses, *, status=200, content_type="text/event-stream"):
    payloads = []
    headers = []
    remaining = list(responses)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            payloads.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            headers.append({key.lower(): value for key, value in self.headers.items()})
            assert self.path == "/v1/chat/completions"
            body = remaining.pop(0) if remaining else b""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            try:
                self.wfile.write(body)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                # 取消测试预期关闭网关连接，不是产品异常吞掉堆栈。
                return

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", payloads, headers
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def completion(text):
    return (
        "data: "
        + json.dumps(
            {"choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}]},
            ensure_ascii=False,
        )
        + "\n\ndata: [DONE]\n\n"
    ).encode()


def tool_call(name, arguments, call_id="call_1"):
    return (
        "data: "
        + json.dumps(
            {
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": call_id,
                                    "type": "function",
                                    "function": {"name": name, "arguments": json.dumps(arguments)},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        + "\n\ndata: [DONE]\n\n"
    ).encode()
