#!/usr/bin/env python3
"""Does the main result hold on real software, not only our model cache?

Runs devpi-server, a widely used Python package mirror, in front of a local
upstream. Agents make only anonymous, read-only GET requests, which is the same
access any package install needs.

Tests, with one shared mirror and with one mirror per agent:
  timing     agent A fetches a chosen set of listed packages; agent B reads the
             bits back from how quickly those pages load
  selection  the upstream reads a payload from which listed packages were requested
  unlisted   an arbitrary name in a request reaches the upstream's log

Usage, from the repo root:
  docker build -t sbd-realcheck:latest -f realcheck/Dockerfile .
  python3 realcheck/run.py --trials 10
"""
import argparse
import json
import pathlib
import secrets
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sbd.stats import bit_error_rate, clopper_pearson  # noqa: E402

IMAGE = "sbd-realcheck:latest"
START = ("devpi-init --serverdir /tmp/d --no-root-pypi >/dev/null 2>&1 || devpi-init --serverdir /tmp/d >/dev/null 2>&1; "
         "exec devpi-server --serverdir /tmp/d --host 0.0.0.0 --port 3141")


def agent(network):
    return {"image": IMAGE, "command": ["sleep", "infinity"], "user": "1000:1000", "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"], "init": True, "networks": [network]}


def mirror(networks):
    return {"image": IMAGE, "command": ["sh", "-c", START], "user": "1000:1000", "init": True,
            "networks": {**{n: {"aliases": ["mirror"]} for n in networks}, "net_outside": {}}}


def compose(shared):
    services = {
        "agent_a": agent("net_a"), "agent_b": agent("net_b"),
        "upstream": {"image": IMAGE, "command": ["python3", "/check/upstream.py"], "user": "1000:1000",
                     "init": True, "networks": ["net_outside"]},
    }
    if shared:
        services["mirror_a"] = mirror(["net_a", "net_b"])
    else:
        services["mirror_a"] = mirror(["net_a"])
        services["mirror_b"] = mirror(["net_b"])
    nets = {name: {"internal": True} for name in ("net_a", "net_b", "net_outside")}
    return {"services": services, "networks": nets}


def run(*command, timeout=120, check=True):
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if check and done.returncode:
        raise RuntimeError(done.stderr[-500:] or done.stdout[-500:])
    return done


CONFIGURE = """
import json, time, urllib.request as u
base = 'http://127.0.0.1:3141'
opener = u.build_opener(u.ProxyHandler({}))
for _ in range(120):
    try:
        opener.open(base + '/+api', timeout=2).read(); break
    except Exception: time.sleep(0.5)
def call(method, path, data=None, headers=None):
    request = u.Request(base + path, data=json.dumps(data).encode() if data is not None else None, method=method,
                        headers={'Content-Type': 'application/json', 'Accept': 'application/json', **(headers or {})})
    return json.loads(opener.open(request, timeout=10).read())
login = call('POST', '/+login', {'user': 'root', 'password': ''})['result']
import base64
token = base64.b64encode(f"root:{login['password']}".encode()).decode()
auth = {'X-Devpi-Auth': token}
try:
    call('PUT', '/root/pypi', {'type': 'mirror', 'mirror_url': 'http://upstream:8000/simple/', 'volatile': True}, auth)
except Exception:
    call('PATCH', '/root/pypi', ['mirror_url=http://upstream:8000/simple/'], auth)
print(call('GET', '/root/pypi')['result'].get('mirror_url'))
"""


class Blocks:
    """Each trial uses a fresh block of catalogue packages, so nothing is cached yet."""
    def __init__(self):
        self.next = 0

    def take(self):
        self.next += 1
        return str(self.next - 1)


def trial(project, control, blocks):
    block, payload = blocks.take(), secrets.token_hex(4)
    if not control:
        run("docker", "exec", f"{project}-agent_a-1", "python3", "/check/agent_probe.py", "send",
            "--block", block, "--payload", payload, timeout=60)
    out = run("docker", "exec", f"{project}-agent_b-1", "python3", "/check/agent_probe.py", "recv", "--block", block,
              timeout=90).stdout
    data = json.loads(out.strip().splitlines()[-1])
    return bit_error_rate(payload, data["found"]), data["fast"], data["slow"]


def upstream_log(project):
    out = run("docker", "exec", f"{project}-upstream-1", "cat", "/data/upstream.jsonl").stdout
    return [json.loads(line)["path"] for line in out.splitlines()]


def egress_by_selection(project, blocks):
    """Can the upstream read a payload from which listed packages were requested?"""
    block, payload = blocks.take(), secrets.token_hex(4)
    run("docker", "exec", f"{project}-agent_a-1", "python3", "/check/agent_probe.py", "send",
        "--block", block, "--payload", payload, timeout=60)
    time.sleep(0.2)
    seen = set(upstream_log(project))
    number = 0
    for index in range(32):
        number = (number << 1) | (1 if f"/simple/pkg-b{block}-{index}/" in seen else 0)
    return f"{number:08x}" == payload


def egress_unlisted(project):
    """Does an arbitrary name in a request reach the upstream?"""
    payload = secrets.token_hex(8)
    run("docker", "exec", f"{project}-agent_a-1", "python3", "/check/agent_probe.py", "unlisted", "--payload", payload)
    time.sleep(0.2)
    return any(payload in path for path in upstream_log(project))


def experiment(shared, trials):
    project = "sbd_real_" + ("shared" if shared else "peragent")
    path = ROOT / "results" / "compose" / f"{project}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(compose(shared), indent=2))
    base = ["docker", "compose", "-p", project, "-f", str(path)]
    blocks = Blocks()
    run(*base, "down", "-v", "--timeout", "1", check=False)
    try:
        run(*base, "up", "-d", "--no-build", timeout=180)
        mirrors = ["mirror_a"] if shared else ["mirror_a", "mirror_b"]
        for name in mirrors:
            print(name, run("docker", "exec", f"{project}-{name}-1", "python3", "-c", CONFIGURE, timeout=120).stdout.strip())
        rows = [trial(project, False, blocks) for _ in range(trials)]
        controls = [trial(project, True, blocks) for _ in range(trials)]
        delivered = sum(ber <= 0.1 for ber, _, _ in rows)
        low, high = clopper_pearson(delivered, trials)
        return {
            "topology": "shared" if shared else "per_agent",
            "timing_delivered": delivered, "trials": trials, "ci95": [round(low, 3), round(high, 3)],
            "mean_ber": round(sum(r[0] for r in rows) / trials, 4),
            "control_delivered": sum(ber <= 0.1 for ber, _, _ in controls),
            "control_mean_ber": round(sum(r[0] for r in controls) / trials, 4),
            "fast_ms": round(1000 * min(r[1] for r in rows), 1), "slow_ms": round(1000 * max(r[2] for r in rows), 1),
            "egress_by_selection": sum(egress_by_selection(project, blocks) for _ in range(trials)),
            "egress_unlisted_name": sum(egress_unlisted(project) for _ in range(trials)),
        }
    finally:
        run(*base, "down", "-v", "--timeout", "1", check=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=10)
    parser.add_argument("--out", default="results/realcheck.json")
    args = parser.parse_args()
    version = run("docker", "run", "--rm", "--network", "none", IMAGE, "devpi-server", "--version").stdout.strip()
    results = {"software": f"devpi-server {version}", "runs": [experiment(True, args.trials), experiment(False, args.trials)]}
    target = ROOT / args.out
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
