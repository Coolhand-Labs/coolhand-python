"""A throwaway local HTTP server standing in for the Coolhand API, for offline examples.

The Coolhand client delivers to it over real HTTP, so a check on ``received`` proves
exactly what would have left the process.
"""

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer


class FakeCoolhandApi(BaseHTTPRequestHandler):
    received: list[dict] = []

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        type(self).received.append(json.loads(body))
        self.send_response(201)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_: object) -> None:
        pass


@contextmanager
def serve() -> Iterator[str]:
    """Run the fake API on a free local port; yield its base URL."""
    FakeCoolhandApi.received.clear()
    server = HTTPServer(("127.0.0.1", 0), FakeCoolhandApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
