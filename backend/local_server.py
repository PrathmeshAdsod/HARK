from __future__ import annotations

import base64
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hark.web import lambda_handler  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    def _dispatch(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length) if length else b""
        event = {
            "rawPath": urlsplit(self.path).path,
            "requestContext": {"http": {"method": self.command}},
            "body": base64.b64encode(body).decode("ascii") if body else None,
            "isBase64Encoded": bool(body),
        }
        response = lambda_handler(event, None)
        self.send_response(response["statusCode"])
        for name, value in response.get("headers", {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(response.get("body", "").encode("utf-8"))

    do_GET = _dispatch
    do_POST = _dispatch

    def log_message(self, fmt: str, *args) -> None:
        print(f"[hark] {fmt % args}")


if __name__ == "__main__":
    host, port = "127.0.0.1", 8080
    print(f"Hark local server: http://{host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
