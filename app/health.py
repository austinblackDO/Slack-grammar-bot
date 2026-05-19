from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger("slack-grammar-bot")

_HEALTH_BODY = b'{"ok":true}\n'


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib hook name
        if self.path != "/healthz":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_HEALTH_BODY)))
        self.end_headers()
        self.wfile.write(_HEALTH_BODY)

    def log_message(self, fmt: str, *args: object) -> None:
        logger.debug("health: " + fmt, *args)


def start_health_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    threading.Thread(target=server.serve_forever, name="health-server", daemon=True).start()
    logger.info("health server listening on port %s", port)
    return server
