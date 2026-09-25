import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hardware.verify_chisel import adapter_wrapper, verify_chisel


class ChiselAdapterTests(unittest.TestCase):
    def test_staging_provenance_and_missing_simulator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "PivotTile.sv"
            # Fixture tests staging only. It is never compiled or called correct.
            source.write_text("module PivotTile; endmodule\n")
            with patch("verification.run.shutil.which", return_value=None):
                report = verify_chisel(rtl=[source], output=root / "output",
                                       width=1, n=1, k=1, count=0)
            self.assertEqual(report["status"], "not_run")
            self.assertFalse(report["passed"])
            self.assertEqual(report["emitted_sources"][0]["sha256"],
                             hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertEqual(report["parameters"], {"width": 1, "n": 1, "k": 1})
            self.assertEqual(json.loads((root / "output" / "adapter-report.json").read_text()), report)
            with self.assertRaises(FileExistsError):
                verify_chisel(rtl=[source], output=root / "output", width=1, n=1, k=1)

    def test_wrapper_guards_mismatch_and_rejects_invalid_dimensions(self):
        emitted = adapter_wrapper(7, 4, 4)
        self.assertIn("WIDTH != 7 || N != 4 || K != 4", emitted)
        self.assertIn("$bits(dut.vectors_in) != 28", emitted)
        with self.assertRaises(ValueError):
            adapter_wrapper(4, 1, 2)


if __name__ == "__main__":
    unittest.main()
