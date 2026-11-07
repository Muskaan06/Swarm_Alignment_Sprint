"""Run one episode: n agents, one condition, one seed. Writes one JSONL log.

The episode ends on the first breakout, when every agent has submitted, or after `rounds`.
"""
import random

from agents.llm_agent import BudgetExceeded
from sandbox.logger import EpisodeLogger
from sandbox.tasks import make_tasks
from sandbox.world import World

FORMAT_HINT = 'Reply with exactly one JSON object, e.g. {"tool": "ws_list", "args": {}}'


def run_episode(condition, seed, n_agents, key_parts, rounds, make_agent, log_path, meta=None,
                prompt="task"):
    """make_agent(name, task, agent_names) -> object with .act(observation) -> (action, raw, error)."""
    t = make_tasks(n_agents, key_parts, seed, prompt)
    names = t["agents"]
    world = World(condition, names, t["workspaces"], t["key"], t["token"])
    agents = {a: make_agent(a, t["tasks"][a], names) for a in names}
    obs = {a: None for a in names}
    answers, errors = {}, {}
    order_rng = random.Random(f"order-{seed}")   # same turn order in every condition
    log = EpisodeLogger(log_path)
    log.write({"type": "episode_start", "condition": condition, "prompt": prompt, "seed": seed, "n_agents": n_agents,
               "key_parts": key_parts, "rounds": rounds, "shared_services": sorted(world.shared),
               "flaw": world.flaw, "agents": names, "parts": t["parts"], "part_of": t["part_of"],
               "key": t["key"], "token": t["token"], "secrets": t["secrets"], **(meta or {})})
    aborted = None
    try:
        for r in range(rounds):
            world.round = r
            live = [a for a in names if a not in answers and a not in errors]
            if not live:
                break
            order_rng.shuffle(live)
            for a in live:
                try:
                    action, raw, err = agents[a].act(obs[a])
                except RuntimeError as e:          # model/API failure: this agent stops
                    errors[a] = str(e)[:300]
                    log.write({"type": "agent_error", "round": r, "agent": a, "error": errors[a]})
                    continue
                if action is None:
                    obs[a] = f"error: {err}. {FORMAT_HINT}"
                    log.write({"type": "step", "round": r, "agent": a, "tool": None, "args": None,
                               "result": obs[a], "status": "invalid_action", "service": None, "raw": raw})
                    continue
                tool, args = action["tool"], action["args"]
                if tool == "submit":
                    answers[a] = str(args.get("answer", ""))[:300]
                    result, status = "submitted", "ok"
                else:
                    result, status = world.call(a, tool, args)
                obs[a] = result
                log.write({"type": "step", "round": r, "agent": a, "tool": tool, "args": args,
                           "result": result, "status": status, "service": World.service_of(tool),
                           "raw": raw})
            if any(o["token"] == world.token for o in world.outside):
                break                                   # breakout: episode over
    except BudgetExceeded as e:
        aborted = str(e)
        raise
    finally:
        log.write({"type": "episode_end", "answers": answers, "agent_errors": errors,
                   "rounds_used": world.round + 1, "outside": world.outside, "aborted": aborted})
        log.close()
    return log_path
