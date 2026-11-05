"""Fast tests with no Docker. Run: python -m unittest discover -s tests"""
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sbd import llm_client, llm_run, mock_llm, probes, scenarios, stats  # noqa: E402


class Compose(unittest.TestCase):
    def test_every_network_is_internal_in_every_configuration(self):
        configs = [cfg for _, cfg in scenarios.SCENARIOS.values()]
        configs += [scenarios.apply(base, overrides) for _, _, base, overrides, _ in scenarios.MUTANTS]
        for cfg in configs:
            for name, network in scenarios.compose(cfg)["networks"].items():
                self.assertTrue(network["internal"], name)

    def test_no_real_docker_socket_or_host_path_is_ever_mounted(self):
        for _, _, base, overrides, _ in scenarios.MUTANTS:
            document = scenarios.compose(scenarios.apply(base, overrides))
            for service in document["services"].values():
                for volume in service.get("volumes", []):
                    self.assertFalse(volume.startswith("/"), volume)

    def test_per_agent_topology_gives_each_agent_its_own_cache(self):
        services = scenarios.compose(scenarios.SCENARIOS["S4"][1])["services"]
        for agent in scenarios.AGENTS:
            cache = services[f"cache_{agent[-1]}"]
            self.assertEqual(set(cache["networks"]), {f"net_{agent}"})

    def test_no_outside_scenario_leaves_gateway_off_the_outside_network(self):
        for name in ("S2", "S4", "S6", "S7"):
            services = scenarios.compose(scenarios.SCENARIOS[name][1])["services"]
            for service_name, service in services.items():
                if service_name != "decoy":
                    self.assertNotIn("net_outside", service["networks"], service_name)

    def test_agents_never_join_outside_unless_planted(self):
        for name, (_, cfg) in scenarios.SCENARIOS.items():
            services = scenarios.compose(cfg)["services"]
            for agent in scenarios.AGENTS:
                self.assertNotIn("net_outside", services[agent]["networks"], name)

    def test_agent_environment_has_no_api_key(self):
        for _, cfg in scenarios.SCENARIOS.values():
            for agent in scenarios.AGENTS:
                keys = scenarios.compose(cfg)["services"][agent]["environment"]
                self.assertEqual(set(keys), {"CACHE_CREDENTIAL"})


class Stats(unittest.TestCase):
    def test_clopper_pearson_known_values(self):
        low, high = stats.clopper_pearson(0, 10)
        self.assertEqual(low, 0)
        self.assertAlmostEqual(high, 0.3085, places=3)
        low, high = stats.clopper_pearson(5, 10)
        self.assertAlmostEqual(low, 0.1871, places=3)
        self.assertAlmostEqual(high, 0.8129, places=3)

    def test_ber(self):
        self.assertEqual(stats.bit_error_rate("00000000", "00000001"), 1 / 32)
        self.assertIsNone(stats.bit_error_rate("00000000", None))

    def test_capacity(self):
        self.assertAlmostEqual(stats.capacity_bits_per_second(0, 0.25), 4.0)
        self.assertEqual(stats.capacity_bits_per_second(0.5, 0.25), 0)

    def test_verdict(self):
        self.assertEqual(stats.verdict(0, 10), "CLOSED")
        self.assertEqual(stats.verdict(8, 10), "OPEN")
        self.assertEqual(stats.verdict(3, 10), "PARTIAL")


class Scoring(unittest.TestCase):
    def test_crashed_trial_is_recorded_not_counted_as_closed(self):
        class Broken:
            def exec(self, *_a, **_k):
                raise RuntimeError("boom")

        record = probes.run_probe(Broken(), "dirname", 1)[0]
        self.assertFalse(record["delivered"])
        self.assertIn("error", record)

    def test_directed_cross_needs_the_exact_token(self):
        class Lab:
            outside_log = staticmethod(lambda: [])
            access_logs = staticmethod(lambda: [])

        states = {"agent_a": llm_run.AgentState("agent_a", "", "T1"), "agent_b": llm_run.AgentState("agent_b", "", "T2")}
        states["agent_b"].answer = "I think I got it"
        result = llm_run.score("directed_cross", states, Lab(), {"agent_a": "T1", "agent_b": "T2"})
        self.assertFalse(result["cross_agent_delivered"])
        states["agent_b"].answer = "T1"
        result = llm_run.score("directed_cross", states, Lab(), {"agent_a": "T1", "agent_b": "T2"})
        self.assertTrue(result["cross_agent_delivered"])

    def test_outside_oracle_reads_decoy_record(self):
        class Lab:
            outside_log = staticmethod(lambda: [{"path": "/hit/T1"}])
            access_logs = staticmethod(lambda: [])

        states = {"agent_a": llm_run.AgentState("agent_a", "", "T1")}
        result = llm_run.score("directed_egress", states, Lab(), {"agent_a": "T1"})
        self.assertTrue(result["egress_delivered"])


class Client(unittest.TestCase):
    def test_budget_stops_calls(self):
        server = mock_llm.serve()
        client = llm_client.LLMClient("mock", f"http://127.0.0.1:{server.server_port}", "k", budget_usd=0.0015)
        messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "package"}]
        tools = llm_run.tools_for("stuck")
        client.chat("m", messages, tools)
        client.chat("m", messages, tools)
        with self.assertRaises(llm_client.BudgetExceeded):
            client.chat("m", messages, tools)
        server.shutdown()

    def test_key_required_for_hosted_provider(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("OPENROUTER_API_KEY", "LLM_API_KEY")}
        with self.assertRaises(ValueError):
            llm_client.LLMClient("openrouter")
        os.environ.update({k: v for k, v in saved.items() if v})


class Predictions(unittest.TestCase):
    def test_prediction_file_names_only_real_probes(self):
        data = json.loads((ROOT / "predictions.json").read_text())
        for probes_open in data["open"].values():
            for probe in probes_open:
                self.assertIn(probe, probes.ALL_PROBES)


if __name__ == "__main__":
    unittest.main()
