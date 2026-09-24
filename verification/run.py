"""Trusted streaming RTL differential runner for Icarus or Verilator.

Only inputs are written to the HDL working directory; expected outputs remain
inside the Python evaluator. This separation prevents accidental answer leaks,
but is not an OS security boundary. Run untrusted/malicious RTL in an isolated
worker/container with only candidate HDL, trusted capture TB and stimuli mounted.
Never grant an optimization agent tools/read access to heldout results or seeds.
"""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from model.pivot_golden import CONTRACT_VERSION, PivotGolden
from verification.generate import INPUT_KEYS, OUTPUT_KEYS, generate_cases

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_frozen_contract(root: Path = PROJECT_ROOT) -> dict:
    """Check reviewed evaluator hashes before running any candidate simulator.

    The manifest is a reviewable trust anchor, not a signature. Protect it and
    evaluator code from candidate writes using permissions/container mounts.
    """
    path = root / "verification" / "frozen_contract.json"
    if not path.exists():
        return dict(verified=False, reason="frozen contract manifest is missing", changed=[])
    manifest = json.loads(path.read_text(encoding="utf-8"))
    changed = [name for name, digest in manifest["sha256"].items()
               if not (root / name).exists() or _sha256(root / name) != digest]
    return dict(verified=not changed, changed=changed, manifest_sha256=_sha256(path))


def compare_observations(lines, cases, max_mismatches: int = 20) -> dict:
    """Compare streamed PIVOT records with streamed expected cases, fail closed.

    Unknown (x/z) output bits, missing/duplicate/reordered/extra rows and absent
    completion marker are errors. Mismatch details are capped to bound memory.
    """
    expected = iter(cases)
    checked = errors = 0
    details = []
    done = None

    def fail(reason, **values):
        nonlocal errors
        errors += 1
        if len(details) < max_mismatches:
            details.append(dict(reason=reason, **values))

    for line in lines:
        if line.startswith("PIVOT_DONE "):
            if done is not None:
                fail("duplicate completion marker")
            try:
                done = int(line.split()[1])
            except (ValueError, IndexError):
                fail("invalid completion marker")
            continue
        if not line.startswith("PIVOT "):
            continue
        if done is not None:
            fail("observation after completion marker")
        case = next(expected, None)
        if case is None:
            fail("extra observation")
            continue
        checked += 1
        fields = line.split()
        try:
            if len(fields) != 5:
                raise ValueError("record must have five fields")
            observed_id = int(fields[1])
            observed = {key: int(value, 16) for key, value in zip(OUTPUT_KEYS, fields[2:])}
        except ValueError:
            fail("malformed or unknown output", case_id=case["case_id"])
            continue
        if observed_id != case["case_id"]:
            fail("out-of-order case", case_id=case["case_id"], observed_id=observed_id)
        if observed != case["expected"]:
            fail("output mismatch", case_id=case["case_id"], opcode=case["opcode"],
                 expected={key: hex(value) for key, value in case["expected"].items()},
                 observed={key: hex(value) for key, value in observed.items()})
    missing = sum(1 for _ in expected)
    if missing:
        fail("missing observations", missing=missing)
    if done != checked:
        fail("missing or incorrect completion marker", marker=done, checked=checked)
    return dict(passed=errors == 0, checked_cases=checked, missing_cases=missing,
                mismatch_count=errors, mismatches=details)


def run_verification(*, rtl_dir, output_dir, width=512, n=8, k=3,
                     count=1000, seed=0, split="heldout", simulator="auto",
                     timeout=300) -> dict:
    """Compile supplied RTL and compare directed + count random operations.

    count=0 supplies directed smoke coverage. status is passed, failed, or
    not_run; not_run is never a passing result. No synthesis or PPA
    metrics are fabricated. Report includes source hashes and tool versions.
    """
    PivotGolden(width, n, k)
    if count < 0 or timeout <= 0:
        raise ValueError("count must be nonnegative and timeout positive")
    if simulator not in ("auto", "iverilog", "verilator"):
        raise ValueError("simulator must be auto, iverilog, or verilator")
    rtl_dir = Path(rtl_dir).resolve()
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if any((output / filename).exists() for filename in ("report.json", "stimuli.hex", "simulation.log")):
        raise FileExistsError("verification outputs already exist; choose a fresh output directory")
    rtl = sorted([*rtl_dir.glob("*.v"), *rtl_dir.glob("*.sv")])
    if not rtl:
        raise FileNotFoundError(f"No Verilog sources found in {rtl_dir}")
    trusted = [PROJECT_ROOT / "model" / "pivot_golden.py",
               PROJECT_ROOT / "verification" / "generate.py", Path(__file__).resolve(),
               PROJECT_ROOT / "tb" / "pivot_tb.v"]
    trusted_hashes = {str(path.relative_to(PROJECT_ROOT)): _sha256(path) for path in trusted}
    frozen_contract = check_frozen_contract()
    directed_count = sum(1 for _ in generate_cases(width, n, k, 0, seed, split, include_expected=False))
    report = dict(status="not_run", passed=False, contract=CONTRACT_VERSION,
                  width=width, n=n, k=k, random_cases=count, split=split,
                  directed_cases=directed_count, total_cases=count + directed_count,
                  case_count=count + directed_count,
                  checked_cases=0, mismatches=[], mismatch_count=0,
                  rtl_sha256={path.name: _sha256(path) for path in rtl},
                  evaluator_sha256=trusted_hashes, measured_metric="functional simulation only",
                  frozen_contract=frozen_contract,
                  visibility="evaluator_only" if split == "heldout" else "development",
                  report_path=str(output / "report.json"))
    started = time.perf_counter()

    def finish(status, reason=None):
        report["status"] = status
        report["passed"] = status == "passed"
        if reason:
            report["reason"] = reason
        report["elapsed_seconds"] = time.perf_counter() - started
        changed = [path for path in trusted if not path.exists() or
                   _sha256(path) != trusted_hashes[str(path.relative_to(PROJECT_ROOT))]]
        if changed:
            report.update(status="failed", passed=False, reason="trusted evaluator files changed")
        (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report

    if not frozen_contract["verified"]:
        return finish("failed", "Frozen evaluator integrity check failed; review changed reference files")

    chosen = simulator
    if chosen == "auto":
        chosen = "iverilog" if shutil.which("iverilog") and shutil.which("vvp") else "verilator"
    executable = shutil.which(chosen)
    if executable is None or (chosen == "iverilog" and not shutil.which("vvp")):
        return finish("not_run", f"Required simulator executable is not installed: {chosen}")
    report["simulator"] = chosen
    try:
        version = subprocess.run([executable, "-V" if chosen == "iverilog" else "--version"],
                                 capture_output=True, text=True, timeout=10, check=False)
        report["simulator_version"] = (version.stdout or version.stderr).splitlines()[0]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        report["simulator_version"] = "unknown"

    directed = total = 0
    input_digest = hashlib.sha256()
    with (output / "stimuli.hex").open("wb") as stream:
        for case in generate_cases(width, n, k, count, seed, split, include_expected=False):
            line = (f'{case["opcode"]} {case["operand_a"]:x} {case["operand_b"]:x} {case["vectors_in"]:x}\n').encode()
            stream.write(line)
            input_digest.update(line)
            total += 1
            directed += case["kind"] == "directed"
    report.update(total_cases=total, directed_cases=directed, input_sha256=input_digest.hexdigest())
    # No expected-output file or seed is passed to the HDL process.
    tb = trusted[-1]
    if chosen == "iverilog":
        binary = output / "pivot_sim.vvp"
        compile_command = [executable, "-g2012", "-s", "pivot_tb",
                           f"-Ppivot_tb.WIDTH={width}", f"-Ppivot_tb.N={n}", f"-Ppivot_tb.K={k}",
                           "-o", str(binary), str(tb), *map(str, rtl)]
        run_command = [shutil.which("vvp"), str(binary), "+INPUT=stimuli.hex"]
    else:
        build = output / "obj_dir"
        binary = build / "pivot_sim"
        compile_command = [executable, "--binary", "--timing", "-Wno-fatal",
                           "--top-module", "pivot_tb", f"-GWIDTH={width}", f"-GN={n}", f"-GK={k}",
                           "--Mdir", str(build), "-o", "pivot_sim", str(tb), *map(str, rtl)]
        run_command = [str(binary), "+INPUT=stimuli.hex"]
    report["compile_command"] = compile_command
    try:
        compile_started = time.perf_counter()
        with (output / "compile.log").open("w", encoding="utf-8") as log:
            compiled = subprocess.run(compile_command, cwd=output, stdout=log,
                                      stderr=subprocess.STDOUT, timeout=timeout, check=False)
        report["compile_seconds"] = time.perf_counter() - compile_started
        if compiled.returncode:
            return finish("failed", f"HDL compilation failed (exit {compiled.returncode}); see compile.log")
        simulation_started = time.perf_counter()
        with (output / "simulation.log").open("w", encoding="utf-8") as log:
            simulated = subprocess.run(run_command, cwd=output, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=timeout, check=False)
        report["simulation_seconds"] = time.perf_counter() - simulation_started
        if simulated.returncode:
            return finish("failed", f"HDL simulation failed (exit {simulated.returncode}); see simulation.log")
        comparison_started = time.perf_counter()
        with (output / "simulation.log").open(encoding="utf-8", errors="replace") as stream:
            comparison = compare_observations(stream, generate_cases(width, n, k, count, seed, split))
        report.update(comparison)
        if split == "heldout":
            # A workflow may surface the report summary to an optimizer. Do not
            # expose heldout answers, observed traces or per-case IDs there.
            report["mismatches"] = [dict(reason=item["reason"]) for item in report["mismatches"]]
        report["comparison_seconds"] = time.perf_counter() - comparison_started
        return finish("passed" if comparison["passed"] else "failed")
    except subprocess.TimeoutExpired:
        return finish("failed", f"Tool exceeded timeout of {timeout} seconds")
    except OSError as error:
        return finish("failed", f"Could not execute simulator: {error}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtl-dir", type=Path, default=PROJECT_ROOT / "rtl")
    parser.add_argument("--output", dest="output_dir", type=Path, required=True)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--split", choices=("development", "heldout"), default="heldout")
    parser.add_argument("--simulator", "--backend", choices=("auto", "iverilog", "verilator"), default="auto")
    parser.add_argument("--timeout", type=float, default=300)
    report = run_verification(**vars(parser.parse_args(argv)))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else (2 if report["status"] == "not_run" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
