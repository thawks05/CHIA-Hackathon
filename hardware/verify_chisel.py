"""Run fixed-parameter Chisel output through the unchanged PIVOT evaluator."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from verification.run import run_verification


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def adapter_wrapper(width: int, n: int, k: int) -> str:
    if width < 1 or n < 1 or not 1 <= k <= n:
        raise ValueError("Require WIDTH>=1, N>=1 and 1<=K<=N")
    cw, dw, iw = n.bit_length(), width.bit_length(), max(1, (n - 1).bit_length())
    auxiliary = max(width * cw, k * iw)
    return f'''// Adapter for an already elaborated, fixed-parameter PivotTile.
module pivot_tile #(parameter WIDTH={width}, N={n}, K={k})(
    input wire clk, rst,
    input wire [2:0] opcode,
    input wire [WIDTH-1:0] operand_a, operand_b,
    input wire [N*WIDTH-1:0] vectors_in,
    output wire [WIDTH-1:0] result_vector,
    output wire [N*{dw}-1:0] result_distance,
    output wire [{auxiliary}-1:0] result_aux
);
    PivotTile dut(
        .clk(clk), .rst(rst), .opcode(opcode), .operand_a(operand_a),
        .operand_b(operand_b), .vectors_in(vectors_in),
        .result_vector(result_vector), .result_distance(result_distance),
        .result_aux(result_aux)
    );
    initial begin
        if (WIDTH != {width} || N != {n} || K != {k})
            $fatal(1, "Chisel adapter parameters differ from its elaboration declaration");
        if ($bits(dut.operand_a) != {width} || $bits(dut.operand_b) != {width} ||
            $bits(dut.vectors_in) != {n*width} || $bits(dut.result_vector) != {width} ||
            $bits(dut.result_distance) != {n*dw} || $bits(dut.result_aux) != {auxiliary})
            $fatal(1, "Chisel port widths differ from the declared elaboration dimensions");
    end
endmodule
'''


def verify_chisel(*, rtl, output, width=512, n=8, k=3, count=1000,
                  seed=0, split="heldout", simulator="auto", timeout=300):
    wrapper = adapter_wrapper(width, n, k)
    sources = [Path(path).resolve() for path in rtl]
    if not sources or any(not path.is_file() for path in sources):
        raise FileNotFoundError("Pass every emitted self-contained .sv/.v source with --rtl")
    if any(path.suffix.lower() not in {".v", ".sv"} for path in sources):
        raise ValueError("--rtl accepts Verilog/SystemVerilog, not CHIRRTL .fir files")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    staged = output / "chisel-rtl"
    # Never overwrite evidence from a previous invocation.
    staged.mkdir(exist_ok=False)
    source_evidence = []
    for index, source in enumerate(sources):
        target = staged / f"emitted_{index:03d}{source.suffix}"
        shutil.copyfile(source, target)
        source_evidence.append({"original_path": str(source), "staged_path": str(target),
                                "sha256": _digest(target)})
    wrapper_path = staged / "pivot_tile_adapter.v"
    wrapper_path.write_text(wrapper, encoding="utf-8")
    provenance = {
        "adapter": "fixed_chisel_tile_v1", "emitted_top": "PivotTile",
        "harness_top": "pivot_tile", "parameters": {"width": width, "n": n, "k": k},
        "parameter_source": "caller declaration matching the Chisel elaboration command",
        "parameter_limit": "Port widths are checked in simulation; K cannot generally be inferred from port widths",
        "emitted_sources": source_evidence, "wrapper_sha256": _digest(wrapper_path),
        "adapter_sha256": _digest(__file__), "status": "not_run", "passed": False,
        "simulation_report": str(output / "simulation" / "report.json"),
    }
    # Save input provenance even if the trusted evaluator rejects or raises.
    evidence_path = output / "adapter-report.json"
    evidence_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    report = run_verification(rtl_dir=staged, output_dir=output / "simulation",
                              width=width, n=n, k=k, count=count, seed=seed,
                              split=split, simulator=simulator, timeout=timeout)
    provenance.update(status=report["status"], passed=report["passed"],
                      checked_cases=report["checked_cases"],
                      frozen_contract=report["frozen_contract"])
    if report.get("reason"):
        provenance["reason"] = report["reason"]
    evidence_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    return provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rtl", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--split", choices=("development", "heldout"), default="heldout")
    parser.add_argument("--simulator", choices=("auto", "iverilog", "verilator"), default="auto")
    parser.add_argument("--timeout", type=float, default=300)
    report = verify_chisel(**vars(parser.parse_args(argv)))
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else (2 if report["status"] == "not_run" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
