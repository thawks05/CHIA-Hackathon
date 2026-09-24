import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from verification.generate import generate_cases, split_seed, write_dataset
from verification.run import check_frozen_contract, compare_observations, run_verification
from verification.workloads import generate_hdc, generate_htm, write_workloads


def observations(cases):
    for case in cases:
        result = case["expected"]
        yield f'PIVOT {case["case_id"]} {result["result_vector"]:x} {result["result_distance"]:x} {result["result_aux"]:x}\n'
    yield f'PIVOT_DONE {len(cases)}\n'


class VerificationTests(unittest.TestCase):
    def test_frozen_contract_detects_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "verification").mkdir()
            (root / "reference.py").write_text("original", encoding="utf-8")
            digest = hashlib.sha256(b"original").hexdigest()
            (root / "verification" / "frozen_contract.json").write_text(
                json.dumps({"sha256": {"reference.py": digest}}), encoding="utf-8")
            self.assertTrue(check_frozen_contract(root)["verified"])
            (root / "reference.py").write_text("changed", encoding="utf-8")
            report = check_frozen_contract(root)
            self.assertFalse(report["verified"])
            self.assertEqual(report["changed"], ["reference.py"])

    def test_generation_determinism_split_and_coverage(self):
        a = list(generate_cases(8, 3, 2, 17, 42))
        self.assertEqual(a, list(generate_cases(8, 3, 2, 17, 42)))
        b = list(generate_cases(8, 3, 2, 17, 42, "heldout"))
        self.assertEqual(sum(case["kind"] == "random" for case in a), 17)
        self.assertEqual({case["opcode"] for case in a}, set(range(8)))
        self.assertEqual([case for case in a if case["kind"] == "directed"],
                         [case for case in b if case["kind"] == "directed"])
        self.assertNotEqual(a[-1]["vectors_in"], b[-1]["vectors_in"])
        self.assertNotEqual(split_seed(42, "development"), split_seed(42, "heldout"))
        self.assertNotIn("expected", next(generate_cases(8, 3, 2, 17, 42, include_expected=False)))

    def test_manifest_provenance_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = write_dataset(directory, width=8, n=3, k=2, count=8, seed=42)
            data = (Path(directory) / "cases.jsonl").read_bytes()
            self.assertEqual(manifest["data_sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(manifest["total_cases"], len(data.splitlines()))
            self.assertEqual(json.loads(data.splitlines()[0])["operand_a"], "0x0")
            self.assertIn("golden_sha256", manifest)
            with self.assertRaises(FileExistsError):
                write_dataset(directory, width=8, n=3, k=2, count=8)

    def test_synthetic_workload_data_and_split(self):
        hdc = list(generate_hdc(2, 42, width=16))
        self.assertEqual(hdc, list(generate_hdc(2, 42, width=16)))
        self.assertNotEqual(hdc, list(generate_hdc(2, 42, "heldout", 16)))
        self.assertEqual(len(hdc[0]["reference"]["nearest_indices"]), 3)
        htm = list(generate_htm(2, 42, width=16))
        self.assertEqual(len(htm[0]["reference"]["permanence_after"]), 16)
        with tempfile.TemporaryDirectory() as directory:
            manifest = write_workloads(directory, seed=42, samples=2, width=16)
            self.assertEqual(manifest["total_samples"], 4)
            self.assertFalse(manifest["fine_tuning_performed"])
            self.assertFalse(manifest["rtl_validation_performed"])
            for name, artifact in manifest["artifacts"].items():
                self.assertEqual(artifact["sha256"], hashlib.sha256((Path(directory) / name).read_bytes()).hexdigest())

    def test_comparator_success(self):
        cases = list(generate_cases(8, 3, 2, 8, 12))
        report = compare_observations(observations(cases), iter(cases))
        self.assertTrue(report["passed"])
        self.assertEqual(report["checked_cases"], len(cases))

    def test_comparator_detects_wrong_unknown_missing_extra_and_reordered(self):
        cases = list(generate_cases(8, 3, 2, 0, 12))[:2]
        variants = (
            ["PIVOT 0 ff 0 0\n", *list(observations(cases))[1:]],
            ["PIVOT 0 xx 0 0\n", *list(observations(cases))[1:]],
            list(observations(cases))[:1],
            [*list(observations(cases))[:-1], "PIVOT 2 0 0 0\n", "PIVOT_DONE 3\n"],
            [list(observations(cases))[1], list(observations(cases))[0], "PIVOT_DONE 2\n"],
            [*list(observations(cases)), "PIVOT_DONE 2\n"],
        )
        for lines in variants:
            self.assertFalse(compare_observations(lines, cases)["passed"], lines)

    def test_no_tool_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory, patch("verification.run.shutil.which", return_value=None):
            report = run_verification(rtl_dir=Path(__file__).resolve().parents[1] / "rtl",
                                      output_dir=directory, count=0)
            self.assertEqual(report["status"], "not_run")
            self.assertFalse(report["passed"])
            self.assertEqual(report["checked_cases"], 0)
            self.assertGreater(report["case_count"], 0)
            self.assertTrue((Path(directory) / "report.json").exists())
            self.assertFalse((Path(directory) / "stimuli.hex").exists())


if __name__ == "__main__":
    unittest.main()
