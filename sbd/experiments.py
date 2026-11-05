"""Scripted experiments: the scenario matrix and the planted-flaw suite."""
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import platform
import subprocess

from .lab import Lab, ROOT
from .probes import ALL_PROBES, has_control, run_probe, trial_count
from .scenarios import MUTANTS, SCENARIOS, apply
from .stats import clopper_pearson, verdict

RESULTS = ROOT / "results"


def manifest(kind, **extra):
    predictions = ROOT / "predictions.json"
    docker = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, text=True)
    return {
        "kind": kind,
        "utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "machine": platform.machine(),
        "system": platform.system(),
        "docker": docker.stdout.strip(),
        "predictions_sha256": hashlib.sha256(predictions.read_bytes()).hexdigest(),
        **extra,
    }


def measure(lab, probes, trials, cpu_trials, log=print, controls=True):
    """Run all probes on a live lab and summarise them."""
    summary = {}
    for probe in probes:
        count = trial_count(probe, trials, cpu_trials)
        records = run_probe(lab, probe, count)
        control_records = run_probe(lab, probe, count, control=True) if controls and has_control(probe) else []
        delivered = sum(r["delivered"] for r in records)
        low, high = clopper_pearson(delivered, count)
        errors = [r["error"] for r in records + control_records if "error" in r]
        bers = [r["ber"] for r in records if r.get("ber") is not None]
        control_bers = [r["ber"] for r in control_records if r.get("ber") is not None]
        summary[probe] = {
            "trials": count,
            "delivered": delivered,
            "ci95": [round(low, 3), round(high, 3)],
            "verdict": "ERROR" if errors else verdict(delivered, count),
            "control_trials": len(control_records),
            "control_delivered": sum(r["delivered"] for r in control_records),
            "errors": errors[:3],
            "mean_ber": round(sum(bers) / len(bers), 4) if bers else None,
            "control_mean_ber": round(sum(control_bers) / len(control_bers), 4) if control_bers else None,
            "mean_seconds": round(sum(r["seconds"] for r in records) / count, 3),
        }
        log(f"  {probe:24} {summary[probe]['verdict']:8} {delivered}/{count} "
            f"control {summary[probe]['control_delivered']}/{len(control_records)}")
    return summary


def run_matrix(trials=10, cpu_trials=5, scenarios=None, out="scripted", probes=None):
    directory = RESULTS / out
    directory.mkdir(parents=True, exist_ok=True)
    results = {"manifest": manifest("matrix", trials=trials, cpu_trials=cpu_trials,
                                    cpu_slot=float(os.environ.get("CPU_SLOT", "0.25"))), "scenarios": {}}
    for name in scenarios or SCENARIOS:
        label, cfg = SCENARIOS[name]
        print(f"{name}: {label}", flush=True)
        with Lab(name, cfg) as lab:
            results["scenarios"][name] = {"label": label, "config": dataclasses.asdict(cfg),
                                          "probes": measure(lab, probes or ALL_PROBES, trials, cpu_trials)}
        (directory / "matrix.json").write_text(json.dumps(results, indent=2))
    return results


def open_set(summary):
    return {probe for probe, row in summary.items() if row["verdict"] in ("OPEN", "PARTIAL")}


def run_mutants(trials=5, cpu_trials=3, out="scripted", selected=None):
    """Compare each mutant with a fresh measurement of its base scenario."""
    directory = RESULTS / out
    directory.mkdir(parents=True, exist_ok=True)
    results = {"manifest": manifest("mutants", trials=trials, cpu_trials=cpu_trials), "baselines": {}, "mutants": {}}
    for base in sorted({m[2] for m in MUTANTS}):
        print(f"baseline {base}", flush=True)
        with Lab(f"base{base}", SCENARIOS[base][1]) as lab:
            results["baselines"][base] = measure(lab, ALL_PROBES, trials, cpu_trials, controls=False)
    for key, description, base, overrides, kind in MUTANTS:
        if selected and key not in selected:
            continue
        print(f"{key}: {description}", flush=True)
        try:
            with Lab(key, apply(base, overrides)) as lab:
                summary = measure(lab, ALL_PROBES, trials, cpu_trials, log=lambda *_: None, controls=False)
        except Exception as error:
            results["mutants"][key] = {"description": description, "base": base, "kind": kind,
                                       "error": f"{type(error).__name__}: {error}"[:300], "new_open": []}
            print(f"  FAILED TO RUN: {error}", flush=True)
            continue
        new = sorted(open_set(summary) - open_set(results["baselines"][base]))
        results["mutants"][key] = {"description": description, "base": base, "kind": kind,
                                   "new_open": new, "summary": summary}
        print(f"  detected by: {new or 'nothing'}", flush=True)
        (directory / "mutants.json").write_text(json.dumps(results, indent=2))
    (directory / "mutants.json").write_text(json.dumps(results, indent=2))
    return results


def run_cpu_pairs(trials=5, out="scripted"):
    """Measure the CPU channel in S7 for a pinned-apart pair and a same-CPU pair."""
    from .probes import cpu_trial
    directory = RESULTS / out
    directory.mkdir(parents=True, exist_ok=True)
    results = {}
    with Lab("cpupairs", SCENARIOS["S7"][1]) as lab:
        for peer, label in (("agent_b", "different_cpu"), ("agent_c", "same_cpu")):
            sent = [cpu_trial(lab, False, peer) for _ in range(trials)]
            controls = [cpu_trial(lab, True, peer) for _ in range(trials)]
            results[label] = {
                "receiver": peer, "trials": trials,
                "delivered": sum(r["delivered"] for r in sent),
                "mean_ber": round(sum(r["ber"] for r in sent) / trials, 4),
                "control_delivered": sum(r["delivered"] for r in controls),
                "control_mean_ber": round(sum(r["ber"] for r in controls) / trials, 4),
            }
            print(label, results[label], flush=True)
    (directory / "cpu_pairs.json").write_text(json.dumps(results, indent=2))
    return results
