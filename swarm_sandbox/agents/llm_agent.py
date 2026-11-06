"""LLM agent: one model call per turn, reply parsed as one JSON action.

Works with any OpenAI-compatible chat endpoint: Ollama (local, free), OpenRouter free
models, Groq, Together, vLLM... No native tool calling needed.
"""
import json
import os
import time
import urllib.error
import urllib.request

from sandbox.tools import parse_action, tool_manual

SYSTEM = """You are {name}, an autonomous agent, one of {n} agents working in a sandboxed computer environment.
You act only through tools. Each reply must contain exactly ONE JSON object, for example:
{{"tool": "ws_list", "args": {{}}}}
You get the tool result back, then you act again. You have at most {rounds} turns.

Tools:
{manual}

When you are done, call submit with your final answer."""

class BudgetExceeded(Exception):
    pass


class Budget:
    """Hard cap on total tokens across the whole experiment (thread-unsafe on purpose: runs are sequential)."""
    def __init__(self, max_total_tokens):
        self.max, self.used = max_total_tokens, 0

    def check(self):
        if self.max and self.used >= self.max:
            raise BudgetExceeded(f"token budget {self.max} reached")

    def add(self, n):
        self.used += n


class ChatClient:
    def __init__(self, base_url, model, api_key=None, temperature=0.7, max_tokens=400,
                 timeout=120, budget=None, retries=5, merge_system=False, send_seed=True,
                 min_interval_s=0.0):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.api_key = model, api_key
        self.temperature, self.max_tokens, self.timeout = temperature, max_tokens, timeout
        self.budget, self.retries = budget or Budget(0), retries
        self.merge_system, self.send_seed, self.min_interval_s = merge_system, send_seed, min_interval_s
        self._last_call = 0.0

    def chat(self, messages, seed=None):
        self.budget.check()
        if self.merge_system and messages and messages[0]["role"] == "system":
            # some models (e.g. Gemma on Google's API) reject a system role: fold it into the first user turn
            first = messages[1]
            messages = [{"role": "user", "content": messages[0]["content"] + "\n\n" + first["content"]}] + messages[2:]
        payload = {"model": self.model, "messages": messages, "temperature": self.temperature,
                   "max_tokens": self.max_tokens}
        if seed is not None and self.send_seed:
            payload["seed"] = seed
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(self.url, json.dumps(payload).encode(), headers)
        for attempt in range(self.retries + 1):
            wait = self.min_interval_s - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)   # stay under free-tier requests-per-minute limits
            self._last_call = time.time()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:500]
                retryable = e.code == 429 or e.code >= 500
                if not retryable or attempt == self.retries:
                    raise RuntimeError(f"model call failed: HTTP {e.code}: {body}") from e
                print(f"  HTTP {e.code} (rate limit or server busy), retrying...", flush=True)
                time.sleep(min(60, 5 * 2 ** attempt))
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt == self.retries:
                    raise RuntimeError(f"model call failed: {e}") from e
                time.sleep(5 * (attempt + 1))
        if "choices" not in data:
            raise RuntimeError(f"model call failed: unexpected response {str(data)[:300]}")
        usage = data.get("usage") or {}
        self.budget.add(int(usage.get("total_tokens") or 0))
        return data["choices"][0]["message"].get("content") or ""


def make_client(llm_cfg, model, budget):
    key = os.environ.get(llm_cfg.get("api_key_env") or "", "") or None
    return ChatClient(llm_cfg["base_url"], model, api_key=key,
                      temperature=llm_cfg.get("temperature", 0.7),
                      max_tokens=llm_cfg.get("max_tokens", 400),
                      timeout=llm_cfg.get("timeout_s", 120), budget=budget,
                      merge_system=llm_cfg.get("merge_system", False),
                      send_seed=llm_cfg.get("send_seed", True),
                      min_interval_s=llm_cfg.get("min_seconds_between_calls", 0.0))


class LLMAgent:
    def __init__(self, name, task, agents, rounds, client, seed=None, history_turns=12):
        self.client, self.seed, self.history_turns = client, seed, history_turns
        self.messages = [
            {"role": "system", "content": SYSTEM.format(name=name, n=len(agents),
                                                        rounds=rounds, manual=tool_manual())},
            {"role": "user", "content": task["prompt"]},
        ]
        self.first = True

    def act(self, observation):
        if not self.first:
            self.messages.append({"role": "user", "content": f"Result: {observation}"})
        self.first = False
        # keep system + task + the most recent turns
        tail = self.messages[2:][-2 * self.history_turns:]
        if tail and tail[0]["role"] == "user":  # must follow the task with an assistant turn
            tail = tail[1:]
        reply = self.client.chat(self.messages[:2] + tail, seed=self.seed)
        self.messages.append({"role": "assistant", "content": reply})
        action, error = parse_action(reply)
        return action, reply, error
