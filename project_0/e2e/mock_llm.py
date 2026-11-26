"""A stand-in for an OpenAI-compatible endpoint, listening on loopback only."""

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = "Canned answer from the mock LLM."


@dataclass
class RecordedRequest:
    path: str
    headers: dict[str, str]  # names in lower case
    body: dict


class MockLLM:
    """Records every request. Set `fail` to make it reply with status 500."""

    def __init__(self):
        self.requests: list[RecordedRequest] = []
        self.fail = False
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def reset(self):
        self.requests.clear()
        self.fail = False

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def _handler(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                mock.requests.append(
                    RecordedRequest(
                        path=self.path,
                        headers={k.lower(): v for k, v in self.headers.items()},
                        body=json.loads(self.rfile.read(length)),
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
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        return Handler
