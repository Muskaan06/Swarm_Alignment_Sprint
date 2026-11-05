"""Command line: python -m sbd <command>. See README.md."""
import argparse
import subprocess
import sys

from . import experiments, llm_client, llm_run, mock_llm, report
from .lab import ROOT, Lab
from .scenarios import SCENARIOS


def build(args):
    subprocess.run(["docker", "build", "--build-arg", f"BASE_IMAGE={args.base_image}",
                    "-t", "sbd-lab:latest", str(ROOT / "images")], check=True)


def verify(_):
    """Prove the outer boundary before anything else runs."""
    with Lab("verify", SCENARIOS["S1"][1]) as lab:
        script = ("import urllib.request as u,sys\n"
                  "try:\n  u.build_opener(u.ProxyHandler({})).open('http://1.1.1.1',timeout=3); print('REACHED')\n"
                  "except Exception as e: print('blocked')")
        for service in ("agent_a", "cache", "decoy"):
            print(service, lab.exec(service, "python3", "-c", script)[1].strip())


def llm(args):
    server = None
    if args.mock:
        server = mock_llm.serve()
        args.provider, args.base_url, args.api_key = "mock", f"http://127.0.0.1:{server.server_port}", "mock"
    client = llm_client.LLMClient(args.provider, args.base_url, args.api_key, args.budget,
                                  args.input_price, args.output_price)
    results = llm_run.run_grid(args.models, args.scenarios, args.tasks, args.seeds, args.rounds, client, args.out)
    print(f"{len(results)} runs, ${client.spent_usd:.4f} spent")
    if not client.cost_reported and args.input_price is None:
        print("WARNING: the provider reported no cost and no price was given, so the budget was not enforced.")


def main():
    parser = argparse.ArgumentParser(prog="sbd")
    commands = parser.add_subparsers(dest="command", required=True)

    command = commands.add_parser("build")
    command.add_argument("--base-image", default="python:3.12-slim")
    command.set_defaults(run=build)

    commands.add_parser("verify").set_defaults(run=verify)

    command = commands.add_parser("matrix")
    command.add_argument("--trials", type=int, default=10)
    command.add_argument("--cpu-trials", type=int, default=5)
    command.add_argument("--scenarios", nargs="*", default=None)
    command.add_argument("--out", default="scripted")
    command.set_defaults(run=lambda a: experiments.run_matrix(a.trials, a.cpu_trials, a.scenarios, a.out))

    command = commands.add_parser("mutants")
    command.add_argument("--trials", type=int, default=3)
    command.add_argument("--cpu-trials", type=int, default=3)
    command.add_argument("--only", nargs="*", default=None)
    command.add_argument("--out", default="scripted")
    command.set_defaults(run=lambda a: experiments.run_mutants(a.trials, a.cpu_trials, a.out, a.only))

    command = commands.add_parser("cpupairs")
    command.add_argument("--trials", type=int, default=5)
    command.add_argument("--out", default="scripted")
    command.set_defaults(run=lambda a: experiments.run_cpu_pairs(a.trials, a.out))

    command = commands.add_parser("llm")
    command.add_argument("--models", nargs="+", default=["qwen/qwen3-coder"])
    command.add_argument("--provider", default="openrouter", help="openrouter, together, groq, ollama, lmstudio")
    command.add_argument("--base-url", default=None)
    command.add_argument("--api-key", default=None)
    command.add_argument("--scenarios", nargs="+", default=["S1", "S5", "S4"])
    command.add_argument("--tasks", nargs="+", default=["directed_cross", "directed_egress"],
                         choices=list(llm_run.PROMPTS))
    command.add_argument("--seeds", type=int, default=3)
    command.add_argument("--rounds", type=int, default=20)
    command.add_argument("--budget", type=float, default=5.0, help="hard limit in USD for this command")
    command.add_argument("--input-price", type=float, default=None, help="USD per million tokens, if not reported")
    command.add_argument("--output-price", type=float, default=None)
    command.add_argument("--out", default="llm")
    command.add_argument("--mock", action="store_true", help="use the scripted test server")
    command.set_defaults(run=llm)

    command = commands.add_parser("report")
    command.add_argument("--out", default="scripted")
    command.add_argument("--llm", default="llm")
    command.set_defaults(run=lambda a: report.build(a.out, a.llm))

    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    sys.exit(main())
