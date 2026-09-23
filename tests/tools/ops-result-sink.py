#!/usr/bin/env python3
"""Local passive result sink for the HUMAN-ownership manual scaffold.

Receives POSTed JSON evidence from the manual extension ops page
(tests/tools/extensions/browser-cli-human-window-ops) and appends each payload
as one JSON line. Binds 127.0.0.1 only; opens no windows; performs no browser
automation.

Usage: ops-result-sink.py <output-jsonl> [port]   (default port 8737)
"""

import http.server
import socketserver
import sys


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BrowserCliOpsSink/1"

    def log_message(self, fmt, *args):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path != "/health":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/ops-result":
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        payload = self.rfile.read(length)
        with open(self.server.output_path, "ab") as handle:
            handle.write(payload.rstrip(b"\n") + b"\n")
        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: ops-result-sink.py <output-jsonl> [port]\n")
        return 2
    output_path = sys.argv[1]
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8737
    server = Server(("127.0.0.1", port), Handler)
    server.output_path = output_path
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
