"""Frozen candidate evaluator. All reported measurements carry their origin."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pivot.memory import ROOT, MemoryConfig, frozen_workload, simulate
from pivot.program import compile_program


def digest_json(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def source_digest(directories=("pivot", "model", "verification", "rtl", "configs")) -> str:
    digest = hashlib.sha256()
    for directory in directories:
        for path in sorted((ROOT / directory).rglob("*")):
            if path.is_file() and path.suffix in (".py", ".v", ".sv", ".json") and "__pycache__" not in path.parts:
                digest.update(path.relative_to(ROOT).as_posix().encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def validate_candidate(candidate: dict) -> dict:
    if not isinstance(candidate, dict):
        raise ValueError("candidate must be a JSON object")
    if len(json.dumps(candidate)) > 65536:
        raise ValueError("candidate exceeds 64 KiB")
    allowed = {"id", "arm", "params", "program", "rationale", "parent_id", "metadata"}
    if set(candidate) - allowed:
        raise ValueError("unknown candidate fields: " + ",".join(sorted(set(candidate) - allowed)))
    identity = candidate.get("id", digest_json(candidate)[:16])
    if not isinstance(identity, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identity):
        raise ValueError("candidate id must be 1..80 alphanumeric, dash or underscore characters")
    arm = candidate.get("arm", "manual")
    if arm not in ("baseline", "grid", "random", "agent", "manual"):
        raise ValueError("invalid search arm")
    params = candidate.get("params", {})
    if not isinstance(params, dict) or set(params) - {"lane_width", "prefetch_depth", "layout", "issue_policy"}:
        raise ValueError("unknown design parameters")
    if not isinstance(candidate.get("rationale", ""), str):
        raise ValueError("rationale must be text")
    compile_program(candidate.get("program"))
    return {**candidate, "id": identity, "arm": arm, "params": params}


def evaluate_candidate(candidate: dict, output_dir: str, seed: int = 20260923,
                       random_cases: int = 1000, rtl_backend: str = "none") -> dict:
    if rtl_backend not in ("none", "model", "auto", "iverilog", "verilator"):
        raise ValueError("unsupported RTL backend")
    if type(random_cases) is not int or random_cases < 0:
        raise ValueError("random_cases must be nonnegative")
    started = time.monotonic()
    # Hash invalid proposals too so every attempted evaluation consumes budget.
    result = {"candidate_id": str(candidate.get("id", "invalid")) if isinstance(candidate, dict) else "invalid",
              "status": "rejected", "metrics": None,
              "gate": {"status": "not_run", "mode": rtl_backend},
              "tier2": {"status": "not_run", "reason": "synthesis requires configured tools and PDK"},
              "tier3": {"status": "not_run", "reason": "Chipyard full-system evaluation is optional and not implemented"},
              "provenance": {"schema_version": 1, "seed": seed,
                             "created_utc": datetime.now(timezone.utc).isoformat(),
                             "python_version": sys.version.split()[0], "evaluator_sha256": source_digest(),
                             "candidate_sha256": digest_json(candidate), "measurement_kind": "model_estimate"}}
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    try:
        validated = validate_candidate(candidate)
        result["candidate_id"] = validated["id"]
        workload = frozen_workload(seed)
        result["provenance"]["workload_sha256"] = workload["sha256"]
        result["provenance"]["memory_config_sha256"] = hashlib.sha256((ROOT / "configs/memory_hbm2.json").read_bytes()).hexdigest()
        # Address validation precedes scoring. The expression interpreter cannot
        # alter workload length, operation count, expected outputs or score code.
        metrics = simulate(validated, workload, MemoryConfig.load())
        result["gate"] = {"status": "rtl_not_run", "mode": "model_only",
                          "address_mapping": "bijective_on_complete_benchmark_domain",
                          "workload_bursts": metrics["bursts"],
                          "candidate_rtl": "not_modified_by_this_expression_search"}
        if rtl_backend not in ("none", "model"):
            rtl_output = output / "verification"
            command = [sys.executable, "-m", "verification.run", "--output", str(rtl_output.resolve()),
                       "--count", str(random_cases), "--seed", str(seed), "--backend", rtl_backend,
                       "--width", "512", "--n", "8", "--k", "3"]
            proc = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=600)
            (output / "verification.log").write_text(proc.stdout + proc.stderr, encoding="utf-8")
            report_path = rtl_output / "report.json"
            report = json.loads(report_path.read_text()) if report_path.exists() else {"status": "failed", "returncode": proc.returncode}
            result["gate"]["rtl_report"] = report
            if proc.returncode != 0 or report.get("status") not in ("passed", "pass"):
                result["status"] = "rtl_gate_failed" if report.get("status") != "not_run" else "not_run"
                result["gate"]["status"] = report.get("status", "failed")
                return _finish(result, output, started)
            result["gate"]["status"] = "passed"
            result["gate"]["mode"] = rtl_backend
        result["status"] = "model_evaluated" if rtl_backend in ("none", "model") else "rtl_verified_model_evaluated"
        result["metrics"] = metrics
    except (ValueError, TypeError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["gate"]["status"] = "rejected"
    return _finish(result, output, started)


def _finish(result, output, started):
    result["duration_seconds"] = time.monotonic() - started
    (output / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    return result
