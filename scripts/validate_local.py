"""Run project Python verification and preserve its actual output."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/local-validation")
    args = parser.parse_args()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("Choose an empty output directory")
    results = []
    for name, arguments in (("unit", ["discover", "-s", "tests", "-v"]),
                            ("workflow", ["workflows.test_workflow", "-v"]),
                            ("chisel_adapter", ["hardware.test_verify_chisel", "-v"])):
        command = [sys.executable, "-m", "unittest", *arguments]
        completed = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, timeout=180)
        (output / f"{name}.log").write_text(completed.stdout, encoding="utf-8")
        count = re.search(r"Ran (\d+) tests? in", completed.stdout)
        results.append({"suite": name, "command": command, "exit_code": completed.returncode,
                        "tests_run": int(count.group(1)) if count else None})
    report = {"created_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
              "status": "passed" if all(r["exit_code"] == 0 for r in results) else "failed",
              "suites": results, "hdl_simulation": "not_run", "distributed_chia": "not_run"}
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
