"""Bring a scenario up and down, and run commands inside it."""
import json
import pathlib
import subprocess
import time

from .scenarios import AGENTS, Config, compose

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPOSE_DIR = ROOT / "results" / "compose"


def _run(command, timeout=120, check=True):
    done = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    if check and done.returncode != 0:
        raise RuntimeError(f"{' '.join(command[:6])} failed: {done.stderr.strip()[-400:]}")
    return done


class Lab:
    """One running copy of a scenario. Use as a context manager."""

    def __init__(self, name: str, cfg: Config):
        self.project = f"sbd_{name.lower()}"
        self.cfg = cfg
        COMPOSE_DIR.mkdir(parents=True, exist_ok=True)
        self.file = COMPOSE_DIR / f"{self.project}.json"
        self.file.write_text(json.dumps(compose(cfg), indent=2))

    def _compose(self, *args, timeout=180):
        return _run(["docker", "compose", "-p", self.project, "-f", str(self.file), *args], timeout)

    def __enter__(self):
        self.down()
        self._compose("up", "-d", "--no-build", "--quiet-pull")
        self._wait()
        self.check_isolation()
        return self

    def __exit__(self, *_):
        self.down()

    def down(self):
        _run(["docker", "compose", "-p", self.project, "-f", str(self.file),
              "down", "-v", "--remove-orphans", "--timeout", "1"], 120, check=False)

    def container(self, service):
        return f"{self.project}-{service}-1"

    def agent_ips(self):
        """Map each agent's Docker network address to its service name."""
        addresses = {}
        for agent in AGENTS:
            raw = _run(["docker", "inspect", self.container(agent)]).stdout
            detail = json.loads(raw)[0]
            for network in detail.get("NetworkSettings", {}).get("Networks", {}).values():
                address = network.get("IPAddress")
                if address:
                    addresses[address] = agent
        return addresses

    def exec(self, service, *command, timeout=30, user="1000:1000"):
        """Run a command in a service. Timeouts come back as exit code 124."""
        try:
            done = subprocess.run(["docker", "exec", "-u", user, self.container(service), *command],
                                  capture_output=True, text=True, timeout=timeout)
            return done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired:
            return 124, "", "timeout"

    def popen(self, service, *command):
        return subprocess.Popen(["docker", "exec", "-u", "1000:1000", self.container(service), *command],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def cache_services(self):
        if self.cfg.topology == "shared":
            return ["cache"]
        return [f"cache_{agent[-1]}" for agent in AGENTS]

    def _wait(self):
        check = "import socket; socket.create_connection(('127.0.0.1', {}), 1)"
        for service, port in [(name, 8080) for name in self.cache_services()] + [("decoy", 8000)]:
            for _ in range(60):
                if self.exec(service, "python3", "-c", check.format(port))[0] == 0:
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(f"{service} did not start")

    def check_isolation(self):
        """Refuse to run unless the agent networks have no way out.

        Compose marks every network internal. This confirms it on the running
        lab, so a mistake in generation stops the run rather than the data.
        """
        inspected = _run(["docker", "network", "ls", "--filter", f"label=com.docker.compose.project={self.project}",
                          "--format", "{{.Name}}"]).stdout.split()
        for network in inspected:
            detail = json.loads(_run(["docker", "network", "inspect", network]).stdout)[0]
            if not detail.get("Internal"):
                raise RuntimeError(f"{network} is not internal: refusing to run")

    def reset_agent(self, service):
        """Destroy and recreate one agent, as a fresh task would."""
        self._compose("up", "-d", "--no-build", "--no-deps", "--force-recreate", service)
        for _ in range(40):
            if self.exec(service, "sh", "-c", "exit 0")[0] == 0:
                return
            time.sleep(0.1)
        raise RuntimeError(f"{service} did not return after reset")

    def outside_log(self):
        """Read the decoy's record out of band, through Docker."""
        code, out, _ = self.exec("decoy", "cat", "/data/hits.jsonl", timeout=10)
        if code != 0:
            return []
        return [json.loads(line) for line in out.splitlines() if line.strip()]

    def access_logs(self):
        entries = []
        for service in self.cache_services():
            code, out, _ = self.exec(service, "cat", "/data/access.jsonl", timeout=10)
            if code == 0:
                for line in out.splitlines():
                    entry = json.loads(line)
                    entry["cache"] = service
                    entries.append(entry)
        return entries
