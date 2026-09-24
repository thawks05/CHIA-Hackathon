"""Trusted tier implementations shared by local and CHIA execution.

Candidate proposals are data. This module never executes candidate Python,
shell commands, or generated RTL. RTL checks concern the repository baseline.
"""
from __future__ import annotations

import io
from pathlib import Path
import shutil
import socket
import tempfile
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def _envelope(result: dict, directory: Path, started: float) -> dict:
    """Return bounded artifacts to the head, including from ephemeral workers."""
    stream = io.BytesIO()
    omitted = []
    total = 0
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            size = path.stat().st_size
            relative = path.relative_to(directory).as_posix()
            if size > 8 * 1024 * 1024 or total + size > 32 * 1024 * 1024:
                omitted.append({"path": relative, "bytes": size})
                continue
            archive.write(path, relative)
            total += size
    return {
        "result": result,
        "artifact_zip": stream.getvalue(),
        "artifact_omissions": omitted,
        "hostname": socket.gethostname(),
        "duration_seconds": time.monotonic() - started,
        "worker_paths_ephemeral": True,
    }


def tier0(config: dict) -> dict:
    """Elaborate and run directed golden checks on the frozen baseline RTL."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pivot-t0-") as temporary:
        directory = Path(temporary)
        if config.get("rtl_backend", "none") == "none":
            result = {"status": "not_run", "reason": "RTL backend disabled", "scope": "baseline_rtl"}
        elif not shutil.which("iverilog") or not shutil.which("vvp"):
            result = {"status": "not_run", "reason": "iverilog and vvp must be installed", "scope": "baseline_rtl"}
        else:
            from verification.run import run_verification

            result = run_verification(
                rtl_dir=str(ROOT / "rtl"), output_dir=str(directory),
                width=512, n=8, k=3, count=0,
                seed=config["seed"], split="heldout", simulator="iverilog",
                timeout=config.get("stage_timeout", 300),
            )
            result["scope"] = "baseline_rtl"
        return _envelope(result, directory, started)


def tier1(config: dict, candidate: dict, smoke: dict) -> dict:
    """Golden/model evaluation; candidate DSL validation belongs to the core."""
    started = time.monotonic()
    smoke = smoke.get("result", smoke)
    with tempfile.TemporaryDirectory(prefix="pivot-t1-") as temporary:
        directory = Path(temporary)
        if smoke.get("status") in {"failed", "error"}:
            result = {
                "candidate_id": candidate.get("id"), "status": "blocked",
                "reason": "Tier 0 failed; candidate was not evaluated",
                "tier2": {"status": "not_run"}, "tier3": {"status": "not_run"},
            }
        else:
            from pivot.evaluate import evaluate_candidate

            result = evaluate_candidate(
                candidate=candidate, output_dir=str(directory),
                seed=config["seed"], random_cases=config.get("random_cases", 1000),
                rtl_backend=config.get("rtl_backend", "none"),
            )
        return _envelope(result, directory, started)


def tier2(config: dict) -> dict:
    """Optional baseline synthesis. Model candidates do not modify this RTL."""
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="pivot-t2-") as temporary:
        directory = Path(temporary)
        backend = config.get("synthesis_backend", "none")
        if backend == "none":
            result = {"status": "not_run", "reason": "Synthesis is explicitly opt-in"}
        else:
            from synthesis.run import run_synthesis

            def tool_config(name):
                value = config.get(name)
                if not value:
                    return None
                path = Path(value)
                return str(path if path.is_absolute() else ROOT / path)

            result = run_synthesis(
                backend=backend, output=str(directory), top="pivot_tile",
                rtl=[str(path) for path in sorted((ROOT / "rtl").glob("*.v"))],
                width=512, vectors=8, top_k=3,
                hammer_config=tool_config("hammer_config"),
                hammer_env=tool_config("hammer_env"), corner=config.get("corner"),
                metrics_spec=tool_config("metrics_spec"),
                timeout=config.get("stage_timeout", 300),
            )
        result["scope"] = "baseline_rtl_only"
        result["candidate_specific_ppa"] = False
        result["explanation"] = (
            "The candidate changes the memory model parameters or mapping DSL. "
            "This synthesis checks the shared baseline RTL; its results cannot "
            "be assigned to individual model candidates."
        )
        return _envelope(result, directory, started)
