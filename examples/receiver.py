#!/usr/bin/env python3
"""A minimal destination: prints every delivery and answers 204.

Run it in one terminal while trying the demo:

    python examples/receiver.py
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        print(f"--- {self.command} {self.path}\n{body}\n", flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        pass  # keep the output to the bodies


if __name__ == "__main__":
    print("receiver listening on http://127.0.0.1:9100/hook", flush=True)
    HTTPServer(("127.0.0.1", 9100), Handler).serve_forever()
