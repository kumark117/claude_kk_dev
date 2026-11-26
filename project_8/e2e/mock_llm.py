"""A standard-library stand-in for an OpenAI-compatible endpoint, on loopback only."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class MockLLM:
    """Records every request; answers with ANSWER, or fails with the chosen status."""

    ANSWER = "Canned answer from the mock LLM."

    def __init__(self):
        self.requests: list[dict] = []
        self.status = 200
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
                    payload = {"choices": [{"message": {"content": mock.ANSWER}}]}
                else:
                    payload = {"error": "mock failure"}
                data = json.dumps(payload).encode()
                self.send_response(mock.status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"

    def reset(self, status: int = 200) -> None:
        self.requests.clear()
        self.status = status

    def __enter__(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
