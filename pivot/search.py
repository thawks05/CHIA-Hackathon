"""Matched evaluation-count grid/random controls; optional imported agent arm."""
from __future__ import annotations

import argparse
import itertools
import json
import random
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pivot.database import ResultsDB
from pivot.evaluate import evaluate_candidate, source_digest
from pivot.report import write_report


def parameter_space():
    # Alternate lane sizes early, so a truncated grid does not evaluate only one.
    return [{"lane_width": lane, "prefetch_depth": depth, "layout": layout, "issue_policy": issue}
            for depth, layout, issue, lane in itertools.product((1, 2, 4, 8, 16, 32),
                ("vector_major", "bit_interleaved", "xor_swizzle"), ("fifo", "row_hit_first"), (64, 128, 256, 512))]


def control_candidates(arm: str, budget: int, seed: int):
    space = parameter_space()
    if not 1 <= budget <= len(space):
        raise ValueError(f"budget must be 1..{len(space)} distinct evaluations per arm")
    if arm == "random":
        random.Random(seed).shuffle(space)
    elif arm != "grid":
        raise ValueError("control arm must be grid or random")
    for i, params in enumerate(space[:budget]):
        yield {"id": f"{arm}_{i:04d}", "arm": arm, "params": params,
               "rationale": "Predefined finite grid" if arm == "grid" else "Uniform parameter sampling without replacement"}


def run_search(output: str | Path, budget: int = 100, seed: int = 20260923,
               backend: str = "model", random_cases: int = 1000,
               max_seconds: float = 1200, agent_candidates: str | None = None,
               mode: str = "controls") -> dict:
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    arms = ["grid", "random"] if mode == "controls" else [mode]
    candidates = {arm: list(control_candidates(arm, budget, seed)) for arm in arms}
    if agent_candidates:
        imported = json.loads(Path(agent_candidates).read_text())
        if not isinstance(imported, list) or len(imported) > budget:
            raise ValueError("agent candidates must be a JSON list within the same evaluation budget")
        candidates["agent"] = [{**item, "arm": "agent"} for item in imported]
        arms.append("agent")
    baseline = {"id": "baseline", "arm": "baseline", "params": {
        "lane_width": 128, "prefetch_depth": 8, "layout": "vector_major", "issue_policy": "fifo"},
        "rationale": "Frozen starting configuration; source hash recorded before search"}
    metadata = {"run_id": run_id, "seed": seed, "backend": backend, "requested_budget_per_arm": budget,
                "arms": arms, "budget_definition": "attempted evaluations; not equal monetary cost or wall time",
                "evaluator_sha256": source_digest(), "baseline": baseline,
                "agent_status": "imported_candidates_not_an_autonomous_loop" if agent_candidates else "not_run",
                "external_api_calls": 0, "max_seconds": max_seconds}
    (root / "run.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    db = ResultsDB(root / "results.sqlite")
    db.start(run_id, metadata)
    start = time.monotonic()
    ordinal = 0
    backend_arg = "none" if backend == "model" else backend
    try:
        result = evaluate_candidate(baseline, str(root / "evaluations" / run_id / "baseline"), seed, random_cases, backend_arg)
        db.record(run_id, ordinal, baseline, result)
        # Each full round gives one evaluation to every control arm. If a time
        # bound is reached, stop at a round boundary to preserve control counts.
        for index in range(budget):
            if time.monotonic() - start >= max_seconds:
                break
            for arm in arms:
                if index >= len(candidates[arm]):
                    continue
                candidate = candidates[arm][index]
                ordinal += 1
                result = evaluate_candidate(candidate, str(root / "evaluations" / run_id / f"{ordinal:05d}"),
                                            seed, random_cases, backend_arg)
                db.record(run_id, ordinal, candidate, result)
            if (index + 1) % 10 == 0:
                print(f"Completed {index + 1} evaluation rounds ({ordinal} candidate attempts)", flush=True)
        rows = db.rows(run_id)
        summary = write_report(root, metadata, rows)
    finally:
        db.close()
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="artifacts/controls")
    parser.add_argument("--budget", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260923)
    parser.add_argument("--backend", choices=("model", "auto", "iverilog", "verilator"), default="model")
    parser.add_argument("--random-cases", type=int, default=1000)
    parser.add_argument("--max-seconds", type=float, default=1200)
    parser.add_argument("--agent-candidates")
    parser.add_argument("--mode", choices=("controls", "grid", "random"), default="controls")
    args = parser.parse_args(argv)
    if args.max_seconds <= 0:
        parser.error("--max-seconds must be positive")
    print(json.dumps(run_search(**vars(args)), indent=2))


if __name__ == "__main__":
    main()
