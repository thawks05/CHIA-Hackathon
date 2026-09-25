"""Orchestration tests, with no cloud, LLM calls, CHIA installation, or EDA tools."""
from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from pivot.search import control_candidates
from workflows.agent import parse_proposal
from workflows.chia_driver import _save_envelope, controls, main
from workflows import stages


class WorkflowTests(unittest.TestCase):
    def test_controls_share_core_order_and_do_not_repeat(self):
        for arm in ("grid", "random"):
            actual = controls(arm, 144, 11)
            self.assertEqual(actual, list(control_candidates(arm, 144, 11)))
            self.assertEqual(len({json.dumps(c["params"], sort_keys=True) for c in actual}), 144)
        with self.assertRaises(ValueError):
            controls("grid", 145, 11)

    def test_proposal_is_json_only_and_checks_parameter_bounds(self):
        valid = {"params": {"lane_width": 128, "prefetch_depth": 8,
                            "layout": "vector_major", "issue_policy": "fifo"}}
        self.assertEqual(parse_proposal(json.dumps(valid), 1)["arm"], "agent")
        valid["params"]["lane_width"] = True
        with self.assertRaises(ValueError):
            parse_proposal(json.dumps(valid), 1)
        with self.assertRaises(ValueError):
            parse_proposal('{"command":"touch evaluator.py"}', 1)
        with self.assertRaises(ValueError):
            parse_proposal("import os", 1)

    def test_artifact_path_traversal_is_rejected(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("../outside.txt", "escape")
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                _save_envelope({"artifact_zip": payload.getvalue()}, Path(temporary) / "artifacts")
            self.assertFalse((Path(temporary) / "outside.txt").exists())

    def test_unavailable_simulator_is_not_a_pass(self):
        with patch("workflows.stages.shutil.which", return_value=None):
            envelope = stages.tier0({"rtl_backend": "iverilog", "seed": 1})
        self.assertEqual(envelope["result"]["status"], "not_run")

    def test_requested_unavailable_rtl_fails_closed_and_preserves_evidence(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            with patch("workflows.stages.shutil.which", return_value=None), \
                 patch("pivot.evaluate.evaluate_candidate") as evaluate:
                code = main(["--backend", "model", "--rtl-backend", "iverilog",
                             "--budget", "1", "--output", temporary])
            self.assertEqual(code, 1)
            evaluate.assert_not_called()
            report = json.loads((Path(temporary) / "workflow.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["tiers"]["tier0"]["status"], "not_run")
            self.assertEqual(report["tiers"]["tier2"]["status"], "not_run")
            self.assertTrue((Path(temporary) / "tier0" / "stage.json").is_file())
            self.assertTrue((Path(temporary) / "workflow.sqlite").is_file())

    def test_failed_tier0_blocks_core_evaluation(self):
        with patch("pivot.evaluate.evaluate_candidate") as evaluate:
            result = stages.tier1({"seed": 1}, {"id": "test"}, {"status": "failed"})
            remote_result = stages.tier1({"seed": 1}, {"id": "test"}, {"result": {"status": "failed"}, "artifact_zip": b""})
        self.assertEqual(result["result"]["status"], "blocked")
        self.assertEqual(remote_result["result"]["status"], "blocked")
        evaluate.assert_not_called()

    def test_real_model_pipeline_journals_matched_rounds(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            code = main(["--backend", "model", "--budget", "2", "--random-cases", "0",
                         "--output", temporary, "--timeout-seconds", "60"])
            self.assertEqual(code, 0)
            report = json.loads((Path(temporary) / "workflow.json").read_text())
            self.assertEqual(report["arms"]["grid"]["attempts"], 2)
            self.assertEqual(report["arms"]["random"]["scored"], 2)
            self.assertEqual(report["arms"]["agent"]["status"], "not_run")
            self.assertEqual(report["baseline"]["result"]["status"], "model_evaluated")
            connection = sqlite3.connect(Path(temporary) / "workflow.sqlite")
            try:
                arms = [row[0] for row in connection.execute("SELECT arm FROM attempts WHERE stage='tier1' ORDER BY sequence")]
            finally:
                connection.close()
            self.assertEqual(arms, ["baseline", "grid", "random", "grid", "random"])
            self.assertIsNone(report["baseline"]["result"]["metrics"]["physical_area_um2"])

    def test_model_only_does_not_bypass_tier2_gate(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            with patch("workflows.stages.tier2") as synthesis:
                code = main(["--backend", "model", "--budget", "1", "--output", temporary,
                             "--synthesis-backend", "yosys", "--timeout-seconds", "60"])
            self.assertEqual(code, 0)
            synthesis.assert_not_called()
            report = json.loads((Path(temporary) / "workflow.json").read_text())
            self.assertEqual(report["tiers"]["tier2"]["status"], "not_run")

    def test_tier1_failed_gate_overrides_successful_tier0(self):
        def fake_tier1(config, candidate, smoke):
            failed = candidate["arm"] == "grid"
            return {"result": {"candidate_id": candidate["id"],
                               "status": "rtl_gate_failed" if failed else "rtl_verified_model_evaluated",
                               "gate": {"status": "failed" if failed else "passed"},
                               "metrics": None}, "artifact_zip": b""}
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            with patch("workflows.stages.tier0", return_value={"result": {"status": "passed"}, "artifact_zip": b""}), \
                 patch("workflows.stages.tier1", side_effect=fake_tier1):
                code = main(["--backend", "model", "--rtl-backend", "iverilog",
                             "--budget", "1", "--output", temporary])
            self.assertEqual(code, 1)
            report = json.loads((Path(temporary) / "workflow.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["arms"]["grid"]["status"], "failed")
            self.assertEqual(report["tier1_failures"][0]["status"], "rtl_gate_failed")

    def test_baseline_evaluation_error_is_not_a_completed_run(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            with patch("workflows.stages.tier1", side_effect=RuntimeError("worker unavailable")):
                code = main(["--backend", "model", "--budget", "1", "--output", temporary])
            self.assertEqual(code, 1)
            report = json.loads((Path(temporary) / "workflow.json").read_text())
            self.assertEqual(report["status"], "failed")
            self.assertTrue(any(item["arm"] == "baseline" for item in report["tier1_failures"]))

    def test_identical_supplied_candidates_keep_separate_artifacts(self):
        candidate = {"id": "repeat", "arm": "manual", "params": {
            "lane_width": 128, "prefetch_depth": 8, "layout": "vector_major", "issue_policy": "fifo"}}
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            supplied = root / "input.json"
            supplied.write_text(json.dumps([candidate, candidate]), encoding="utf-8")
            output = root / "run"
            code = main(["--backend", "model", "--budget", "2", "--candidates", str(supplied),
                         "--output", str(output)])
            self.assertEqual(code, 0)
            copies = []
            for result_path in (output / "candidates").glob("*/result.json"):
                if json.loads(result_path.read_text())["candidate_id"] == "repeat":
                    copies.append(result_path.parent.name)
            self.assertEqual(len(copies), 2)
            self.assertNotEqual(copies[0], copies[1])
            self.assertEqual(copies[0].split("-", 1)[1], copies[1].split("-", 1)[1])

    def test_invalid_proposal_rejection_is_not_infrastructure_failure(self):
        candidate = {"id": "invalid", "arm": "manual", "params": {"lane_width": -1}}
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            root = Path(temporary)
            supplied = root / "input.json"
            supplied.write_text(json.dumps([candidate]), encoding="utf-8")
            output = root / "run"
            code = main(["--backend", "model", "--budget", "1", "--candidates", str(supplied),
                         "--output", str(output)])
            self.assertEqual(code, 0)
            report = json.loads((output / "workflow.json").read_text())
            self.assertEqual(report["tier1_failures"], [])
            self.assertEqual(report["arms"]["manual"]["scored"], 0)


if __name__ == "__main__":
    unittest.main()
