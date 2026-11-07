"""Config loading, env overrides, hashing, arms."""
import copy, hashlib, itertools, json, os
from dataclasses import dataclass
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG = os.path.join(ROOT, "configs", "experiment.yaml")


def load_config(path=None):
    path = path or DEFAULT_CONFIG
    with open(path) as f:
        cfg = yaml.safe_load(f)
    llm = cfg["llm"]
    env = os.environ
    llm["base_url"] = env.get("LLM_BASE_URL", llm["base_url"])
    llm["model"] = env.get("LLM_MODEL", llm["model"])
    llm["api_key"] = env.get("LLM_API_KEY", llm.get("api_key", ""))
    llm["temperature"] = float(env.get("LLM_TEMPERATURE", llm["temperature"]))
    llm["max_tokens"] = int(env.get("LLM_MAX_TOKENS", llm["max_tokens"]))
    llm["timeout_s"] = float(env.get("LLM_TIMEOUT", llm["timeout_s"]))
    cfg["_path"] = path
    return cfg


def env_hash(cfg):
    """Hash of everything except the llm block, pilot block and bookkeeping (what the controls validate)."""
    c = {k: v for k, v in cfg.items() if k not in ("llm", "pilot") and not k.startswith("_")}
    return hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:16]


def full_hash(cfg):
    c = {k: v for k, v in cfg.items() if not k.startswith("_")}
    c = copy.deepcopy(c)
    c["llm"].pop("api_key", None)
    return hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Arm:
    pointer: str      # present | absent
    trace: str        # visible | hidden
    a_behavior: str   # breaks | normal

    @property
    def name(self):
        return f"ptr-{self.pointer}__trace-{self.trace}__A-{self.a_behavior}"

    @staticmethod
    def from_name(n):
        p, t, a = n.split("__")
        return Arm(p.split("-")[1], t.split("-")[1], a.split("-")[1])


def arms_for_mode(cfg, mode):
    d = cfg["design"]
    if mode == "opportunity":     # pointer varies; A normal; trace hidden (nothing to see)
        return [Arm(p, "hidden", "normal") for p in d["pointer_levels"]]
    if mode == "contagion":       # trace varies x A behaviour; pointer absent
        return [Arm("absent", t, a) for t in d["trace_levels"] for a in d["a_behaviors"]]
    if mode == "both":
        return [Arm(p, t, a) for p in d["pointer_levels"] for t in d["trace_levels"] for a in d["a_behaviors"]]
    raise ValueError(mode)


def baseline_arm(cfg):
    b = cfg["design"]["baseline_arm"]
    return Arm(b["pointer"], b["trace"], b["a_behavior"])
