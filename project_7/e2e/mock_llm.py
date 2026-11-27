"""A tiny OpenAI-compatible server for the end-to-end tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = "Canned answer from the mock LLM."


class MockLLM:
    """Serve on a free loopback port, record requests, succeed or fail on demand."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.fail = False
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                mock.requests.append(
                    {
                        "path": self.path,
                        "authorization": self.headers.get("Authorization"),
                        "body": json.loads(self.rfile.read(length)),
                    }
                )
                if mock.fail:
                    self._reply(500, {"error": "mock failure"})
                else:
                    message = {"role": "assistant", "content": ANSWER}
                    self._reply(200, {"choices": [{"message": message}]})

            def _reply(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "MockLLM":
        self._thread.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def reset(self) -> None:
        self.requests.clear()
        self.fail = False
