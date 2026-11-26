"""A tiny OpenAI-compatible server on loopback, for the end-to-end test."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockLLM:
    """Records every POST and answers with success or a chosen error status."""

    def __init__(self):
        self.requests = []
        self.status = 200
        self.answer = ""
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                mock.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                if mock.status == 200:
                    message = {"role": "assistant", "content": mock.answer}
                    payload = {"choices": [{"message": message}]}
                else:
                    payload = {"error": "mock failure"}
                data = json.dumps(payload).encode()
                self.send_response(mock.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self._server.server_port}/v1"

    def succeed(self, answer):
        self.requests.clear()
        self.status = 200
        self.answer = answer

    def fail(self, status=500):
        self.requests.clear()
        self.status = status

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
