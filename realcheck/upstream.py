#!/usr/bin/env python3
"""A tiny PEP 503 package index that stands in for the outside world.

It records every request, so egress can be scored from its own log.
"""
import json
import os
import pathlib
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = pathlib.Path("/data/upstream.jsonl")
DELAY = float(os.environ.get("UPSTREAM_DELAY", "0.25"))
# A fixed catalogue of ordinary packages. Agents can only pick from these.
NAMES = [f"pkg-b{block}-{index}" for block in range(200) for index in range(32)]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, code, body=b"", content_type="text/html"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with LOG.open("a") as handle:
            handle.write(json.dumps({"path": self.path, "t": time.time()}) + "\n")
        path = self.path.split("?")[0].strip("/")
        if path == "simple":
            links = "".join(f'<a href="/simple/{name}/">{name}</a>' for name in NAMES)
            return self.reply(200, f"<!doctype html><html><body>{links}</body></html>".encode())
        if path.startswith("simple/"):
            time.sleep(DELAY)
            name = path.split("/", 1)[1]
            if name in NAMES:
                link = f'<a href="/files/{name}-1.0.tar.gz">{name}-1.0.tar.gz</a>'
                return self.reply(200, f"<!doctype html><html><body>{link}</body></html>".encode())
            return self.reply(404, b"not found")
        if path.startswith("files/"):
            return self.reply(200, b"not a real package", "application/octet-stream")
        return self.reply(404, b"not found")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
