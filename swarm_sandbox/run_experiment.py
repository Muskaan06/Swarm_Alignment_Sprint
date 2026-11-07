"""Run every condition x seed (x model) in config/experiment.yaml.

  python run_experiment.py --agent stub_pooler --out pooler    # free: cooperative route works?
  python run_experiment.py --agent stub_exploit --out exploit  # free: solo flaw route works?
  python run_experiment.py --agent llm --out main              # real models (see config)

Existing logs are skipped, so an interrupted run can be resumed with the same command.
Conditions are interleaved per seed, so a run stopped by the budget stays balanced.
"""
import argparse
import re
import time
from pathlib import Path

import yaml

from agents.llm_agent import Budget, BudgetExceeded, LLMAgent, make_client
from agents.stub_agent import ExploitAgent, ObedientAgent, PoolerAgent
from run_episode import run_episode
from sandbox.tasks import PROMPTS
from sandbox.world import CONDITIONS

HERE = Path(__file__).resolve().parent


def load_config(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", default=str(HERE / "config" / "experiment.yaml"))
    p.add_argument("--agent", choices=["stub_pooler", "stub_exploit", "stub_obedient", "llm"])
    p.add_argument("--models", nargs="+")
    p.add_argument("--seeds", type=int)
    p.add_argument("--conditions", nargs="+", choices=list(CONDITIONS))
    p.add_argument("--prompts", nargs="+", choices=list(PROMPTS))
    p.add_argument("--rounds", type=int)
    p.add_argument("--out", default=None, help="subfolder of logs/")
    a = p.parse_args()

    cfg = load_config(a.config)
    agent_kind = a.agent or cfg["agent"]
    seeds = a.seeds or cfg["seeds"]
    conditions = a.conditions or cfg["conditions"]
    prompts = a.prompts or cfg.get("prompts", ["task"])
    rounds = a.rounds or cfg["rounds"]
    n, key_parts = cfg["n_agents"], cfg["key_parts"]
    out = HERE / "logs" / (a.out or agent_kind)
    llm_cfg = cfg.get("llm", {})
    models = (a.models or llm_cfg.get("models", [])) if agent_kind == "llm" else [agent_kind]
    budget = Budget(cfg.get("budget", {}).get("max_total_tokens", 0))

    for model in models:
        client = make_client(llm_cfg, model, budget) if agent_kind == "llm" else None
        for seed in range(seeds):
            for prompt, cond in [(pr, c) for pr in prompts for c in conditions]:
                stem = f"{prompt}__{cond}__{re.sub(r'[^A-Za-z0-9._-]', '_', model)}__{seed}"
                path = out / f"{stem}.jsonl"
                if path.exists():
                    continue

                def make_agent(name, task, names, _seed=seed):
                    if agent_kind == "stub_pooler":
                        return PoolerAgent(name, task, names)
                    if agent_kind == "stub_exploit":
                        return ExploitAgent(name, task, names)
                    if agent_kind == "stub_obedient":
                        return ObedientAgent(name, task, names)
                    return LLMAgent(name, task, names, rounds, client,
                                    seed=_seed * 100 + names.index(name),
                                    history_turns=llm_cfg.get("history_turns", 12))

                started = time.time()
                try:
                    run_episode(cond, seed, n, key_parts, rounds, make_agent, path,
                                meta={"model": model, "agent_kind": agent_kind}, prompt=prompt)
                except BudgetExceeded as e:
                    path.unlink(missing_ok=True)   # incomplete episode: do not count it
                    print(f"STOP: {e}. Used {budget.used} tokens.")
                    return
                print(f"{model:20s} seed={seed:<3d} {prompt:5s} {cond:20s} {time.time() - started:6.1f}s "
                      f"tokens_so_far={budget.used}", flush=True)
    print(f"done. logs in {out}. total tokens: {budget.used}")


if __name__ == "__main__":
    main()
