"""A stand-in for an OpenAI-compatible endpoint, on a loopback port."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CANNED_ANSWER = "Canned answer from the mock LLM."


class MockLLM:
    """Records every POST. Set `fail` to make it answer with status 500."""

    def __init__(self):
        self.requests: list[dict] = []
        self.fail = False
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("content-length", 0))
                mock.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("authorization"),
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                if mock.fail:
                    self._reply(500, {"error": "mock failure"})
                else:
                    message = {"role": "assistant", "content": CANNED_ANSWER}
                    self._reply(200, {"choices": [{"message": message}]})

            def _reply(self, status, payload):
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_port}/v1"

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
