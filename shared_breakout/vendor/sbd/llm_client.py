"""OpenAI-compatible chat client (stdlib only).

Works with OpenRouter, other hosted providers and local servers such as Ollama
or LM Studio. The API key stays in this process, on the host: it is never passed
into any container.
"""
import json
import os
import time
import urllib.error
import urllib.request

PROVIDERS = {
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "ollama": ("http://localhost:11434/v1", None),
    "lmstudio": ("http://localhost:1234/v1", None),
}


class BudgetExceeded(RuntimeError):
    pass


class LLMClient:
    def __init__(self, provider="openrouter", base_url=None, api_key=None, budget_usd=5.0,
                 input_price=None, output_price=None, timeout=120):
        default_url, key_name = PROVIDERS.get(provider, (None, None))
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or default_url or "").rstrip("/")
        if not self.base_url:
            raise ValueError(f"unknown provider {provider!r}: pass --base-url")
        self.api_key = api_key or os.environ.get("LLM_API_KEY") or (os.environ.get(key_name) if key_name else None)
        if key_name and not self.api_key:
            raise ValueError(f"set {key_name} or LLM_API_KEY")
        self.provider = provider
        self.budget_usd = budget_usd
        self.input_price = input_price    # USD per million tokens, fallback only
        self.output_price = output_price
        self.timeout = timeout
        self.spent_usd = 0.0
        self.cost_reported = True
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _cost(self, usage):
        if usage.get("cost") is not None:
            return float(usage["cost"])
        self.cost_reported = False
        if self.input_price is None or self.output_price is None:
            return 0.0
        return (usage.get("prompt_tokens", 0) * self.input_price
                + usage.get("completion_tokens", 0) * self.output_price) / 1e6

    def chat(self, model, messages, tools, temperature=0.2, max_tokens=1200, seed=None):
        if self.spent_usd >= self.budget_usd:
            raise BudgetExceeded(f"spent ${self.spent_usd:.4f} of ${self.budget_usd:.2f}")
        payload = {"model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
                   "temperature": temperature, "max_tokens": max_tokens}
        if seed is not None:
            payload["seed"] = seed
        if self.provider == "openrouter":
            payload["usage"] = {"include": True}
        data = self._post(payload)
        usage = data.get("usage") or {}
        self.prompt_tokens += usage.get("prompt_tokens", 0)
        self.completion_tokens += usage.get("completion_tokens", 0)
        self.spent_usd += self._cost(usage)
        if not data.get("choices"):
            raise RuntimeError(f"no choices in response: {json.dumps(data)[:300]}")
        return data["choices"][0]["message"]

    def _post(self, payload):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        body = json.dumps(payload).encode()
        last_error = None
        for attempt in range(6):
            request = urllib.request.Request(f"{self.base_url}/chat/completions", body, headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read())
            except urllib.error.HTTPError as error:
                last_error = f"HTTP {error.code}: {error.read()[:300]!r}"
                if error.code not in (408, 429, 500, 502, 503, 504):
                    raise RuntimeError(last_error) from error
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                last_error = str(error)
            time.sleep(min(30, 2 ** attempt))
        raise RuntimeError(f"gave up after retries: {last_error}")
