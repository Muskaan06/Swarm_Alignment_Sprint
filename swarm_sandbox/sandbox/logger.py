"""JSONL episode log. Written only by the harness, never by agents.

Line types: episode_start (config + ground-truth secrets), step (one tool call),
episode_end (answers, outside log). detect.py works from these files alone.
"""
import json
from pathlib import Path


class EpisodeLogger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self.path.open("w", encoding="utf-8")

    def write(self, record):
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self):
        self._fh.close()


def read_log(path):
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]
