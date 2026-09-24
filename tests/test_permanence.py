from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verification.permanence import compare_observations, generate_cases, run_verification


class PermanenceTests(unittest.TestCase):
    def test_independent_arithmetic_reference_widths_and_lanes(self):
        for lanes, width in ((1, 1), (3, 3), (8, 8), (16, 32)):
            mask = (1 << width) - 1
            for case in generate_cases(lanes, width, 100, 724):
                expected = 0
                for lane in range(lanes):
                    value = (case["permanence_in"] >> (lane * width)) & mask
                    change = case["increment"] if (case["active"] >> lane) & 1 else -case["decrement"]
                    expected |= max(0, min(mask, value + change)) << (lane * width)
                self.assertEqual(case["expected"], expected)

    def test_stream_comparator_and_corruption(self):
        cases = list(generate_cases(3, 4, 20, 88))
        lines = [f'PERMANENCE {case["case_id"]} {case["expected"]:x}\n' for case in cases]
        lines.append(f"PERMANENCE_DONE {len(cases)}\n")
        self.assertTrue(compare_observations(lines, cases)["passed"])
        self.assertFalse(compare_observations(lines[:-1], cases)["passed"])
        self.assertFalse(compare_observations(["PERMANENCE 0 xxxx\n", *lines[1:]], cases)["passed"])
        self.assertFalse(compare_observations(["PERMANENCE 0 ffff\n", *lines[1:]], cases)["passed"])
        self.assertFalse(compare_observations([*lines, lines[0]], cases)["passed"])

    def test_reproducibility_and_validation(self):
        self.assertEqual(list(generate_cases(2, 4, 10, 42)), list(generate_cases(2, 4, 10, 42)))
        self.assertNotEqual(list(generate_cases(2, 4, 10, 42))[-1], list(generate_cases(2, 4, 10, 43))[-1])
        with self.assertRaises(ValueError):
            list(generate_cases(0, 8))

    def test_no_tool_is_not_run(self):
        with tempfile.TemporaryDirectory() as directory, patch("verification.permanence.shutil.which", return_value=None):
            report = run_verification(output_dir=directory, count=0)
            self.assertEqual(report["status"], "not_run")
            self.assertFalse(report["passed"])
            self.assertEqual(report["checked_cases"], 0)
            self.assertTrue((Path(directory) / "report.json").exists())


if __name__ == "__main__":
    unittest.main()
