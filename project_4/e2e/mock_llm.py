"""A loopback stand-in for an OpenAI-compatible chat completions endpoint."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CANNED_ANSWER = "Canned answer from the mock LLM."


class MockLLM:
    """Records every POST and replies with success, or with 500 if `fail` is set."""

    def __init__(self):
        self.requests = []
        self.fail = False
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                mock.requests.append(
                    {
                        "path": self.path,
                        "headers": {k.lower(): v for k, v in self.headers.items()},
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
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        # Port 0 lets the system pick a free port.
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self):
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
