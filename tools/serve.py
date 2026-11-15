#!/usr/bin/env python3
"""Preview site/ locally with clean URLs (/transfem -> transfem.html), like the real host.

Run:  python3 -I tools/serve.py [port]
"""
import functools
import http.server
import os
import sys

SITE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "site")


class Handler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                      ".js": "text/javascript; charset=utf-8", ".txt": "text/plain; charset=utf-8"}

    def send_head(self):
        path = self.translate_path(self.path)
        if not os.path.exists(path) and os.path.exists(path + ".html"):
            self.path = self.path.split("?")[0] + ".html"
        elif not os.path.exists(path):
            self.send_response(404)
            body = open(os.path.join(SITE, "404.html"), "rb").read()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return None
        return super().send_head()


port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
http.server.ThreadingHTTPServer(("127.0.0.1", port),
                                functools.partial(Handler, directory=SITE)).serve_forever()
