#!/usr/bin/env python3
"""Static HTTP server for curated channel artwork.

Both ecosystems fetch channel logos over HTTP from whatever `tvg-logo` says.
Today that is the provider, which has degraded many logos to the DirecTV GO
placeholder -- so any re-fetch loses artwork. Pointing `tvg-logo` here instead
makes the curated art the source of truth:

  * production Jellyfin re-fetches OUR image instead of the provider's
  * CT 112's NextPVR, which populates icons once at channel import and never
    refreshes, imports OUR image -- which turns a channel re-import from a
    risk into the delivery mechanism
  * a rebuilt stack just points at this host

Serves /root/icon-archive/extracted, which `icon-archive extract` fills from
the content-addressed archive (production Jellyfin, CT 112 NextPVR, and
generated artwork not yet installed anywhere).

Read-only, no directory listing, and it will not serve anything outside the
icon directory. Nothing here accepts input that changes state.

  /icons/<url-encoded channel name>.png
  /healthz
  /index.json      catalogue: channel name -> url, for tooling
"""
import json
import os
import posixpath
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ICON_DIR = "/root/icon-archive/extracted"
PORT = 8100
BIND = "0.0.0.0"

CTYPE = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp"}


class Handler(BaseHTTPRequestHandler):
    server_version = "media-core-icon-host/1.0"

    def log_message(self, fmt, *args):        # quiet; journal gets the errors
        pass

    def _send(self, code, body, ctype="text/plain", cache=True):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            # Icons change rarely, but when they do, a day-long client cache
            # is the difference between a fix landing and not. Jellyfin held
            # 113 stale channel logos through a full clear-and-refresh on
            # 2026-08-03 because of a 24h max-age here. Revalidate instead:
            # the images are small and both consumers are on the LAN.
            self.send_header("Cache-Control", "public, max-age=60, must-revalidate")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path

        if path == "/healthz":
            n = len([f for f in os.listdir(ICON_DIR)]) if os.path.isdir(ICON_DIR) else 0
            return self._send(200, json.dumps({"ok": True, "icons": n}).encode(),
                              "application/json", cache=False)

        if path == "/index.json":
            if not os.path.isdir(ICON_DIR):
                return self._send(503, b'{"error":"icon dir missing"}',
                                  "application/json", cache=False)
            cat = {os.path.splitext(f)[0]:
                   "/icons/" + urllib.parse.quote(f)
                   for f in sorted(os.listdir(ICON_DIR))}
            return self._send(200, json.dumps(cat, indent=1).encode(),
                              "application/json", cache=False)

        if not path.startswith("/icons/"):
            return self._send(404, b"not found")

        name = urllib.parse.unquote(path[len("/icons/"):])
        # Refuse anything that could escape the icon directory. normpath first
        # so "a/../../etc/passwd" collapses before the check.
        safe = posixpath.normpath("/" + name).lstrip("/")
        # `".." in safe` would be wrong here: it is a substring test, and a
        # legitimate channel filename can contain two dots
        # ("DE NDR HD MECKLENBURG V..png"). Check path *components*.
        if (not safe or safe != name or os.path.isabs(name)
                or ".." in safe.split("/")):
            return self._send(400, b"bad name")

        full = os.path.join(ICON_DIR, safe)
        if os.path.dirname(os.path.realpath(full)) != os.path.realpath(ICON_DIR):
            return self._send(400, b"bad name")
        if not os.path.isfile(full):
            return self._send(404, b"no such icon")

        with open(full, "rb") as fh:
            blob = fh.read()
        ctype = CTYPE.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        self._send(200, blob, ctype)


def main():
    os.makedirs(ICON_DIR, exist_ok=True)
    srv = ThreadingHTTPServer((BIND, PORT), Handler)
    srv.serve_forever()


if __name__ == "__main__":
    main()
