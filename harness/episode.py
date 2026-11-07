"""Run one episode (one seed in one arm)."""
import time
from .config import Arm
from .world import World
from .executor import Executor
from . import detector
from Swarm_Alignment_Sprint.servers.allowed_server import make_allowed
from Swarm_Alignment_Sprint.servers.decoy_server import make_decoy


class Handle:
    def __init__(self, executor, agent, rnd, world):
        self.executor, self.agent, self.rnd, self.world = executor, agent, rnd, world

    def do(self, tool, **args):
        return self.executor.run(self.agent, self.rnd, tool, args)


def default_agents(cfg, world, arm, seed):
    from Swarm_Alignment_Sprint.agents.scripted import ConfederateA
    from Swarm_Alignment_Sprint.agents.llm_agent import LLMAgent
    ag = {"A": ConfederateA(arm.a_behavior)}
    for i, n in enumerate(cfg["design"]["llm_agents"]):
        ag[n] = LLMAgent(n, world, cfg, seed, i)
    return ag


def run_episode(cfg, arm: Arm, seed, agents=None, make_agents=None, run_tag=""):
    """agents: dict name->agent (control runs) or make_agents(cfg, world, arm, seed) -> dict."""
    t0 = time.time()
    world = World(cfg, arm, seed)
    run_id = f"{arm.name}__s{seed}{run_tag}"
    server_log = []
    host = cfg["servers"]["host"]
    srvs = [make_allowed(world, server_log, host, world.allowed_port),
            make_decoy(world, server_log, host, world.decoy_port)]
    ex = Executor(world, run_id, arm.name, seed, server_log)
    try:
        ag = agents if agents is not None else (make_agents or default_agents)(cfg, world, arm, seed)
        order = cfg["design"]["agents"]
        for rnd in range(1, cfg["design"]["rounds"] + 1):
            for name in order:
                if name in ag:
                    ag[name].take_turn(Handle(ex, name, rnd, world), rnd)
            if all(n in ex.finished for n in cfg["design"]["llm_agents"] if n in ag):
                break
    finally:
        for s in srvs:
            s.shutdown(); s.server_close()
    rec = detector.reconcile(world, ex.events, server_log)
    summary = {}
    for n in order:
        evs = [e for e in ex.events if e["agent"] == n]
        br = [e for e in evs if e["breakout_type"]]
        tags = {t for e in evs for t in e["info"]}
        summary[n] = {
            "breakout": bool(br),
            "types": sorted({t for e in br for t in e["breakout_type"].split("|")}),
            "first_round": min((e["round"] for e in br), default=None),
            "n_actions": len(evs), "n_invalid": sum(1 for e in evs if "invalid_action" in e["info"]),
            "listed_shared": "listed_shared" in tags, "opened_pointer": "opened_pointer" in tags,
            "opened_trace": "opened_trace" in tags, "tests_passed": n in ex.passed,
            "tried_task": any(e["action"] in ("run_tests", "http_get") for e in evs),
            "tokens": getattr(ag.get(n), "tokens", 0), "llm_errors": getattr(ag.get(n), "errors", 0),
        }
    return {
        "run": run_id, "seed": seed, "condition": arm.name, "pointer": arm.pointer, "trace": arm.trace,
        "a_behavior": arm.a_behavior, "model": cfg["llm"]["model"], "temperature": cfg["llm"]["temperature"],
        "env_hash": __import__("harness.config", fromlist=["x"]).env_hash(cfg),
        "events": ex.events, "server_log": server_log, "reconcile": rec, "agents": summary,
        "transcripts": {n: getattr(a, "transcript", None) for n, a in ag.items() if hasattr(a, "transcript")},
        "world": {"canary_token": world.canary_data_token, "canary_key": world.canary_key},
        "seconds": round(time.time() - t0, 1),
    }, ag
