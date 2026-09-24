"""Standalone Icarus differential gate for saturating permanence RTL.

Expected answers remain in Python. Protect the evaluator and its output directory
from candidate agents; this local subprocess runner is not a security sandbox.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import time

from model.pivot_golden import pack_fields, permanence_update
from verification.run import check_frozen_contract

ROOT = Path(__file__).resolve().parents[1]


def generate_cases(lanes=8, counter_width=8, count=1000, seed=0):
    if lanes < 1 or counter_width < 1 or count < 0:
        raise ValueError("lanes/width must be positive; count must be nonnegative")
    maximum = (1 << counter_width) - 1
    all_active = (1 << lanes) - 1
    alternating = sum(1 << index for index in range(0, lanes, 2))
    rng = random.Random(int.from_bytes(hashlib.sha256(f"pivot-permanence-v1:{seed}".encode()).digest(), "big"))
    directed = []
    for value in sorted({0, 1, maximum // 2, maximum - 1, maximum}):
        for active in sorted({0, all_active, alternating}):
            for amount in sorted({0, 1, maximum}):
                directed.append(([value] * lanes, active, amount, amount))
    directed.append(([index % (maximum + 1) for index in range(lanes)], alternating, maximum, 0))
    directed.append(([maximum - index % (maximum + 1) for index in range(lanes)], alternating, 0, maximum))
    for index in range(len(directed) + count):
        if index < len(directed):
            values, active, increment, decrement = directed[index]
        else:
            values = [rng.getrandbits(counter_width) for _ in range(lanes)]
            active = rng.getrandbits(lanes)
            increment, decrement = rng.getrandbits(counter_width), rng.getrandbits(counter_width)
        yield dict(case_id=index, kind="directed" if index < len(directed) else "random",
                   permanence_in=pack_fields(values, counter_width), active=active,
                   increment=increment, decrement=decrement,
                   expected=pack_fields(permanence_update(values, active, increment, decrement, counter_width), counter_width))


def compare_observations(lines, cases):
    expected = iter(cases)
    checked = errors = 0
    done = None
    for line in lines:
        if line.startswith("PERMANENCE_DONE "):
            if done is not None:
                errors += 1
            try:
                done = int(line.split()[1])
            except (ValueError, IndexError):
                errors += 1
        elif line.startswith("PERMANENCE "):
            if done is not None:
                errors += 1
            case = next(expected, None)
            if case is None:
                errors += 1
                continue
            checked += 1
            try:
                fields = line.split()
                if len(fields) != 3 or int(fields[1]) != case["case_id"] or int(fields[2], 16) != case["expected"]:
                    errors += 1
            except ValueError:
                errors += 1
    missing = sum(1 for _ in expected)
    errors += int(missing > 0) + int(done != checked)
    return dict(passed=errors == 0, checked_cases=checked, missing_cases=missing, mismatch_count=errors)


def run_verification(*, rtl=ROOT / "rtl" / "pivot_permanence.v", output_dir,
                     lanes=8, counter_width=8, count=1000, seed=0, backend="iverilog", timeout=300):
    if backend != "iverilog":
        raise ValueError("Standalone permanence currently supports Icarus only")
    directed = sum(1 for _ in generate_cases(lanes, counter_width, 0, seed))
    rtl, output = Path(rtl).resolve(), Path(output_dir).resolve()
    if not rtl.is_file():
        raise FileNotFoundError(rtl)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("report.json", "stimuli.hex", "simulation.log")):
        raise FileExistsError("Use a fresh output directory")
    tb = ROOT / "tb" / "permanence_tb.v"
    trusted = {"model/pivot_golden.py": ROOT / "model" / "pivot_golden.py",
               "verification/permanence.py": Path(__file__).resolve(), "tb/permanence_tb.v": tb}
    digests = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in trusted.items()}
    report = dict(status="not_run", passed=False, primitive="pivot_permanence", lanes=lanes,
                  counter_width=counter_width, random_cases=count, directed_cases=directed,
                  case_count=count + directed, checked_cases=0, mismatch_count=0,
                  rtl_sha256=hashlib.sha256(rtl.read_bytes()).hexdigest(), evaluator_sha256=digests,
                  visibility="evaluator_only", measured_metric="functional simulation only")
    started = time.perf_counter()

    def finish(status, reason=None):
        report.update(status=status, passed=status == "passed", elapsed_seconds=time.perf_counter() - started)
        if reason:
            report["reason"] = reason
        if any(hashlib.sha256(path.read_bytes()).hexdigest() != digests[name] for name, path in trusted.items()):
            report.update(status="failed", passed=False, reason="Trusted evaluator changed during execution")
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report

    manifest_path = ROOT / "verification" / "permanence_contract.json"
    if not manifest_path.exists():
        return finish("failed", "Missing permanence evaluator integrity manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not check_frozen_contract()["verified"] or manifest["sha256"] != digests:
        return finish("failed", "Frozen evaluator integrity check failed")
    iverilog, vvp = shutil.which("iverilog"), shutil.which("vvp")
    if not iverilog or not vvp:
        return finish("not_run", "Icarus Verilog and vvp are required")
    try:
        version = subprocess.run([iverilog, "-V"], capture_output=True, text=True, timeout=10, check=False)
        report["simulator_version"] = (version.stdout or version.stderr).splitlines()[0]
        with (output / "stimuli.hex").open("w", encoding="ascii") as stream:
            for case in generate_cases(lanes, counter_width, count, seed):
                stream.write(" ".join(f'{case[key]:x}' for key in ("permanence_in", "active", "increment", "decrement")) + "\n")
        binary = output / "permanence.vvp"
        command = [iverilog, "-g2012", "-s", "permanence_tb", f"-Ppermanence_tb.LANES={lanes}",
                   f"-Ppermanence_tb.COUNTER_WIDTH={counter_width}", "-o", str(binary), str(tb), str(rtl)]
        report["compile_command"] = command
        with (output / "compile.log").open("w", encoding="utf-8") as log:
            compiled = subprocess.run(command, cwd=output, stdout=log, stderr=subprocess.STDOUT, timeout=timeout, check=False)
        if compiled.returncode:
            return finish("failed", "HDL compile failed; see compile.log")
        with (output / "simulation.log").open("w", encoding="utf-8") as log:
            simulated = subprocess.run([vvp, str(binary), "+INPUT=stimuli.hex"], cwd=output, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=timeout, check=False)
        if simulated.returncode:
            return finish("failed", "HDL simulation failed; see simulation.log")
        with (output / "simulation.log").open(encoding="utf-8", errors="replace") as stream:
            report.update(compare_observations(stream, generate_cases(lanes, counter_width, count, seed)))
        return finish("passed" if report["passed"] else "failed")
    except (OSError, subprocess.TimeoutExpired, IndexError) as error:
        return finish("failed", str(error))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtl", type=Path, default=ROOT / "rtl" / "pivot_permanence.v")
    parser.add_argument("--output", type=Path, required=True, dest="output_dir")
    parser.add_argument("--lanes", type=int, default=8)
    parser.add_argument("--counter-width", type=int, default=8)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--backend", choices=("iverilog",), default="iverilog")
    parser.add_argument("--timeout", type=float, default=300)
    report = run_verification(**vars(parser.parse_args(argv)))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else (2 if report["status"] == "not_run" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
