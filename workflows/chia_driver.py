"""Run a tier cascade locally or through CHIA; record every real attempt.

python -m workflows.chia_driver --backend model --budget 8
python -m workflows.chia_driver --backend chia --rtl-backend iverilog
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def controls(arm: str, budget: int, seed: int) -> list[dict]:
    from pivot.search import control_candidates
    return list(control_candidates(arm, budget, seed))


def _save_envelope(envelope: dict, destination: Path) -> dict:
    destination.mkdir(parents=True, exist_ok=True)
    destination = destination.resolve()
    packed = envelope.pop("artifact_zip", b"")
    if packed:
        with zipfile.ZipFile(io.BytesIO(packed)) as archive:
            for entry in archive.infolist():
                target = (destination / entry.filename).resolve()
                if not target.is_relative_to(destination):
                    raise ValueError("Worker returned an artifact outside its directory")
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.read(entry))
    envelope["artifact_directory"] = str(destination)
    envelope["artifact_zip_sha256"] = hashlib.sha256(packed).hexdigest()
    (destination / "stage.json").write_text(json.dumps(envelope, indent=2, default=str) + "\n", encoding="utf-8")
    return envelope


class Journal:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("""CREATE TABLE IF NOT EXISTS attempts (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            created_utc TEXT NOT NULL, arm TEXT NOT NULL,
            candidate_id TEXT, stage TEXT NOT NULL, status TEXT NOT NULL,
            candidate_json TEXT, result_json TEXT NOT NULL)""")

    def add(self, arm: str, stage: str, result: dict, candidate: dict | None = None):
        self.connection.execute(
            "INSERT INTO attempts(created_utc,arm,candidate_id,stage,status,candidate_json,result_json) VALUES(?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), arm,
             candidate.get("id") if candidate else None, stage,
             str(result.get("status", "unknown")),
             json.dumps(candidate) if candidate else None, json.dumps(result, default=str)),
        )
        self.connection.commit()

    def close(self):
        self.connection.close()


def parser() -> argparse.ArgumentParser:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--backend", choices=("model", "chia"), default="model")
    cli.add_argument("--output", default="artifacts/chia-run")
    cli.add_argument("--budget", type=int, default=8, help="Evaluations per arm; agent enabled only by --agent-model")
    cli.add_argument("--seed", type=int, default=20260923)
    cli.add_argument("--random-cases", type=int, default=1000)
    cli.add_argument("--rtl-backend", choices=("none", "iverilog"), default="none")
    cli.add_argument("--synthesis-backend", choices=("none", "yosys", "hammer"), default="none")
    cli.add_argument("--hammer-config")
    cli.add_argument("--hammer-env")
    cli.add_argument("--metrics-spec", help="Verified extraction spec for the actual Hammer reports")
    cli.add_argument("--corner", help="Name of the corner configured by the supplied Hammer config")
    cli.add_argument("--stage-timeout", type=int, default=300)
    cli.add_argument("--parallel-evaluations", type=int, default=2, help="Maximum concurrent CHIA candidate nodes")
    cli.add_argument("--timeout-seconds", type=int, default=1500)
    cli.add_argument("--candidates", type=Path, help="JSON list or {candidates:[...]}; executes supplied designs instead of controls")
    cli.add_argument("--agent-model", help="Explicitly enables billable Vertex proposals through CHIA")
    cli.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    cli.add_argument("--location", default=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"))
    return cli


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.budget < 1 or args.budget > 144 or args.random_cases < 0:
        raise SystemExit("Use a budget from 1 to 144 distinct candidates and nonnegative random case count")
    if args.stage_timeout < 1 or args.timeout_seconds < 1 or args.parallel_evaluations < 1:
        raise SystemExit("Timeouts and parallel-evaluations must be positive")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output / "workflow.sqlite").exists():
        raise SystemExit("Output already has workflow.sqlite; choose a new run directory")
    deadline = time.monotonic() + args.timeout_seconds
    config = vars(args).copy()
    config.pop("candidates", None)
    journal = Journal(output / "workflow.sqlite")
    ray = None
    report = {
        "status": "running", "backend": args.backend, "seed": args.seed,
        "budget_per_arm": args.budget, "evidence": "analytical_memory_model",
        "tiers": {}, "arms": {}, "no_model_training": True,
        "tier3": {"status": "not_run", "reason": "Chipyard/SoC integration is an optional extension"},
        "physical_ppa_comparison": {"status": "not_run", "reason": "No candidate-specific implemented datapath"},
    }
    nodes = None
    smoke_ref = None
    arm_counts = {}
    evaluation_ordinal = 0
    tier1_failures = []

    def counts(arm):
        return arm_counts.setdefault(arm, {"attempts": 0, "scored": 0, "rtl_verified": 0,
                                          "best_model_operations_per_cycle": None})

    def dispatch(stage: str, *values):
        nonlocal smoke_ref
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Workflow time budget exhausted")
        if args.backend == "chia":
            from chia.base.ChiaFunction import get

            ref = getattr(nodes, stage).chia_remote(*values)
            if stage == "tier0":
                smoke_ref = ref
            try:
                return get(ref, timeout=min(remaining, args.stage_timeout + 30))
            except Exception:
                ray.cancel(ref, force=True)
                raise
        from workflows import stages
        return getattr(stages, stage)(*values)

    def record(candidate: dict, envelope=None, error=None) -> dict:
        nonlocal evaluation_ordinal
        evaluation_ordinal += 1
        # Generated IDs are local identifiers, never output paths supplied by an agent.
        identity = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()[:16]
        directory = output / "candidates" / f"{evaluation_ordinal:05d}-{identity}"
        try:
            if error is not None:
                raise error
            envelope["evaluation_ordinal"] = evaluation_ordinal
            envelope = _save_envelope(envelope, directory)
            result = envelope["result"]
        except Exception as exc:
            result = {"candidate_id": candidate.get("id"), "status": "error", "error": f"{type(exc).__name__}: {exc}"}
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "error.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        arm = str(candidate.get("arm", "manual"))
        journal.add(arm, "tier1", result, candidate)
        status = result.get("status")
        gate_status = result.get("gate", {}).get("status")
        # Invalid design proposals legitimately consume budget without making
        # the evaluator broken. Missing/failed requested checks are different.
        infrastructure_failure = status in {"error", "blocked", "rtl_gate_failed", "not_run"}
        baseline_failure = arm == "baseline" and status not in {"model_evaluated", "rtl_verified_model_evaluated"}
        missing_requested_gate = args.rtl_backend != "none" and status != "rejected" and gate_status not in {"passed", "pass"}
        if infrastructure_failure or baseline_failure or missing_requested_gate:
            tier1_failures.append({"ordinal": evaluation_ordinal, "candidate_id": candidate.get("id"),
                                   "arm": arm, "status": status, "gate_status": gate_status})
        summary = counts(arm)
        summary["attempts"] += 1
        metrics = result.get("metrics") or {}
        if metrics:
            summary["scored"] += 1
            score = metrics.get("model_operations_per_cycle")
            if score is not None:
                summary["best_model_operations_per_cycle"] = max(summary["best_model_operations_per_cycle"] or 0, score)
        if result.get("gate", {}).get("status") == "passed":
            summary["rtl_verified"] += 1
        with (output / "events.jsonl").open("a", encoding="utf-8") as events:
            events.write(json.dumps({"ordinal": evaluation_ordinal, "artifact_directory": str(directory),
                                    "candidate": candidate, "result": result}, default=str) + "\n")
        print(json.dumps({"candidate": candidate.get("id"), "status": result.get("status"), "metrics": result.get("metrics", {})}), flush=True)
        return result

    def evaluate_many(candidates: list[dict], smoke: dict) -> list[dict]:
        if args.backend == "model":
            from workflows import stages
            results = []
            # Complete a control round before checking the deadline again.
            for candidate in candidates:
                try:
                    results.append(record(candidate, envelope=stages.tier1(config, candidate, smoke)))
                except Exception as exc:
                    results.append(record(candidate, error=exc))
            return results
        from chia.base.ChiaFunction import get
        results = []
        for offset in range(0, len(candidates), args.parallel_evaluations):
            batch = candidates[offset:offset + args.parallel_evaluations]
            # Keep the T0 ObjectRef dependency in the actual CHIA task graph.
            refs = [nodes.tier1.chia_remote(config, candidate, smoke_ref) for candidate in batch]
            for candidate, ref in zip(batch, refs):
                try:
                    remaining = max(1.0, deadline - time.monotonic())
                    envelope = get(ref, timeout=min(remaining, args.stage_timeout + 30))
                    results.append(record(candidate, envelope=envelope))
                except Exception as exc:
                    ray.cancel(ref, force=True)
                    results.append(record(candidate, error=exc))
        return results

    try:
        if args.backend == "chia":
            import ray as ray_module
            ray = ray_module
            ray.init(
                address=os.environ.get("RAY_ADDRESS", "auto"),
                runtime_env={"working_dir": str(ROOT), "excludes": [
                    ".git/", ".codex/", ".agents/", "artifacts/", "data/", "private/",
                    "hardware/.tools/", "hardware/target/", "hardware/project/target/",
                    "hardware/project/project/", "hardware/generated/", "**/__pycache__/",
                    "*.tar.gz", "*.zip", "*.sqlite*", ".env", ".env.*",
                ]},
            )
            from workflows import chia_nodes
            nodes = chia_nodes
            required = {"pivot_tier0", "pivot_tier1"}
            if args.synthesis_backend != "none":
                required.add("pivot_synth")
            resources = ray.cluster_resources()
            missing = sorted(tag for tag in required if resources.get(tag, 0) < 1)
            if missing:
                raise RuntimeError("Cluster lacks resource tags: " + ", ".join(missing))
        smoke_envelope = _save_envelope(dispatch("tier0", config), output / "tier0")
        smoke = smoke_envelope["result"]
        report["tiers"]["tier0"] = smoke
        journal.add("shared", "tier0", smoke)
        if args.rtl_backend != "none" and smoke.get("status") not in {"passed", "pass"}:
            report["tiers"]["tier2"] = {"status": "not_run", "reason": "Requested T0 RTL gate did not pass"}
            report["arms"] = {arm: {"status": "not_run", "attempts": 0,
                                    "reason": "Requested T0 RTL gate did not pass", "actual_llm": False}
                              for arm in ("grid", "random", "agent")}
            raise RuntimeError(
                f"Requested RTL backend {args.rtl_backend!r} did not pass T0 "
                f"(status={smoke.get('status', 'unknown')}); evaluation was blocked"
            )

        baseline = {"id": "baseline", "arm": "baseline", "params": {
            "lane_width": 128, "prefetch_depth": 8, "layout": "vector_major", "issue_policy": "fifo"},
            "rationale": "Common frozen starting configuration outside each arm's budget"}
        baseline_result = evaluate_many([baseline], smoke)[0]
        report["baseline"] = {"candidate": baseline, "result": baseline_result}

        arms = {}
        if args.candidates:
            supplied = json.loads(args.candidates.read_text(encoding="utf-8"))
            if isinstance(supplied, dict):
                supplied = supplied.get("candidates")
            if not isinstance(supplied, list) or any(not isinstance(c, dict) for c in supplied):
                raise ValueError("--candidates must contain a list of candidate objects")
            for candidate in supplied[:args.budget]:
                arms.setdefault(str(candidate.get("arm", "manual")), []).append(candidate)
        else:
            arms = {arm: controls(arm, args.budget, args.seed) for arm in ("grid", "random")}
        # All arms see the same frozen baseline. Subsequent LLM feedback contains
        # only its own aggregate outcomes, never hidden tests/control trajectories.
        history = [{"params": baseline["params"], "status": baseline_result.get("status"),
                    "metrics": baseline_result.get("metrics", {})}]
        proposer = None
        attempts = successful_responses = 0
        agent_error = None
        if args.agent_model and not args.candidates:
            try:
                from workflows.agent import VertexProposer
                proposer = VertexProposer(args.agent_model, args.project, args.location, min(args.stage_timeout, 120))
            except Exception as exc:
                agent_error = f"{type(exc).__name__}: {exc}"
        for index in range(args.budget):
            if time.monotonic() >= deadline:
                break
            round_candidates = [candidates[index] for candidates in arms.values() if index < len(candidates)]
            if proposer is not None:
                attempts += 1
                try:
                    proposer.llm.timeout_seconds = max(1, min(args.stage_timeout, int(deadline - time.monotonic())))
                    candidate, metadata = proposer.propose(history, index + 1)
                    successful_responses += 1
                    journal.add("agent", "proposal", {"status": "completed", **metadata}, candidate)
                    round_candidates.append(candidate)
                except Exception as exc:
                    agent_error = f"{type(exc).__name__}: {exc}"
                    journal.add("agent", "proposal", {"status": "failed", "error": agent_error})
                    history.append({"status": "proposal_failed", "error": type(exc).__name__})
                    # Service/auth/billing failures do not trigger repeated calls.
                    if not isinstance(exc, (ValueError, json.JSONDecodeError)):
                        proposer = None
            results = evaluate_many(round_candidates, smoke)
            for candidate, result in zip(round_candidates, results):
                if candidate.get("arm") == "agent":
                    history.append({"params": candidate["params"], "program": candidate.get("program"),
                                    "status": result.get("status"), "metrics": result.get("metrics", {})})
        for arm, candidates in arms.items():
            summary = counts(arm)
            report["arms"][arm] = {"status": "completed" if summary["attempts"] == len(candidates) else "time_budget_exhausted",
                                    **summary, "actual_llm": False}
        if args.agent_model and not args.candidates:
            agent_status = ("completed" if attempts == args.budget else "time_budget_exhausted")
            if proposer is None and agent_error:
                agent_status = "failed" if successful_responses else "not_run"
            report["arms"]["agent"] = {"status": agent_status, **counts("agent"),
                "proposal_attempts": attempts, "successful_responses": successful_responses,
                "actual_llm": successful_responses > 0, "model": args.agent_model,
                "last_error": agent_error}
            journal.add("agent", "summary", report["arms"]["agent"])
        elif args.candidates and "agent" in arms:
            report["arms"]["agent"]["status"] = "imported_candidates"
            report["arms"]["agent"]["actual_llm"] = False
            report["arms"]["agent"]["reason"] = "Supplied JSON is not evidence of a live LLM call"
        else:
            report["arms"]["agent"] = {"status": "not_run", "reason": "No --agent-model supplied; no LLM calls made", "actual_llm": False}
            journal.add("agent", "proposal", report["arms"]["agent"])

        if args.synthesis_backend != "none" and smoke.get("status") in {"passed", "pass"} and time.monotonic() < deadline:
            synthesis = _save_envelope(dispatch("tier2", config), output / "tier2")["result"]
        else:
            synthesis = {"status": "not_run", "reason": "Synthesis disabled, T0 not passed, or time budget exhausted", "scope": "baseline_rtl_only"}
        report["tiers"]["tier2"] = synthesis
        journal.add("shared", "tier2", synthesis)
        report["tier1_failures"] = tier1_failures
        for arm in {failure["arm"] for failure in tier1_failures}:
            if arm in report["arms"]:
                report["arms"][arm]["status"] = "failed"
        if smoke.get("status") in {"failed", "error"} or synthesis.get("status") in {"failed", "error"} or tier1_failures:
            report["status"] = "failed"
            report["error"] = "A requested T0/T1 gate or synthesis stage failed; inspect the stage records"
        else:
            report["status"] = "completed" if time.monotonic() < deadline else "time_budget_exhausted"
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        (output / "workflow.json").write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
        journal.close()
        if ray is not None:
            ray.shutdown()
    print(json.dumps(report, indent=2, default=str))
    return 1 if report["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
