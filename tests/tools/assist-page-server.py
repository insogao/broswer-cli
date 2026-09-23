#!/usr/bin/env python3
"""Local page server for the Foreground Assist manual acceptance package.

Host-side only: it never launches, opens or touches a browser. It serves
self-identifying pages and appends one line per request to a log file, so the
offline collector can prove which navigations really happened during the
manual session.

Usage:
  assist-page-server.py --log PATH --port PORT [--bind 127.0.0.1]

Endpoints:
  /page?name=X   -> <title>BCLI_PAGE_X</title> and BCLI_BODY_X in the body
  /healthz       -> "ok"

Log format (one line per request):
  ts=<epoch_ms> method=GET path=<path> status=<code>

Exit: 0 on SIGTERM/SIGINT (the driver stops it by exact PID).
"""

import argparse
import http.server
import signal
import sys
import time
import urllib.parse


def now_ms():
    return int(time.time() * 1000)


class AssistPageHandler(http.server.BaseHTTPRequestHandler):
    server_version = "BrowserCliAssistPage/1.0"

    def log_message(self, fmt, *args):  # noqa: A003 - silence default logging
        pass

    def _record(self, status, path=None):
        line = "ts=%d method=%s path=%s status=%d\n" % (
            now_ms(),
            self.command or "GET",
            path if path is not None else self.path,
            status,
        )
        with open(self.server.log_path, "a", encoding="utf-8") as handle:
            handle.write(line)

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/healthz":
            self._send(200, "text/plain; charset=utf-8", b"ok")
            self._record(200)
            return
        if parsed.path == "/page":
            name = urllib.parse.parse_qs(parsed.query).get("name", ["page"])[0]
            safe = (
                name.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )
            html = (
                "<!doctype html><html><head><meta charset=\"utf-8\">"
                "<title>BCLI_PAGE_%s</title></head>"
                "<body>BCLI_BODY_%s</body></html>" % (safe, safe)
            )
            self._send(200, "text/html; charset=utf-8", html.encode("utf-8"))
            self._record(200)
            return
        self._send(404, "text/plain; charset=utf-8", b"not found")
        self._record(404)


class AssistPageServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, log_path):
        super().__init__(address, handler)
        self.log_path = log_path


def main():
    parser = argparse.ArgumentParser(
        description="Local page server for the Foreground Assist manual package."
    )
    parser.add_argument("--log", required=True, help="request log file (appended)")
    parser.add_argument("--port", type=int, required=True, help="TCP port to bind")
    parser.add_argument("--bind", default="127.0.0.1", help="bind address")
    args = parser.parse_args()

    server = AssistPageServer((args.bind, args.port), AssistPageHandler, args.log)

    def _stop(_signum, _frame):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    print(
        "ASSIST-PAGE-SERVER: listening on http://%s:%d/ (log %s)"
        % (args.bind, server.server_address[1], args.log),
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    except SystemExit:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
