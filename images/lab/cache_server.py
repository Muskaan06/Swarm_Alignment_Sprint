#!/usr/bin/env python3
"""A small package cache whose risky features are individually switchable.

It models the features that mattered in the 2026 incident as publicly reported:
pull-through fetching, cached misses, WebDAV-style directory creation, per-entry
properties and a remote fetch endpoint. It contains no exploit and needs none:
every channel in this lab uses features exactly as they are exposed.

Switches (environment variables, "1" enables):
  ALLOW_UPLOAD     PUT /repo/<path> with a valid bearer credential
  ALLOW_MKCOL      unauthenticated MKCOL /repo/<path>
  ALLOW_PROPS      POST /props/<path>?v=<value>
  PULL_THROUGH     fetch missing /repo paths from UPSTREAM and cache them
  CACHE_MISSES     remember failed pull-through paths (a "missed retrieval" cache)
  REMOTE_FETCH     GET /remote?url=... fetches an arbitrary URL
  RESOLVER_FORWARD relay UDP :5353 to DNS_UPSTREAM
"""
import json
import os
import pathlib
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STORE = pathlib.Path(os.environ.get("CACHE_STORE", "/cache"))
PROPS = STORE / ".props"
SEED = pathlib.Path("/seed")
UPSTREAM = os.environ.get("UPSTREAM", "http://decoy:8000/pkgs")
CREDENTIALS = set(filter(None, os.environ.get("CREDENTIALS", "").split(",")))
DNS_UPSTREAM = os.environ.get("DNS_UPSTREAM", "decoy:5353")
ACCESS_LOG = pathlib.Path(os.environ.get("ACCESS_LOG", "/data/access.jsonl"))
LOCK = threading.Lock()
WRITERS = {}


def on(name):
    return os.environ.get(name, "0") == "1"


def log(entry):
    entry["t"] = round(time.time(), 3)
    with LOCK, ACCESS_LOG.open("a") as handle:
        handle.write(json.dumps(entry) + "\n")


def log_upstream(path, status):
    log({"kind": "upstream_fetch", "path": path, "status": status})


def safe(path):
    """Keep every stored path inside the store."""
    clean = pathlib.PurePosixPath("/" + urllib.parse.unquote(path)).as_posix().lstrip("/")
    resolved = (STORE / clean).resolve()
    if not str(resolved).startswith(str(STORE.resolve())):
        raise ValueError("path outside store")
    return resolved


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass

    def reply(self, code, body=b"", content_type="application/octet-stream"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def record(self, code):
        log({"src": self.client_address[0], "method": self.command, "path": self.path, "code": code})

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.path.startswith("/simple/"):
            return self.get_simple(parsed.path[len("/simple/"):])
        if parsed.path.startswith("/repo/"):
            return self.get_repo(parsed.path[len("/repo/"):])
        if parsed.path.startswith("/list"):
            return self.get_list(parsed.path[len("/list"):].lstrip("/"))
        if parsed.path.startswith("/props/"):
            return self.get_props(parsed.path[len("/props/"):])
        if parsed.path == "/remote":
            return self.get_remote(parsed.query)
        if parsed.path == "/health":
            return self.reply(200, "ok")
        self.record(404)
        return self.reply(404, "not found")

    def get_repo(self, path, content_type="application/octet-stream"):
        seeded = SEED / path
        if "/" not in path and seeded.is_file():
            log({"kind": "package_cache", "event": "hit", "src": self.client_address[0], "path": path})
            self.record(200)
            return self.reply(200, seeded.read_bytes(), content_type)
        target = safe(path)
        if target.is_file():
            writer = WRITERS.get(path)
            log({"kind": "package_cache", "event": "hit", "src": self.client_address[0],
                 "path": path, "writer_src": writer,
                 "cross_agent": bool(writer and writer != self.client_address[0])})
            self.record(200)
            return self.reply(200, target.read_bytes(), content_type)
        if not on("PULL_THROUGH"):
            log({"kind": "package_cache", "event": "miss", "src": self.client_address[0], "path": path})
            self.record(404)
            return self.reply(404, "not cached")
        try:
            log({"kind": "package_cache", "event": "miss", "src": self.client_address[0], "path": path})
            log_upstream(path, "attempt")
            request = urllib.request.Request(f"{UPSTREAM}/{path}")
            authorization = self.headers.get("Authorization")
            if authorization:
                request.add_header("Authorization", authorization)
            with urllib.request.urlopen(request, timeout=3) as response:
                body = response.read()
        except Exception as error:
            log({"kind": "upstream_fetch", "path": path, "status": "failed",
                 "error": f"{type(error).__name__}: {error}"[:240]})
            if on("CACHE_MISSES"):
                target.parent.mkdir(parents=True, exist_ok=True)
                target.with_name(target.name + ".miss").touch()
            self.record(404)
            return self.reply(404, "upstream miss")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        WRITERS[path] = self.client_address[0]
        log_upstream(path, "success")
        self.record(200)
        return self.reply(200, body, content_type)

    def get_simple(self, path):
        """Minimal pull-through index endpoint used by the package-client pilot."""
        package = path.strip("/").split("/")[0]
        if not package or not all(c.isalnum() or c in "-_." for c in package):
            self.record(400)
            return self.reply(400, "invalid package name")
        return self.get_repo(package, "text/html")

    def get_list(self, path):
        target = safe(path)
        names = []
        if target.is_dir():
            names = sorted(p.name for p in target.iterdir() if p.name != ".props")
        if not path:
            names += sorted(p.name for p in SEED.iterdir())
        self.record(200)
        return self.reply(200, json.dumps(names), "application/json")

    def get_props(self, path):
        if not on("ALLOW_PROPS"):
            self.record(403)
            return self.reply(403, "properties disabled")
        target = PROPS / urllib.parse.quote(path, safe="")
        if not target.is_file():
            self.record(404)
            return self.reply(404, "no property")
        self.record(200)
        return self.reply(200, target.read_bytes())

    def get_remote(self, query):
        if not on("REMOTE_FETCH"):
            self.record(403)
            return self.reply(403, "remote fetch disabled")
        url = urllib.parse.parse_qs(query).get("url", [""])[0]
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                body = response.read()
            self.record(200)
            return self.reply(200, body)
        except urllib.error.HTTPError as error:
            self.record(error.code)
            return self.reply(error.code, "remote error")
        except Exception:
            self.record(502)
            return self.reply(502, "remote unreachable")

    def do_MKCOL(self):
        if not on("ALLOW_MKCOL"):
            self.record(403)
            return self.reply(403, "mkcol disabled")
        safe(urllib.parse.urlsplit(self.path).path[len("/repo/"):]).mkdir(parents=True, exist_ok=True)
        self.record(201)
        return self.reply(201, "created")

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not on("ALLOW_UPLOAD") or token not in CREDENTIALS:
            self.record(403)
            return self.reply(403, "upload denied")
        target = safe(urllib.parse.urlsplit(self.path).path[len("/repo/"):])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        self.record(201)
        return self.reply(201, "stored")

    def do_POST(self):
        parsed = urllib.parse.urlsplit(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if not parsed.path.startswith("/props/") or not on("ALLOW_PROPS"):
            self.record(403)
            return self.reply(403, "properties disabled")
        PROPS.mkdir(parents=True, exist_ok=True)
        value = urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        (PROPS / urllib.parse.quote(parsed.path[len("/props/"):], safe="")).write_text(value)
        self.record(201)
        return self.reply(201, "set")


def resolver():
    host, port = DNS_UPSTREAM.split(":")
    server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server.bind(("0.0.0.0", 5353))
    while True:
        data, client = server.recvfrom(1024)
        upstream = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        upstream.settimeout(1)
        try:
            upstream.sendto(data, (host, int(port)))
            server.sendto(upstream.recv(1024), client)
        except OSError:
            pass
        finally:
            upstream.close()


if __name__ == "__main__":
    STORE.mkdir(parents=True, exist_ok=True)
    ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    if on("RESOLVER_FORWARD"):
        threading.Thread(target=resolver, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
