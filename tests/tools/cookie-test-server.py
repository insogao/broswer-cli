#!/usr/bin/env python3
"""Local cookie test server for tests/phase2-cookie-profile-isolation.sh.

Endpoints (all on http://127.0.0.1:<ephemeral-port>):
  /ping                  -> <title>PING_OK</title>
  /set?name=N&value=V    -> Set-Cookie: N=V; Path=/; Max-Age=600 (+ <title>COOKIE_SET_V</title>)
  /read?name=N           -> <title>COOKIE_READ_<value|ABSENT></title>

Every request appends one line to the log file:
  "<METHOD> <path> cookie=<raw Cookie header or ->"

Usage: cookie-test-server.py <port-file> <log-file>
Writes the bound port to <port-file> and serves until killed.
No network beyond 127.0.0.1; no keychain; no browser interaction.
"""

import http.server
import socketserver
import sys
import urllib.parse


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "BrowserCliCookieTest/1"

    def log_message(self, fmt, *args):  # keep stderr clean
        pass

    def _send_html(self, title, extra_headers=None, code=200):
        body = (
            "<!doctype html><html><head><title>"
            + title
            + "</title></head><body>"
            + title
            + "</body></html>"
        ).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in extra_headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        cookie_header = self.headers.get("Cookie", "-")

        with open(self.server.logfile, "a", encoding="utf-8") as log:
            log.write("%s %s cookie=%s\n" % (self.command, self.path, cookie_header))

        if parsed.path == "/ping":
            self._send_html("PING_OK")
        elif parsed.path == "/set":
            name = query.get("name", ["bc_cookie"])[0]
            value = query.get("value", ["unset"])[0]
            self._send_html(
                "COOKIE_SET_" + value,
                [("Set-Cookie", "%s=%s; Path=/; Max-Age=600" % (name, value))],
            )
        elif parsed.path == "/read":
            name = query.get("name", ["bc_cookie"])[0]
            found = "ABSENT"
            for part in cookie_header.split(";"):
                part = part.strip()
                if part.startswith(name + "="):
                    found = part.split("=", 1)[1]
                    break
            self._send_html("COOKIE_READ_" + found)
        else:
            self._send_html("NOT_FOUND", code=404)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: cookie-test-server.py <port-file> <log-file>\n")
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
