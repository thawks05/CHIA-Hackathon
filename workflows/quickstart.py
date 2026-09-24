"""Bounded Linux/local launch that always writes a manifest and result archive."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import tarfile
import time


ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("model", "chia"), default="model")
    parser.add_argument("--output", required=True)
    parser.add_argument("--budget", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=int, default=1800)
    parser.add_argument("--random-cases", type=int, default=1000)
    parser.add_argument("--rtl-backend", choices=("auto", "none", "iverilog"), default="auto")
    args = parser.parse_args(argv)
    if not 1 <= args.budget <= 144 or args.timeout_seconds < 1 or args.random_cases < 0:
        parser.error("Budget must be 1..144; time positive; random cases nonnegative")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        parser.error("Choose an empty output directory")
    manifest = {
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version, "platform": platform.platform(),
        "backend": args.backend, "no_cloud_provisioning": True,
        "no_training": True, "agent": "not_run: no paid LLM requested by quickstart",
        "tools": {name: shutil.which(name) for name in ("iverilog", "vvp", "verilator", "yosys", "hammer-vlsi", "chia")},
    }
    rtl = args.rtl_backend
    if rtl == "auto":
        # In CHIA mode simulators run on the worker, not necessarily the head.
        rtl = "iverilog" if args.backend == "chia" or (shutil.which("iverilog") and shutil.which("vvp")) else "none"
    command = [sys.executable, "-m", "workflows.chia_driver",
               "--backend", args.backend, "--output", str(output / "run"),
               "--budget", str(args.budget), "--rtl-backend", rtl,
               "--random-cases", str(args.random_cases),
               "--timeout-seconds", str(max(1, args.timeout_seconds - 15))]
    # A synthesis run requires its own explicit command/config after quickstart.
    manifest["command"] = command
    started = time.monotonic()
    exit_code = 1
    try:
        with (output / "run.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=(os.name != "nt"),
            )
            print(f"Running {args.backend}; log: {output / 'run.log'}", flush=True)
            try:
                exit_code = process.wait(timeout=args.timeout_seconds)
                manifest["status"] = "completed" if exit_code == 0 else "failed"
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.wait()
                exit_code = 124
                manifest["status"] = "time_budget_exhausted"
                manifest["note"] = "Partial SQLite rows/logs preserved; unfinished checks are not passes."
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        manifest["exit_code"] = exit_code
        manifest["elapsed_seconds"] = time.monotonic() - started
        manifest["finished_utc"] = datetime.now(timezone.utc).isoformat()
        (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        archive = output.parent / f"{output.name}.tar.gz"
        with tarfile.open(archive, "w:gz") as bundle:
            for path in sorted(output.rglob("*")):
                if path.is_file() and not path.is_symlink():
                    bundle.add(path, arcname=str(Path(output.name) / path.relative_to(output)), recursive=False)
        print(f"Manifest: {output / 'manifest.json'}\nArchive: {archive}", flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
