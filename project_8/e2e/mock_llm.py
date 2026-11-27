"""A tiny OpenAI-compatible server for tests, using only the standard library."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CANNED_ANSWER = "Canned answer from the mock LLM."


class MockLLM:
    """Records every POST. Set `fail` to make it reply with status 500."""

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
                        "authorization": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                if mock.fail:
                    self._send(500, {"error": "mock failure"})
                else:
                    message = {"role": "assistant", "content": CANNED_ANSWER}
                    self._send(200, {"choices": [{"message": message}]})

            def _send(self, status, body):
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
