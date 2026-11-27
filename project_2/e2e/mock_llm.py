"""A loopback stand-in for an OpenAI-compatible server (standard library only)."""

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = "Canned answer from the mock LLM."


@dataclass
class RecordedRequest:
    path: str
    authorization: str | None
    body: dict


class MockLLM:
    """Records every POST. Set `fail` to make it answer with status 500."""

    def __init__(self):
        self.requests: list[RecordedRequest] = []
        self.fail = False
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def reset(self):
        self.requests.clear()
        self.fail = False

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _handler(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("content-length", 0)))
                mock.requests.append(
                    RecordedRequest(
                        self.path, self.headers.get("authorization"), json.loads(raw)
                    )
                )
                if mock.fail:
                    self._reply(500, {"error": "mock failure"})
                else:
                    message = {"role": "assistant", "content": ANSWER}
                    self._reply(200, {"choices": [{"message": message}]})

            def _reply(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        return Handler
