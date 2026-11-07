"""Run experiments.  Examples:
  python run_batch.py --mode both --smoke                 # 1 seed per arm, then inspect transcripts
  python run_batch.py --pilot                             # 5 seeds, baseline arm, reports baseline rate
  python run_batch.py --freeze                            # freeze settings after calibration
  python run_batch.py --mode both --seeds 20 --out results/main
  python run_batch.py --mode both --seeds 10 --out results/stub --stub   # stub agents, tests the pipeline only
"""
import argparse, json, os, shutil, sys, time
import requests
from Swarm_Alignment_Sprint.harness.config import load_config, env_hash, full_hash, arms_for_mode, baseline_arm, ROOT
from Swarm_Alignment_Sprint.harness.episode import run_episode, default_agents

FROZEN = os.path.join(ROOT, "configs", "frozen.json")
CONTROLS = os.path.join(ROOT, "results", "controls_ok.json")


def make_stub(cfg, world, arm, seed):
    from Swarm_Alignment_Sprint.agents.scripted import ConfederateA
    from Swarm_Alignment_Sprint.agents.stub_llm import StubLLM
    ag = {"A": ConfederateA(arm.a_behavior)}
    for i, n in enumerate(cfg["design"]["llm_agents"]):
        ag[n] = StubLLM(n, world, cfg, seed, i)
    return ag


def preflight(cfg):
    L = cfg["llm"]
    try:
        r = requests.post(L["base_url"].rstrip("/") + "/chat/completions", timeout=L["timeout_s"],
                          headers={"Authorization": f"Bearer {L['api_key'] or 'none'}"},
                          json={"model": L["model"], "max_tokens": 5, "messages": [{"role": "user", "content": "hi"}]})
        r.raise_for_status()
    except Exception as e:
        sys.exit(f"LLM endpoint check failed ({L['base_url']}, model {L['model']}): {e}\n"
                 "Start Ollama and `ollama pull` the model, or set LLM_BASE_URL / LLM_MODEL.")


def gate(cfg, args):
    if args.stub:
        return
    if not os.path.exists(CONTROLS) or json.load(open(CONTROLS))["env_hash"] != env_hash(cfg):
        sys.exit("Controls have not passed for this config. Run: python -m controls.run_controls")
    if not (args.smoke or args.pilot or args.allow_unfrozen):
        if not os.path.exists(FROZEN):
            sys.exit("No frozen settings. Run --smoke, --pilot, calibrate, then `python run_batch.py --freeze` "
                     "(or pass --allow-unfrozen).")
        if json.load(open(FROZEN))["full_hash"] != full_hash(cfg):
            sys.exit("Config differs from the frozen settings. Revert it or re-freeze deliberately.")


def dump_transcript(path, rec):
    with open(path, "w") as f:
        for n, tr in rec["transcripts"].items():
            f.write(f"===== agent {n} =====\n")
            for t in tr or []:
                f.write(f"[round {t.get('round')}] {json.dumps(t, ensure_ascii=False)}\n")
        f.write("\n===== executor events =====\n")
        for e in rec["events"]:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["opportunity", "contagion", "both"], default="both")
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--seed-start", type=int, default=0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--config", default=None)
    ap.add_argument("--smoke", action="store_true", help="1 seed per arm")
    ap.add_argument("--pilot", action="store_true", help="baseline arm only, pilot.seeds seeds")
    ap.add_argument("--freeze", action="store_true")
    ap.add_argument("--allow-unfrozen", action="store_true")
    ap.add_argument("--stub", action="store_true", help="scripted stand-in for the LLM (pipeline test only)")
    args = ap.parse_args()
    cfg = load_config(args.config)

    if args.freeze:
        json.dump({"full_hash": full_hash(cfg), "env_hash": env_hash(cfg), "time": time.ctime(),
                   "model": cfg["llm"]["model"], "temperature": cfg["llm"]["temperature"],
                   "pressure": cfg["task"]["pressure"], "dataset_rows": cfg["task"]["dataset_rows"],
                   "rounds": cfg["design"]["rounds"], "actions_per_round": cfg["design"]["actions_per_round"]},
                  open(FROZEN, "w"), indent=1)
        shutil.copy(cfg["_path"], os.path.join(ROOT, "configs", "frozen_experiment.yaml"))
        print("Frozen:", open(FROZEN).read())
        return

    gate(cfg, args)
    if args.pilot:
        arms, seeds, out = [baseline_arm(cfg)], range(args.seed_start, args.seed_start + cfg["pilot"]["seeds"]), args.out or "results/pilot"
    elif args.smoke:
        arms, seeds, out = arms_for_mode(cfg, args.mode), [args.seed_start], args.out or "results/smoke"
    else:
        arms, seeds = arms_for_mode(cfg, args.mode), range(args.seed_start, args.seed_start + args.seeds)
        out = args.out or f"results/{args.mode}"
    out = out if os.path.isabs(out) else os.path.join(ROOT, out)
    os.makedirs(os.path.join(out, "runs"), exist_ok=True)
    os.makedirs(os.path.join(out, "transcripts"), exist_ok=True)
    if not args.stub:
        preflight(cfg)
    shutil.copy(cfg["_path"], os.path.join(out, "config_used.yaml"))
    json.dump({"mode": args.mode, "full_hash": full_hash(cfg), "env_hash": env_hash(cfg), "stub": args.stub,
               "model": cfg["llm"]["model"] if not args.stub else "STUB", "arms": [a.name for a in arms]},
              open(os.path.join(out, "meta.json"), "w"), indent=1)

    total = len(arms) * len(list(seeds)); done = 0; t0 = time.time()
    for seed in seeds:              # seeds outer, arms inner: partial results stay paired
        for arm in arms:
            rid = f"{arm.name}__s{seed}"
            path = os.path.join(out, "runs", rid + ".json")
            done += 1
            if os.path.exists(path):
                continue
            rec, _ = run_episode(cfg, arm, seed, make_agents=make_stub if args.stub else default_agents)
            if args.stub:
                rec["model"] = "STUB"
            json.dump(rec, open(path, "w"), indent=1)
            dump_transcript(os.path.join(out, "transcripts", rid + ".txt"), rec)
            b = {n: rec["agents"][n]["breakout"] for n in "BCD"}
            print(f"[{done}/{total}] {rid} breakouts B/C/D={[int(v) for v in b.values()]} "
                  f"{rec['seconds']}s elapsed={int(time.time()-t0)}s", flush=True)

    if args.pilot:
        from Swarm_Alignment_Sprint.analyze import pilot_report
        pilot_report(out, cfg)
    if args.smoke:
        from Swarm_Alignment_Sprint.controls.smoke_report import smoke_report
        smoke_report(out, cfg)


if __name__ == "__main__":
    main()
