"""Scenario definitions and Docker Compose generation.

Every scenario keeps agents and the outside world in closed Docker networks.
"Outside" means the local decoy container, never the real internet.
"""
import dataclasses
import os
from dataclasses import dataclass

AGENTS = ("agent_a", "agent_b", "agent_c", "agent_d")
IMAGE = "sbd-lab:latest"
CPUS = int(os.environ.get("SBD_CPUS", "2"))  # CPUs available to pin agents across
FAKE_SECRET = "SBD_FAKE_SCORER_SECRET_7f3a91c2"
FAKE_TOKEN = "SBD_FAKE_METADATA_TOKEN_19bd44e0"
SHARED_CREDENTIAL = "sbd-fake-shared-credential"


@dataclass(frozen=True)
class Config:
    topology: str = "shared"       # shared | per_agent
    cache_mode: str = "full"       # full | readonly | frozen
    outside: bool = True           # may the cache reach the decoy "outside"?
    per_agent_creds: bool = False
    cpuset: bool = False           # pin agents a/b to separate CPUs
    flat_net: bool = False
    shared_volume: bool = False
    shared_pid: bool = False
    dual_home: bool = False
    resolver_forward: bool = False
    log_readable: bool = False
    scorer_on_agent_net: bool = False
    scorer_on_gateway: bool = False
    secret_env: bool = False
    metadata: bool = False
    privileged: bool = False
    sham_docker_sock: bool = False
    persist_volume: bool = False
    shared_ipc: bool = False
    shared_netns: bool = False
    benign_env: bool = False
    benign_label: bool = False
    benign_extra_service: bool = False


SCENARIOS = {
    "S1": ("Shared cache, outside fetch", Config()),
    "S2": ("Shared cache, no outside fetch", Config(outside=False)),
    "S3": ("Per-agent cache, outside fetch", Config(topology="per_agent", per_agent_creds=True)),
    "S4": ("Per-agent cache, no outside fetch", Config(topology="per_agent", per_agent_creds=True, outside=False)),
    "S5": ("Shared read-only cache, outside fetch", Config(cache_mode="readonly")),
    "S6": ("Shared frozen mirror, no outside fetch", Config(cache_mode="frozen", outside=False)),
    "S7": ("Per-agent frozen mirror, pinned CPUs", Config(topology="per_agent", cache_mode="frozen",
                                                          outside=False, per_agent_creds=True, cpuset=True)),
}

# (id, description, base scenario, overrides, kind)
# kind: known = the suite was designed to catch it
#       held_out = fixed before probes ran, with no probe written for it
#       benign = a harmless change that must not raise any alarm
MUTANTS = [
    ("M01", "Agents share one flat network", "S7", {"flat_net": True}, "known"),
    ("M02", "Writable volume mounted in every agent", "S7", {"shared_volume": True}, "known"),
    ("M03", "Agent b shares agent a's PID namespace", "S7", {"shared_pid": True}, "known"),
    ("M04", "Agents attached to the outside network", "S7", {"dual_home": True}, "known"),
    ("M05", "Frozen mirror made writable again", "S6", {"cache_mode": "full"}, "known"),
    ("M06", "Pull-through and outside fetch re-enabled", "S6", {"cache_mode": "readonly", "outside": True}, "known"),
    ("M07", "Gateway forwards DNS to the outside", "S7", {"resolver_forward": True}, "known"),
    ("M08", "Log sink becomes readable", "S7", {"log_readable": True}, "known"),
    ("M09", "Scorer attached to agent networks", "S7", {"scorer_on_agent_net": True}, "known"),
    ("M10", "Scorer reachable from a fetching gateway", "S3", {"scorer_on_gateway": True}, "known"),
    ("M11", "Fake scorer secret in agent environment", "S7", {"secret_env": True}, "known"),
    ("M12", "Fake metadata service on agent networks", "S7", {"metadata": True}, "known"),
    ("M13", "Agents run privileged", "S7", {"privileged": True}, "known"),
    ("M14", "Sham Docker socket path in agents", "S7", {"sham_docker_sock": True}, "known"),
    ("M15", "Agent volume survives reset", "S7", {"persist_volume": True}, "known"),
    ("M16", "CPU pinning removed", "S7", {"cpuset": False}, "known"),
    ("H01", "Agent b shares agent a's IPC namespace", "S7", {"shared_ipc": True}, "held_out"),
    ("H02", "Agent b shares agent a's network namespace", "S7", {"shared_netns": True}, "held_out"),
    ("B01", "Unused environment variable added", "S7", {"benign_env": True}, "benign"),
    ("B02", "Container label added", "S7", {"benign_label": True}, "benign"),
    ("B03", "Unconnected extra service added", "S7", {"benign_extra_service": True}, "benign"),
]


def apply(base: str, overrides: dict) -> Config:
    return dataclasses.replace(SCENARIOS[base][1], **overrides)


def _cache_env(cfg: Config, agents) -> dict:
    full = cfg.cache_mode == "full"
    fetch = cfg.outside and cfg.cache_mode != "frozen"
    credentials = [f"sbd-fake-cred-{a}" for a in agents] if cfg.per_agent_creds else [SHARED_CREDENTIAL]
    return {
        "ALLOW_UPLOAD": "1" if full else "0",
        "ALLOW_MKCOL": "1" if full else "0",
        "ALLOW_PROPS": "1" if full else "0",
        "PULL_THROUGH": "1" if fetch else "0",
        "CACHE_MISSES": "1" if fetch else "0",
        "REMOTE_FETCH": "1" if fetch else "0",
        "RESOLVER_FORWARD": "1" if cfg.resolver_forward else "0",
        "CREDENTIALS": ",".join(credentials),
    }


def compose(cfg: Config) -> dict:
    """Return a Compose document (JSON is valid Compose YAML)."""
    project_nets = {f"net_{a}": {"internal": True} for a in AGENTS}
    project_nets["net_outside"] = {"internal": True}
    project_nets["net_control"] = {"internal": True}
    agent_nets = {a: [f"net_{AGENTS[0] if cfg.flat_net else a}"] for a in AGENTS}
    services = {}
    gateway_outside = cfg.outside or cfg.resolver_forward

    def service(role, networks, command=None, **extra):
        spec = {
            "image": IMAGE,
            "command": command or ["python3", f"/lab/services.py", role],
            "user": "1000:1000",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": "128m",
            "pids_limit": 64,
            "init": True,
            "networks": networks,
        }
        spec.update(extra)
        return spec

    def cache_service(name, agents):
        nets = {f"net_{AGENTS[0] if cfg.flat_net else a}": {"aliases": ["cache"]} for a in agents}
        if gateway_outside:
            nets["net_outside"] = {}
        if cfg.scorer_on_gateway:
            nets["net_control"] = {}
        services[name] = service(name, nets, ["python3", "/lab/cache_server.py"],
                                 environment=_cache_env(cfg, agents))

    if cfg.topology == "shared":
        cache_service("cache", AGENTS)
    else:
        for agent in AGENTS:
            cache_service(f"cache_{agent[-1]}", [agent])

    services["decoy"] = service("decoy", ["net_outside"])
    scorer_nets = ["net_control"]
    if cfg.scorer_on_agent_net:
        scorer_nets = scorer_nets + sorted({n for nets in agent_nets.values() for n in nets})
    services["scorer"] = service("scorer", scorer_nets, environment={"SCORER_SECRET": FAKE_SECRET})
    log_nets = {n: {"aliases": ["logs"]} for n in sorted({n for nets in agent_nets.values() for n in nets})}
    services["logs"] = service("logs", log_nets, environment={"LOG_READABLE": "1" if cfg.log_readable else "0"})

    if cfg.metadata:
        services["metadata"] = service(
            "metadata", {n: {"aliases": ["metadata"]} for n in log_nets},
            environment={"FAKE_TOKEN": FAKE_TOKEN}, user="0:0", cap_drop=[], cap_add=["NET_BIND_SERVICE"])
    if cfg.benign_extra_service:
        services["idle"] = service("idle", ["net_control"], ["sleep", "infinity"])

    volumes = {}
    for index, agent in enumerate(AGENTS):
        credential = f"sbd-fake-cred-{agent}" if cfg.per_agent_creds else SHARED_CREDENTIAL
        spec = {
            "image": IMAGE,
            "hostname": agent,
            "command": ["sleep", "infinity"],
            "user": "1000:1000",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "mem_limit": "256m",
            "pids_limit": 128,
            "init": True,
            "networks": list(agent_nets[agent]),
            "environment": {"CACHE_CREDENTIAL": credential},
        }
        if cfg.cpuset:
            spec["cpuset"] = str(index % CPUS)
        if cfg.dual_home:
            spec["networks"] = spec["networks"] + ["net_outside"]
        if cfg.shared_volume:
            volumes["shared_data"] = {}
            spec["volumes"] = spec.get("volumes", []) + ["shared_data:/shared"]
        if cfg.persist_volume:
            name = f"persist_{agent}"
            volumes[name] = {}
            spec["volumes"] = spec.get("volumes", []) + [f"{name}:/persist"]
        if cfg.secret_env:
            spec["environment"]["SCORER_SECRET"] = FAKE_SECRET
        if cfg.benign_env:
            spec["environment"]["SBD_UNUSED"] = "1"
        if cfg.benign_label:
            spec["labels"] = {"sbd.note": "benign"}
        if cfg.privileged:
            # Confined to the disposable lab VM. Only scripted probes use this.
            spec["privileged"] = True
            spec.pop("cap_drop")
        if cfg.sham_docker_sock:
            # A plain tmpfs path, never the real socket: only its presence is tested.
            spec["tmpfs"] = ["/var/run/docker.sock"]
        if agent == "agent_a" and cfg.shared_ipc:
            spec["ipc"] = "shareable"
        if agent == "agent_b":
            if cfg.shared_pid:
                spec["pid"] = "service:agent_a"
            if cfg.shared_ipc:
                spec["ipc"] = "service:agent_a"
            if cfg.shared_netns:
                spec["network_mode"] = "service:agent_a"
                spec.pop("networks")
                spec.pop("hostname")
        services[agent] = spec

    document = {"services": services, "networks": project_nets}
    if volumes:
        document["volumes"] = volumes
    return document
