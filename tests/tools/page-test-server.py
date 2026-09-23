#!/usr/bin/env python3
"""Local page test server for tests/phase2-tab-window-creation.sh.

Endpoints (http://127.0.0.1:<ephemeral-port>):
  /ping                      -> <title>PING_OK</title>
  /page?name=<id>            -> <title>BCLI_PAGE_<id></title>, body contains
                                "BCLI_BODY_<id>" (real HTTP + DOM content)
  /opener?name=<id>&child=<c>
                             -> opener page that defines window.__bcliOpenChild()
                                which performs a real JS window.open() of
                                /page?name=<c> (title BCLI_PAGE_opener_<id>,
                                body BCLI_BODY_opener_<id>)

Each request appends "<METHOD> <path>" to the log file.

Usage: page-test-server.py <port-file> <log-file>
No network beyond 127.0.0.1; no keychain; never launches a browser.
"""

import http.server
import re
import socketserver
import sys
import urllib.parse

SAFE_ID = re.compile(r"[^A-Za-z0-9_-]")


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BrowserCliPageTest/1"

    def log_message(self, fmt, *args):
        pass

    def _send_html(self, title, body_text=None, code=200, script=""):
        body_text = title if body_text is None else body_text
        body = (
            "<!doctype html><html><head><title>"
            + title
            + "</title></head><body><div id=\"marker\">"
            + body_text
            + "</div>"
            + ("<script>" + script + "</script>" if script else "")
            + "</body></html>"
        ).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        with open(self.server.logfile, "a", encoding="utf-8") as log:
            log.write("%s %s\n" % (self.command, self.path))

        if parsed.path == "/ping":
            self._send_html("PING_OK")
        elif parsed.path == "/page":
            name = query.get("name", ["unnamed"])[0]
            self._send_html("BCLI_PAGE_" + name, "BCLI_BODY_" + name)
        elif parsed.path == "/opener":
            name = SAFE_ID.sub("", query.get("name", ["parent"])[0]) or "parent"
            child = SAFE_ID.sub("", query.get("child", ["child"])[0]) or "child"
            script = (
                "window.__bcliLastChild = null;"
                "window.__bcliOpenChild = function () {"
                "window.__bcliLastChild = window.open('/page?name=" + child + "',"
                "'bcli_child','width=320,height=240');"
                "return !!(window.__bcliLastChild && !window.__bcliLastChild.closed);"
                "};"
            )
            self._send_html(
                "BCLI_PAGE_opener_" + name, "BCLI_BODY_opener_" + name, script=script
            )
        else:
            self._send_html("NOT_FOUND", code=404)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: page-test-server.py <port-file> <log-file>\n")
        return 2
    port_file, log_file = sys.argv[1], sys.argv[2]
    server = Server(("127.0.0.1", 0), Handler)
    server.logfile = log_file
    with open(port_file, "w", encoding="utf-8") as handle:
        handle.write(str(server.server_address[1]))
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
