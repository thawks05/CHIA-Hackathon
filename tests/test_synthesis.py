"""Regression checks for provenance and rejection of unsupported PPA claims."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from synthesis.run import extract_report_metrics, run_synthesis, wrapper_text


class SynthesisTests(unittest.TestCase):
    def test_missing_tool_is_not_run_and_has_no_metrics(self):
        with tempfile.TemporaryDirectory() as directory, patch("synthesis.run.shutil.which", return_value=None):
            result = run_synthesis("hammer", directory)
            self.assertEqual(result["status"], "not_run")
            self.assertEqual(result["metrics"], {})
            saved = json.loads((Path(directory) / "result.json").read_text())
            self.assertEqual(saved["status"], "not_run")

    def test_extraction_requires_current_report_and_unambiguous_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "area.rpt").write_text("Total cell area: 12.5\n")
            rule = {"area_um2": {"path": "area.rpt", "regex": r"Total cell area: ([0-9.]+)"}}
            metrics, evidence = extract_report_metrics(root, rule)
            self.assertEqual(metrics, {"area_um2": 12.5})
            self.assertEqual(len(evidence["area_um2"]["sha256"]), 64)
            (root / "area.rpt").write_text("Total cell area: 12.5\nTotal cell area: 99\n")
            with self.assertRaises(ValueError):
                extract_report_metrics(root, rule)
            rule["area_um2"]["path"] = "../other-run.rpt"
            with self.assertRaises(ValueError):
                extract_report_metrics(root, rule)

    def test_invalid_dimensions_rejected_and_tiny_aux_fits(self):
        with self.assertRaises(ValueError):
            wrapper_text(16, 1, 2)
        self.assertIn("[7:0] result_aux", wrapper_text(1, 4, 4))
        self.assertIn("[0:0] result_aux", wrapper_text(1, 1, 1))


if __name__ == "__main__":
    unittest.main()
