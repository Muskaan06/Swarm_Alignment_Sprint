import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sbd import shared_pilot
from sbd.shared_pilot import CONDITIONS
from sbd.scenarios import AGENTS, compose


class SharedResourcePilot(unittest.TestCase):
    def test_conditions_change_only_cache_topology(self):
        shared = CONDITIONS["shared_cache"][1]
        private = CONDITIONS["private_caches"][1]
        self.assertEqual(shared.cache_mode, private.cache_mode)
        self.assertEqual(shared.outside, private.outside)
        self.assertEqual(shared.per_agent_creds, private.per_agent_creds)
        self.assertNotEqual(shared.topology, private.topology)

    def test_pilot_conditions_keep_fake_outside_fetch_enabled(self):
        for _, config in CONDITIONS.values():
            self.assertTrue(config.outside)
            self.assertEqual(config.cache_mode, "full")
            document = compose(config)
            self.assertTrue(all(network["internal"] for network in document["networks"].values()))
            for agent in AGENTS:
                self.assertNotIn("net_outside", document["services"][agent]["networks"])
                self.assertEqual(set(document["services"][agent]["environment"]), {"CACHE_CREDENTIAL"})

    def test_only_shared_cache_is_shared_between_agents(self):
        shared = compose(CONDITIONS["shared_cache"][1])["services"]
        private = compose(CONDITIONS["private_caches"][1])["services"]
        self.assertIn("cache", shared)
        self.assertEqual(sum(name.startswith("cache_") for name in private), len(AGENTS))
        for agent in AGENTS:
            self.assertIn(f"net_{agent}", shared["cache"]["networks"])
            self.assertIn(f"net_{agent}", private[f"cache_{agent[-1]}"]["networks"])

    def test_analysis_reports_paired_seed_outcomes(self):
        rows = [
            {"model": "qwen3:8b", "scenario": "shared_cache", "seed": 0, "egress_delivered": True},
            {"model": "qwen3:8b", "scenario": "private_caches", "seed": 0, "egress_delivered": False},
        ]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "results" / "pilot"
            output.mkdir(parents=True)
            for index, row in enumerate(rows):
                (output / f"{index}.result.json").write_text(json.dumps(row))
            captured = io.StringIO()
            with patch.object(shared_pilot, "ROOT", Path(temporary)), contextlib.redirect_stdout(captured):
                shared_pilot.analyze("pilot")

        self.assertIn("| qwen3:8b | private_caches | 1 | 0 | 0.000 |", captured.getvalue())
        self.assertIn("shared-only=1, private-only=0, both=0, neither=0", captured.getvalue())

    def test_positive_control_uses_shared_cache_with_fake_outside_enabled(self):
        result = CONDITIONS["shared_cache"][1]
        self.assertEqual(result.topology, "shared")
        self.assertTrue(result.outside)
        self.assertEqual(result.cache_mode, "full")


if __name__ == "__main__":
    unittest.main()
