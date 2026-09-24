"""Compare external gem5 observations to model predictions without inventing them.

Input CSV: workload_sha256,model_cycles,gem5_cycles,gem5_commit,split
Each row must refer to the SAME fixed trace and clock convention in both models.
The validation report is not itself an automatically calibrated model.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from pathlib import Path


def compare(path: str | Path) -> dict:
    source = Path(path)
    with source.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("at least one measured gem5 observation is required")
    errors = []
    commits = set()
    for row in rows:
        if len(row.get("workload_sha256", "")) != 64 or not row.get("gem5_commit"):
            raise ValueError("each row requires trace SHA256 and the measured gem5 commit")
        if row.get("split") != "validation":
            raise ValueError("calibration validation must use held-out validation observations")
        predicted, observed = float(row["model_cycles"]), float(row["gem5_cycles"])
        if not all(math.isfinite(x) and x > 0 for x in (predicted, observed)):
            raise ValueError("cycle observations must be positive finite values")
        errors.append(abs(predicted - observed) / observed)
        commits.add(row["gem5_commit"])
    return {"status": "external_observations_compared", "samples": len(rows),
            "mean_absolute_percentage_error": sum(errors) / len(errors),
            "max_absolute_percentage_error": max(errors), "gem5_commits": sorted(commits),
            "observations_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "note": "No automatic parameter fitting or claim of acceptable calibration; assess errors and clock conventions."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = compare(args.observations)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
