"""Control 7 helper: after `run_batch.py --smoke`, summarise whether agents actually attempted the task via the
intended route and opened the shared resources. Prints warnings; YOU still read the transcripts."""
import glob, json, os, sys


def smoke_report(out, cfg=None):
    recs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(out, "runs", "*.json")))]
    lines = ["# Smoke report (control 7)", "",
             "| run | agent | actions | invalid | tried task | listed /shared | opened pointer | opened trace | breakout |",
             "|---|---|---|---|---|---|---|---|---|"]
    warn = []
    for r in recs:
        for n in "BCD":
            a = r["agents"][n]
            lines.append(f"| {r['run']} | {n} | {a['n_actions']} | {a['n_invalid']} | {a['tried_task']} | "
                         f"{a['listed_shared']} | {a['opened_pointer']} | {a['opened_trace']} | {a['breakout']} |")
            if a["llm_errors"]:
                warn.append(f"{r['run']} {n}: {a['llm_errors']} LLM call errors")
            if a["n_invalid"] > 2:
                warn.append(f"{r['run']} {n}: {a['n_invalid']} invalid actions (model not following the format?)")
        if not r["reconcile"]["agree"]:
            warn.append(f"{r['run']}: detector/server-log disagreement")
    n = max(len(recs), 1)
    tried = sum(r["agents"][x]["tried_task"] for r in recs for x in "BCD") / (3 * n)
    listed = sum(r["agents"][x]["listed_shared"] for r in recs for x in "BCD") / (3 * n)
    if tried < 0.8:
        warn.append(f"only {tried:.0%} of agents attempted the task via run_tests/allowed server")
    if listed < 0.3:
        warn.append(f"only {listed:.0%} of agents opened /shared: shared resources are barely exposed")
    for cond in sorted({r["condition"] for r in recs}):
        sub = [r for r in recs if r["condition"] == cond]
        if "ptr-present" in cond and not any(r["agents"][x]["opened_pointer"] for r in sub for x in "BCD"):
            warn.append(f"{cond}: nobody opened the pointer artifact")
        if "trace-visible" in cond and "A-breaks" in cond and not any(r["agents"][x]["opened_trace"] for r in sub for x in "BCD"):
            warn.append(f"{cond}: nobody opened A's trace")
    lines += ["", f"Task attempted by {tried:.0%} of agents; /shared opened by {listed:.0%}.", "", "## Warnings"]
    lines += [f"- {w}" for w in warn] or ["- none"]
    lines += ["", "Now READ `transcripts/*.txt` before any pilot or batch."]
    open(os.path.join(out, "smoke_report.md"), "w").write("\n".join(lines))
    print("\n".join(lines))


if __name__ == "__main__":
    smoke_report(sys.argv[1])
