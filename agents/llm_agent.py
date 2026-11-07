"""LLM agent: OpenAI-compatible chat endpoint (Ollama by default), one JSON action per model call."""
import json, re, time
import requests
from Swarm_Alignment_Sprint.harness.executor import TOOLS



def build_system_prompt(world, name, cfg):
    d, t = cfg["design"], cfg["task"]
    return cfg["prompt"]["system"].format(name=name, host=cfg["servers"]["host"], port=world.allowed_port,
                         token=world.tokens[name], pressure=t["pressure_text"][t["pressure"]],
                         budget=d["rounds"] * d["actions_per_round"], apr=d["actions_per_round"],
                         rounds=d["rounds"], tools="\n".join("- " + v for v in TOOLS.values()))


def parse_action(text):
    """Extract the first JSON object with a 'tool' key. Tolerates <think>, code fences and a missing quote."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"```(?:json)?", "", text)
    text = re.sub(r'"args\s*:\s*\{', '"args": {', text)
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(text[i:])
            except Exception:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("tool"), str):
                a = obj.get("args", {})
                return obj["tool"], (a if isinstance(a, dict) else {})
    return None


class LLMAgent:
    def __init__(self, name, world, cfg, seed, idx):
        self.name, self.cfg, self.world = name, cfg, world
        self.llm = cfg["llm"]
        self.seed = seed * 10 + idx
        self.apr = cfg["design"]["actions_per_round"]
        self.budget = cfg["design"]["rounds"] * self.apr
        self.used = 0
        self.messages = [{"role": "system", "content": build_system_prompt(world, name, cfg)}]
        self.transcript = []     # raw model replies + parse status
        self.tokens = 0
        self.errors = 0

    def _call(self):
        L = self.llm
        body = {"model": L["model"], "messages": self.messages, "temperature": L["temperature"],
                "max_tokens": L["max_tokens"], **(L.get("extra_body") or {})}
        if L.get("send_seed", True):
            body["seed"] = self.seed
        hdr = {"Authorization": f"Bearer {L['api_key'] or 'none'}"}
        r = requests.post(L["base_url"].rstrip("/") + "/chat/completions", json=body, headers=hdr,
                          timeout=L["timeout_s"])
        r.raise_for_status()
        j = r.json()
        self.tokens += (j.get("usage") or {}).get("total_tokens", 0)
        return j["choices"][0]["message"]["content"] or ""

    def take_turn(self, h, rnd):
        if self.name in h.executor.finished:
            return
        for _ in range(self.apr):
            if self.used >= self.budget or self.name in h.executor.finished:
                return
            left = self.budget - self.used
            self.messages.append({"role": "user", "content":
                                  f"Round {rnd}. Actions left: {left}. Reply with one JSON action."
                                  if not self.transcript else
                                  f"(Round {rnd}, actions left: {left}.) Next action as one JSON object."})
            try:
                reply = self._call()
            except Exception as e:
                self.errors += 1
                self.transcript.append({"round": rnd, "error": str(e)})
                self.messages.pop()
                time.sleep(1)
                return
            self.messages.append({"role": "assistant", "content": reply})
            act = parse_action(reply)
            self.used += 1
            if act is None:
                self.transcript.append({"round": rnd, "raw": reply, "parsed": None})
                self.messages.append({"role": "user", "content":
                                      'error: reply with exactly one JSON object, e.g. {"tool": "ls", "args": {"path": "."}}'})
                h.executor.events.append({"run": h.executor.run_id, "seed": h.executor.seed,
                                          "condition": h.executor.condition, "agent": self.name, "round": rnd,
                                          "action": "invalid", "args": {}, "breakout_type": None, "ok": False,
                                          "info": ["invalid_action"], "result": reply[:200]})
                continue
            tool, args = act
            res = h.do(tool, **args)
            self.transcript.append({"round": rnd, "raw": reply, "parsed": {"tool": tool, "args": args}})
            self.messages.append({"role": "user", "content": f"Result of {tool}:\n{res}"})
