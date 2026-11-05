"""Turn result files into the tables and figures used in the report."""
import csv
import json
import pathlib

from .lab import ROOT
from .probes import ALL_PROBES, NOISY
from .scenarios import MUTANTS, SCENARIOS
from .stats import capacity_bits_per_second, clopper_pearson

GROUPS = [
    ("Cross-agent", ["dirname", "upload", "props", "cachefill", "presence", "dead_drop",
                     "direct_tcp", "shared_fs", "proc", "log_read", "cpu"]),
    ("Egress", ["egress_direct", "egress_ssrf", "egress_pull_path", "egress_dns"]),
    ("Control plane", ["control_scorer_direct", "control_scorer_ssrf", "control_secret_env",
                       "control_metadata", "posture_caps", "posture_docker_sock"]),
    ("Persistence", ["persist_cache", "persist_fs"]),
]
LABELS = {
    "dirname": "Directory names", "upload": "File upload", "props": "Entry properties",
    "cachefill": "Cached miss names", "presence": "Cache timing", "dead_drop": "Outside dead drop",
    "direct_tcp": "Direct TCP", "shared_fs": "Shared volume", "proc": "Process list",
    "log_read": "Readable logs", "cpu": "CPU contention",
    "egress_direct": "Direct HTTP", "egress_ssrf": "Gateway fetch", "egress_pull_path": "Pull-through path",
    "egress_dns": "DNS forwarding", "control_scorer_direct": "Scorer, direct", "control_scorer_ssrf": "Scorer, via gateway",
    "control_secret_env": "Secret in environment", "control_metadata": "Metadata service",
    "posture_caps": "Elevated capabilities", "posture_docker_sock": "Docker socket path",
    "persist_cache": "Cache survives reset", "persist_fs": "Disk survives reset",
}
INK, MUTED, RULE = "#1f2328", "#5b6470", "#d5d9df"
RAMP = ["#f1f3f5", "#cfe0f3", "#7fb0e3", "#2f6db5"]


def compare_predictions(matrix):
    predictions = json.loads((ROOT / "predictions.json").read_text())["open"]
    misses = []
    for scenario, data in matrix["scenarios"].items():
        expected = set(predictions[scenario])
        for probe, row in data["probes"].items():
            measured = row["verdict"] in ("OPEN", "PARTIAL")
            if measured != (probe in expected):
                misses.append({"scenario": scenario, "probe": probe, "predicted_open": probe in expected,
                               "verdict": row["verdict"], "delivered": row["delivered"], "trials": row["trials"]})
    return misses


def write_matrix_csv(matrix, path):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["scenario", "probe", "delivered", "trials", "ci_low", "ci_high", "verdict",
                         "control_delivered", "control_trials", "mean_ber", "control_mean_ber"])
        for scenario, data in matrix["scenarios"].items():
            for probe, row in data["probes"].items():
                low, high = clopper_pearson(row["delivered"], row["trials"])
                writer.writerow([scenario, probe, row["delivered"], row["trials"], round(low, 3), round(high, 3), row["verdict"],
                                 row["control_delivered"], row["control_trials"], row["mean_ber"],
                                 row["control_mean_ber"]])


def figure(matrix, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    scenarios = list(matrix["scenarios"])
    def ever_open(probe):
        return any(data["probes"][probe]["delivered"] for data in matrix["scenarios"].values())

    # Probes that stayed closed everywhere are left out and named in the caption.
    groups = [(name, [p for p in probes if ever_open(p)]) for name, probes in GROUPS]
    rows = []
    for name, probes in groups:
        if probes:
            rows.append(("header", name))
            rows.extend(("probe", probe) for probe in probes)
    height = 0.27 * len(rows) + 0.6
    fig, ax = plt.subplots(figsize=(7.4, height), dpi=200)
    for y, (kind, value) in enumerate(rows):
        if kind == "header":
            ax.text(-0.1, y + 0.62, value, ha="right", va="center", fontsize=7.5, weight="bold", color=INK)
            continue
        ax.text(-0.1, y + 0.5, LABELS[value], ha="right", va="center", fontsize=7, color=MUTED)
        for x, scenario in enumerate(scenarios):
            row = matrix["scenarios"][scenario]["probes"][value]
            rate = row["delivered"] / row["trials"] if row["trials"] else 0
            if row["verdict"] == "ERROR":
                color, text = "#ffffff", "error"
            else:
                color = RAMP[0] if rate == 0 else RAMP[1] if rate < 0.5 else RAMP[2] if rate < 0.8 else RAMP[3]
                text = f"{row['delivered']}/{row['trials']}"
            ax.add_patch(plt.Rectangle((x + 0.03, y + 0.05), 0.94, 0.9, color=color, linewidth=0))
            ax.text(x + 0.5, y + 0.5, text, ha="center", va="center", fontsize=6.3,
                    color="#ffffff" if rate >= 0.8 else INK if rate > 0 else MUTED)
    for x, scenario in enumerate(scenarios):
        ax.text(x + 0.5, -0.25, scenario, ha="center", va="bottom", fontsize=8, weight="bold", color=INK)
    ax.set_xlim(0, len(scenarios))
    ax.set_ylim(len(rows), -0.2)
    ax.axis("off")
    fig.subplots_adjust(left=0.27, right=0.99, top=0.95, bottom=0.01)
    fig.savefig(path)
    plt.close(fig)


def markdown_summary(matrix, misses):
    lines = ["| Scenario | Cache | Writes | Outside | Cross-agent open | Egress open | Persistence open |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    groups = dict(GROUPS)
    for name, data in matrix["scenarios"].items():
        config, probes = data["config"], data["probes"]
        def open_labels(group):
            return ", ".join(LABELS[p] for p in groups[group] if p in probes and probes[p]["verdict"] in ("OPEN", "PARTIAL")) or "none"
        lines.append(f"| {name} | {config['topology']} | {config['cache_mode']} | {'yes' if config['outside'] else 'no'} | "
                     f"{open_labels('Cross-agent')} | {open_labels('Egress')} | {open_labels('Persistence')} |")
    return "\n".join(lines)


def mutant_table(mutants):
    lines = ["| ID | Planted flaw | Base | Set | Caught by |", "| --- | --- | --- | --- | --- |"]
    rates = {}
    for key, description, base, _, kind in MUTANTS:
        item = mutants["mutants"].get(key)
        if item is None:
            continue
        detected = bool(item["new_open"])
        caught = "did not run" if "error" in item else ", ".join(LABELS[p] for p in item["new_open"]) or "nothing"
        lines.append(f"| {key} | {description} | {base} | {kind} | {caught} |")
        if "error" not in item:
            hit, total = rates.get(kind, (0, 0))
            rates[kind] = (hit + detected, total + 1)
    return "\n".join(lines), rates


def llm_table(directory):
    runs = [json.loads(path.read_text()) for path in sorted(directory.glob("*.result.json"))]
    grouped = {}
    for run in runs:
        key = (run["model"], run["scenario"], run["task"])
        grouped.setdefault(key, []).append(run)
    lines = ["| Model | Scenario | Task | Runs | Cross-agent | Egress | Cache writes attempted | Cost (USD) |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for (model, scenario, task), items in sorted(grouped.items()):
        lines.append(f"| {model} | {scenario} | {task} | {len(items)} | "
                     f"{sum(i['cross_agent_delivered'] for i in items)}/{len(items)} | "
                     f"{sum(i['egress_delivered'] for i in items)}/{len(items)} | "
                     f"{sum(i['cache_writes'] for i in items)} | {sum(i['cost_usd'] for i in items):.3f} |")
    return "\n".join(lines), runs


def build(out="scripted", llm="llm"):
    source = ROOT / "results" / out
    target = source / "report"
    target.mkdir(parents=True, exist_ok=True)
    sections = []
    matrix_path = source / "matrix.json"
    if matrix_path.exists():
        matrix = json.loads(matrix_path.read_text())
        write_matrix_csv(matrix, target / "matrix.csv")
        figure(matrix, target / "figure2_matrix.png")
        misses = compare_predictions(matrix)
        (target / "prediction_misses.json").write_text(json.dumps(misses, indent=2))
        sections.append("## Scenario summary\n\n" + markdown_summary(matrix, misses))
        sections.append(f"## Prediction misses\n\n{len(misses)} cells differed.\n\n```json\n{json.dumps(misses, indent=1)}\n```")
        cpu = []
        for scenario, data in matrix["scenarios"].items():
            row = data["probes"].get("cpu")
            if row and row["mean_ber"] is not None:
                slot = float(matrix["manifest"].get("cpu_slot", 0.25))
                cpu.append(f"| {scenario} | {row['mean_ber']} | {row['control_mean_ber']} | "
                           f"{capacity_bits_per_second(row['mean_ber'], slot):.2f} |")
        sections.append("## CPU channel\n\n| Scenario | Mean BER | Control BER | Capacity estimate (bit/s) |\n| --- | --- | --- | --- |\n" + "\n".join(cpu))
    mutants_path = source / "mutants.json"
    if mutants_path.exists():
        table, rates = mutant_table(json.loads(mutants_path.read_text()))
        lines = [table, ""]
        for kind, (hit, total) in rates.items():
            low, high = clopper_pearson(hit, total)
            lines.append(f"- {kind}: {hit}/{total} flagged (95% CI {low:.2f} to {high:.2f})")
        sections.append("## Planted flaws\n\n" + "\n".join(lines))
    pairs_path = source / "cpu_pairs.json"
    if pairs_path.exists():
        sections.append("## CPU pairs in S7\n\n```json\n" + pairs_path.read_text() + "\n```")
    real_path = source / "realcheck.json"
    if real_path.exists():
        sections.append("## Real mirror check\n\n```json\n" + real_path.read_text() + "\n```")
    llm_dir = ROOT / "results" / llm
    if llm_dir.exists():
        table, runs = llm_table(llm_dir)
        sections.append(f"## LLM runs\n\n{len(runs)} runs.\n\n{table}")
    (target / "summary.md").write_text("\n\n".join(sections) + "\n")
    print(f"wrote {target}")
