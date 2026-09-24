import json
import tempfile
import unittest
from pathlib import Path

from pivot.evaluate import evaluate_candidate, validate_candidate
from pivot.memory import MemoryConfig, frozen_workload, simulate
from pivot.program import Expression, ProgramError, compile_program
from pivot.report import model_pareto
from pivot.search import control_candidates, parameter_space, run_search


class ProgramTests(unittest.TestCase):
    def test_safe_integer_expressions(self):
        expression = Expression("(linear ^ (linear // channels)) % channels")
        self.assertEqual(expression({"linear": 37, "channels": 16}), 7)
        self.assertEqual(Expression("-hit if hit == 1 else ordinal")({"hit": 1, "ordinal": 9}), -1)

    def test_no_python_escape(self):
        for text in ("__import__('os')", "v.__class__", "[v][0]", "2**100", "open('x')", "[x for x in v]"):
            with self.subTest(text=text), self.assertRaises(ProgramError):
                Expression(text)

    def test_arithmetic_bounded(self):
        for text in ("1 << 100", "1 // 0", "2147483648 * 2147483648 * 4"):
            with self.subTest(text=text), self.assertRaises(ProgramError):
                Expression(text)({})

    def test_complete_address_program(self):
        with self.assertRaises(ProgramError):
            compile_program({"channel": "v"})


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.workload = frozen_workload(42, vectors=32, queries=2)

    def test_reproducible_and_candidate_independent_trace(self):
        self.assertEqual(self.workload, frozen_workload(42, vectors=32, queries=2))
        self.assertNotEqual(self.workload["sha256"], frozen_workload(43, vectors=32, queries=2)["sha256"])

    def test_work_volume_preserved(self):
        volumes = set()
        for layout in ("vector_major", "bit_interleaved", "xor_swizzle"):
            metrics = simulate({"params": {"layout": layout}}, self.workload)
            volumes.add((metrics["operations"], metrics["bursts"], metrics["transferred_bytes"]))
            self.assertLessEqual(metrics["model_bandwidth_utilization"], 1)
            self.assertGreater(metrics["model_cycles"], 0)
            self.assertIsNone(metrics["physical_area_um2"])
        self.assertEqual(len(volumes), 1)

    def test_alias_rejected(self):
        with self.assertRaisesRegex(ProgramError, "alias"):
            simulate({"program": {key: "0" for key in ("channel", "bank", "row", "column")}}, self.workload)

    def test_program_can_reproduce_known_layout(self):
        program = {"channel": "linear % channels", "bank": "(linear // channels) % banks",
                   "row": "linear // (channels*banks*row_bursts)",
                   "column": "(linear // (channels*banks)) % row_bursts"}
        actual = simulate({"program": program}, self.workload)
        expected = simulate({"params": {"layout": "bit_interleaved"}}, self.workload)
        self.assertEqual(actual, expected)

    def test_out_of_bounds_and_unknown_names(self):
        for channel in ("channels", "-1", "ordinal"):
            with self.subTest(channel=channel), self.assertRaises(ProgramError):
                simulate({"program": {"channel": channel, "bank": "0", "row": "linear", "column": "0"}}, self.workload)

    def test_memory_config_validation(self):
        with self.assertRaises(ValueError):
            MemoryConfig(pseudo_channels=3)
        with self.assertRaises(ValueError):
            MemoryConfig(tck_ns=float("nan"))

    def test_banks_share_channel_column_interval(self):
        workload = {"width": 256, "vectors": 2, "groups": [
            {"workload": "timing", "operations": 2, "accesses": [(0, 0), (1, 0)]}]}
        candidate = {"program": {"channel": "0", "bank": "v", "row": "0", "column": "0"}}
        cfg = MemoryConfig(pseudo_channels=1, banks_per_channel=2, t_ccd_cycles=10)
        result = simulate(candidate, workload, cfg)
        self.assertEqual(result["model_memory_cycles"], 12 + 10 + 18 + 2)


class ExperimentTests(unittest.TestCase):
    def test_controls_same_space_and_budget(self):
        grid = list(control_candidates("grid", 144, 5))
        rand = list(control_candidates("random", 144, 5))
        self.assertEqual(len(parameter_space()), 144)
        self.assertEqual({json.dumps(c["params"], sort_keys=True) for c in grid},
                         {json.dumps(c["params"], sort_keys=True) for c in rand})
        self.assertNotEqual([c["params"] for c in grid], [c["params"] for c in rand])

    def test_no_path_injection(self):
        with self.assertRaises(ValueError):
            validate_candidate({"id": "../../out"})
        with self.assertRaises(ValueError):
            validate_candidate({"params": {"workload_size": 0}})

    def test_model_result_never_claims_rtl_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_candidate({"id": "test", "params": {}}, directory)
            self.assertEqual(result["status"], "model_evaluated")
            self.assertEqual(result["gate"]["status"], "rtl_not_run")
            self.assertIsNone(result["metrics"]["throughput_per_area"])
            self.assertTrue(Path(directory, "result.json").exists())

    def test_bad_program_has_no_score(self):
        with tempfile.TemporaryDirectory() as directory:
            result = evaluate_candidate({"program": {k: "0" for k in ("channel", "bank", "row", "column")}}, directory)
            self.assertEqual(result["status"], "rejected")
            self.assertIsNone(result["metrics"])

    def test_matched_run_exports_real_records(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = run_search(directory, budget=2, seed=17)
            self.assertEqual(summary["arms"]["grid"]["attempted"], 2)
            self.assertEqual(summary["arms"]["random"]["attempted"], 2)
            self.assertEqual(summary["agent_status"], "not_run")
            for filename in ("results.sqlite", "results.csv", "summary.json", "control_progress.svg", "REPORT.md"):
                self.assertTrue(Path(directory, filename).exists())


if __name__ == "__main__":
    unittest.main()
