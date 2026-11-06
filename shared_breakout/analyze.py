"""Summarise results/<run>/: breakout rate per condition with Wilson 95% CI and Fisher exact test.

  python analyze.py pilot
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def fisher_two_sided(a, b, c, d):
    """Two-sided Fisher exact test on [[a, b], [c, d]] (stdlib only)."""
    n1, n2, k = a + b, c + d, a + c
    total = math.comb(n1 + n2, k)
    prob = lambda x: math.comb(n1, x) * math.comb(n2, k - x) / total
    p_obs = prob(a)
    return min(1.0, sum(prob(x) for x in range(max(0, k - n2), min(n1, k) + 1) if prob(x) <= p_obs + 1e-12))


def main(run):
    rows = [json.loads(p.read_text()) for p in sorted((HERE / "results" / run).glob("*.result.json"))]
    if not rows:
        sys.exit(f"no results in results/{run}")
    by = defaultdict(list)
    for r in rows:
        by[(r["model"], r["condition"])].append(r)
    lines = ["| model | condition | n | breakouts | rate | 95% CI | mean pooling pairs | mean calls |",
             "|---|---|---|---|---|---|---|---|"]
    for (model, cond), rs in sorted(by.items()):
        k, n = sum(r["breakout"] for r in rs), len(rs)
        lo, hi = wilson(k, n)
        lines.append(f"| {model} | {cond} | {n} | {k} | {k / n:.2f} | [{lo:.2f}, {hi:.2f}] | "
                     f"{sum(len(r['pooling_pairs']) for r in rs) / n:.1f} | {sum(r['tool_calls'] for r in rs) / n:.1f} |")
    for model in sorted({m for m, _ in by}):
        s, i = by.get((model, "shared"), []), by.get((model, "isolated"), [])
        if s and i:
            a, c = sum(r["breakout"] for r in s), sum(r["breakout"] for r in i)
            lines.append(f"\nFisher exact, {model}: shared {a}/{len(s)} vs isolated {c}/{len(i)}, "
                         f"p = {fisher_two_sided(a, len(s) - a, c, len(i) - c):.4f}")
    text = "\n".join(lines)
    print(text)
    (HERE / "results" / run / "summary.md").write_text(text + "\n")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        keys = sorted(by)
        rates = [sum(r["breakout"] for r in by[k]) / len(by[k]) for k in keys]
        errs = [[r - wilson(sum(x["breakout"] for x in by[k]), len(by[k]))[0] for r, k in zip(rates, keys)],
                [wilson(sum(x["breakout"] for x in by[k]), len(by[k]))[1] - r for r, k in zip(rates, keys)]]
        fig, ax = plt.subplots(figsize=(5, 3.5))
        ax.bar([f"{m}\n{c}" for m, c in keys], rates, yerr=errs, capsize=4)
        ax.set_ylim(0, 1)
        ax.set_ylabel("breakout rate (95% Wilson CI)")
        fig.tight_layout()
        fig.savefig(HERE / "results" / run / "breakout_rate.png", dpi=150)
    except ImportError:
        print("(matplotlib not installed: skipped plot)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "pilot")
