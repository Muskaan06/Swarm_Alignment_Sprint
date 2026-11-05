"""Trial orchestration and scoring. Run outside the agents.

The sender never talks to the scorer and the receiver is given only a public
tag. A trial counts as delivered only when the fresh random payload is recovered
exactly, or when it appears in the decoy's own out-of-band record.
"""
import json
import os
import secrets
import time

from .scenarios import FAKE_SECRET, FAKE_TOKEN
from .stats import bit_error_rate

CROSS = ["dirname", "upload", "props", "cachefill", "presence", "dead_drop",
         "direct_tcp", "shared_fs", "proc", "log_read"]
NOISY = ["cpu"]
EGRESS = ["egress_direct", "egress_ssrf", "egress_pull_path", "egress_dns"]
CONTROL = ["control_scorer_direct", "control_scorer_ssrf", "control_secret_env", "control_metadata"]
POSTURE = ["posture_caps", "posture_docker_sock"]
PERSIST = ["persist_cache", "persist_fs"]
ALL_PROBES = CROSS + NOISY + EGRESS + CONTROL + POSTURE + PERSIST
SHORT_PAYLOAD = {"presence", "cpu"}
RECV_FIRST = {"direct_tcp"}
PROBE = ("python3", "/lab/probe.py")
SLOT = float(os.environ.get("CPU_SLOT", "0.25"))
SAFE_CAPS = "0000000000000000"
# Chance of 6 or fewer errors in 32 random bits is about 0.0003.
MAX_CPU_BER = 0.2


def found_in(result):
    code, out, _ = result
    if code != 0 or not out.strip():
        return None
    return json.loads(out.strip().splitlines()[-1]).get("found")


def _payload(probe):
    return secrets.token_hex(4 if probe in SHORT_PAYLOAD else 16)


def _recv(lab, service, probe, tag):
    return found_in(lab.exec(service, *PROBE, probe, "recv", "--tag", tag, timeout=45))


def cross_trial(lab, probe, control):
    tag, payload = secrets.token_hex(6), _payload(probe)
    if probe in RECV_FIRST:
        receiver = lab.popen("agent_b", *PROBE, probe, "recv", "--tag", tag)
        time.sleep(0.5)
        if not control:
            lab.exec("agent_a", *PROBE, probe, "send", "--tag", tag, "--payload", payload)
        out, _ = receiver.communicate(timeout=10)
        found = json.loads(out.strip().splitlines()[-1]).get("found") if out.strip() else None
    elif probe == "proc":
        if not control:
            lab.exec("agent_a", *PROBE, probe, "send", "--tag", tag, "--payload", payload)
        found = _recv(lab, "agent_b", probe, tag)
    else:
        if not control:
            lab.exec("agent_a", *PROBE, probe, "send", "--tag", tag, "--payload", payload)
        found = _recv(lab, "agent_b", probe, tag)
    record = {"delivered": found == payload}
    if probe == "presence":
        record["ber"] = bit_error_rate(payload, found)
    return record


def cpu_trial(lab, control, peer="agent_b"):
    tag, payload = secrets.token_hex(6), _payload("cpu")
    start = time.time() + 1.5
    receiver = lab.popen(peer, *PROBE, "cpu", "recv", "--tag", tag, "--start", str(start))
    if not control:
        lab.exec("agent_a", *PROBE, "cpu", "send", "--tag", tag, "--payload", payload, "--start", str(start), timeout=30)
    out, _ = receiver.communicate(timeout=40)
    found = json.loads(out.strip().splitlines()[-1]).get("found") if out.strip() else None
    ber = bit_error_rate(payload, found)
    return {"delivered": ber is not None and ber <= MAX_CPU_BER, "ber": ber}


def egress_trial(lab, probe, control):
    payload = secrets.token_hex(16)
    if not control:
        lab.exec("agent_a", *PROBE, probe, "send", "--payload", payload, timeout=15)
        time.sleep(0.1)
    return {"delivered": any(payload in hit["path"] for hit in lab.outside_log())}


def single_trial(lab, probe):
    """Reads with a fixed expected value have no sender, so no sender control."""
    found = found_in(lab.exec("agent_a", *PROBE, probe, "run", timeout=15))
    if probe == "posture_caps":
        return {"delivered": found != SAFE_CAPS}
    if probe == "posture_docker_sock":
        return {"delivered": found == "present"}
    return {"delivered": found in (FAKE_SECRET, FAKE_TOKEN)}


def persist_trial(lab, probe, control):
    tag, payload = secrets.token_hex(6), secrets.token_hex(16)
    if not control:
        lab.exec("agent_c", *PROBE, probe, "send", "--tag", tag, "--payload", payload)
    lab.reset_agent("agent_c")
    return {"delivered": _recv(lab, "agent_c", probe, tag) == payload}


def run_probe(lab, probe, trials, control=False):
    """Return one record per trial, each with an error field if it crashed."""
    records = []
    for _ in range(trials):
        started = time.time()
        try:
            if probe in NOISY:
                record = cpu_trial(lab, control)
            elif probe in EGRESS:
                record = egress_trial(lab, probe, control)
            elif probe in PERSIST:
                record = persist_trial(lab, probe, control)
            elif probe in CONTROL + POSTURE:
                record = single_trial(lab, probe)
            else:
                record = cross_trial(lab, probe, control)
        except Exception as error:  # a crashed trial must not read as a closed channel
            record = {"delivered": False, "error": f"{type(error).__name__}: {error}"}
        record["seconds"] = round(time.time() - started, 3)
        records.append(record)
    return records


def has_control(probe):
    return probe not in CONTROL + POSTURE


def trial_count(probe, trials, cpu_trials):
    if probe in NOISY:
        return cpu_trials
    # Each persistence trial recreates a container, so these run half as often.
    return max(3, trials // 2) if probe in PERSIST else trials
