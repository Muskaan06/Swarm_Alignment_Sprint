# Quick start (Ollama, no API key)

Everything is already set up for Ollama in `config/experiment.yaml`
(server `http://localhost:11434/v1`, model `qwen2.5:7b`, 4 agents, key in 2 parts,
15 rounds, 10 seeds, 4 conditions: isolated, all_shared, isolated+flaw, all_shared+flaw).

```bash
# 1. install
pip install -r requirements.txt
python -m pytest -q tests                      # should print: 95 passed

# 2. free check, no model needed (takes seconds)
python run_experiment.py --agent stub_pooler --seeds 5 --out pooler
python run_experiment.py --agent stub_exploit --seeds 5 --out exploit
python analyze.py pooler                        # -> results/pooler/

# 3. get the model (Ollama must be running)
ollama pull qwen2.5:7b

# 4. small pilot: 4 episodes
python run_experiment.py --agent llm --seeds 1 --out pilot
python analyze.py pilot                         # check results/pilot/summary.md

# 5. full run: 40 episodes (resume = run the same command again)
python run_experiment.py --agent llm --out main
python analyze.py main
```

**Pilot check** (`results/pilot/summary.md`):
- invalid actions per episode should be low (under ~3): the model follows the format
- open one file in `logs/pilot/` and check the agents read `key_part.txt` and call tools
- one seed is too few to judge breakout rates; just check nothing is broken
If agents mostly produce invalid actions, use a bigger model: `ollama pull qwen2.5:14b`,
then change `models:` in the config.

**Optional extras** (in the config):
- compare models: list more than one under `models:` (pull each first)
- which resource carries the key: add `notes_only`, `kv_only`, `queue_only`, `folder_only`
  under `conditions:`
- exact replication of shared_breakout: `key_parts: 4`

**Send back:** commit and push `results/`, and zip the `logs/` folder separately
(logs are not in git).
