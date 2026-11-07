# Colab / notebook cells. Each "# %%" block is a cell. Upload this folder (or git clone it) first.

# %% 1. install deps and Ollama
!pip -q install -r requirements.txt
!curl -fsSL https://ollama.com/install.sh | sh

# %% 2. start Ollama in the background and pull the model (default qwen3:8b; use qwen3:4b on a small GPU)
import subprocess, time, os
subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); time.sleep(5)
os.environ["LLM_MODEL"] = "qwen3:8b"
!ollama pull $LLM_MODEL

# %% 3. controls 1-6 (no LLM). Must print "N/N checks passed"
!python -m controls.run_controls

# %% 4. smoke run (control 7): 1 seed per arm, then READ results/smoke/transcripts/*.txt and smoke_report.md
!python run_batch.py --mode both --smoke

# %% 5. pilot: baseline arm, 5 seeds. Target baseline rate 10-40%; adjust task.pressure / task.dataset_rows only
!python run_batch.py --pilot

# %% 6. freeze settings once the pilot is in band, then a small batch
!python run_batch.py --freeze
!python run_batch.py --mode both --seeds 5 --out results/small

# %% 7. analysis
!python analyze.py results/small
