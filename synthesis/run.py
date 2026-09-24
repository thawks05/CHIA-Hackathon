"""Invoke Yosys or Hammer and retain exact inputs, logs, and report provenance.

Yosys generic cell counts are structural proxies, never square micrometres.
Hammer physical metrics require an explicit report extraction specification.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
import uuid

ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _quoted(path: Path) -> str:
    value = path.resolve().as_posix()
    if any(char in value for char in ('"', '\n', '\r')):
        raise ValueError("Tool input paths cannot contain quotes or newlines")
    return '"' + value + '"'


def wrapper_text(width: int, vectors: int, top_k: int) -> str:
    if width < 1 or vectors < 1 or not 1 <= top_k <= vectors:
        raise ValueError("Require WIDTH>=1, N>=1, and 1<=K<=N")
    count_width = vectors.bit_length()
    distance_width = width.bit_length()
    index_width = max(1, (vectors - 1).bit_length())
    auxiliary = max(width * count_width, top_k * index_width)
    return f'''module pivot_synthesis_top(
    input wire clk, rst,
    input wire [2:0] opcode,
    input wire [{width-1}:0] operand_a, operand_b,
    input wire [{vectors*width-1}:0] vectors_in,
    output wire [{width-1}:0] result_vector,
    output wire [{vectors*distance_width-1}:0] result_distance,
    output wire [{auxiliary-1}:0] result_aux
);
    pivot_tile #(.WIDTH({width}), .N({vectors}), .K({top_k})) dut (
        .clk(clk), .rst(rst), .opcode(opcode),
        .operand_a(operand_a), .operand_b(operand_b), .vectors_in(vectors_in),
        .result_vector(result_vector), .result_distance(result_distance), .result_aux(result_aux)
    );
endmodule
'''


def extract_report_metrics(run_dir: Path, spec: dict) -> tuple[dict, dict]:
    """Extract exactly one positive metric from each real report in this run.

    A spec entry is {"path": "syn-rundir/reports/area.rpt", "regex":
    "Total cell area: ([0-9.]+)", "scale": 1}. Group 1 must be numeric.
    Report paths must remain under the fresh run directory. The caller owns
    verifying that the chosen regex and unit match the synthesis plugin.
    """
    metrics, evidence = {}, {}
    allowed = {"area_um2", "fmax_mhz", "critical_path_ns"}
    for name, rule in spec.items():
        if name not in allowed:
            raise ValueError(f"Unsupported physical metric: {name}")
        path = (run_dir / rule["path"]).resolve()
        if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
            raise ValueError(f"Missing report inside current run: {rule['path']}")
        matches = list(re.finditer(rule["regex"], path.read_text(errors="replace"), re.MULTILINE))
        if len(matches) != 1:
            raise ValueError(f"Expected one {name} match; got {len(matches)}")
        value = float(matches[0].group(1)) * float(rule.get("scale", 1))
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"Invalid {name}: {value}")
        metrics[name] = value
        evidence[name] = {"report": str(path), "sha256": sha256(path),
                          "matched_text": matches[0].group(0), "extraction": rule}
    return metrics, evidence


def run_synthesis(backend: str, output: str | Path, rtl=None, top="pivot_tile",
                  width=512, vectors=8, top_k=3, hammer_config=None,
                  hammer_env=None, corner=None, timeout=1800,
                  metrics_spec=None, candidate=None) -> dict:
    """Return a serializable result; missing software/configuration is not_run.

    `corner` labels provenance only; the actual process/corner is selected by
    the Hammer configuration. `candidate` is metadata and never changes RTL.
    Width/N/K are applied via a wrapper only for the legacy `pivot_tile` top.
    Other tops must already be elaborated with their desired parameters.
    """
    if backend not in {"yosys", "hammer"}:
        raise ValueError("backend must be yosys or hammer")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", top):
        raise ValueError("Invalid top module name")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_dir = output / (backend + "-" + uuid.uuid4().hex[:12])
    run_dir.mkdir()
    result = {
        "backend": backend, "status": "not_run", "metrics": {},
        "corner_label": corner, "run_dir": str(run_dir),
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "implementation_relation": "shared_baseline" if rtl is None else "explicit_rtl",
        "candidate": candidate,
        "metric_kind": "generic_structural_proxy" if backend == "yosys" else "physical_synthesis",
        "parameters": {"width": width, "vectors": vectors, "top_k": top_k},
        "top_module": top,
    }

    def finish(status: str, reason: str | None = None):
        result["status"] = status
        if reason:
            result["reason"] = reason
        encoded = json.dumps(result, indent=2, sort_keys=True)
        (run_dir / "result.json").write_text(encoded + "\n")
        (output / "result.json").write_text(encoded + "\n")
        return result

    executable = shutil.which("yosys" if backend == "yosys" else "hammer-vlsi")
    if not executable:
        return finish("not_run", f"{backend} executable is unavailable")
    if backend == "hammer" and not hammer_config:
        return finish("not_run", "Hammer requires a technology, tool, timing, and corner configuration")
    sources = [Path(item).resolve() for item in rtl] if rtl else sorted((ROOT / "rtl").glob("*.v"))
    if not sources or any(not path.is_file() for path in sources):
        return finish("failed", "RTL input is missing")
    result["rtl_sha256"] = {str(path): sha256(path) for path in sources}
    # Snapshot all primary inputs before launching a long running tool.
    snapshot = run_dir / "rtl"
    snapshot.mkdir()
    copied = []
    for number, source in enumerate(sources):
        target = snapshot / f"{number:03d}_{source.name}"
        shutil.copy2(source, target)
        copied.append(target)
    selected_top = top
    if top == "pivot_tile":
        wrapper = snapshot / "pivot_synthesis_top.v"
        try:
            wrapper.write_text(wrapper_text(width, vectors, top_k))
        except ValueError as exc:
            return finish("failed", str(exc))
        copied.append(wrapper)
        selected_top = "pivot_synthesis_top"
    result["synthesis_top"] = selected_top
    if backend == "yosys":
        script = run_dir / "synthesis.ys"
        stats_path = run_dir / "stats.json"
        netlist_path = run_dir / "netlist.json"
        script.write_text("\n".join([
            "read_verilog -sv " + " ".join(_quoted(path) for path in copied),
            f"hierarchy -check -top {selected_top}",
            f"synth -flatten -top {selected_top}",
            f"tee -o {_quoted(stats_path)} stat -json",
            f"write_json {_quoted(netlist_path)}", "",
        ]))
        command = [executable, "-s", str(script)]
    else:
        config = Path(hammer_config).resolve()
        if not config.is_file():
            return finish("not_run", "Hammer configuration file is missing")
        config_copy = run_dir / ("user-config" + config.suffix)
        shutil.copy2(config, config_copy)
        result["hammer_config"] = {"path": str(config), "sha256": sha256(config)}
        override = run_dir / "design.json"
        override.write_text(json.dumps({
            "synthesis.inputs.input_files": [str(path) for path in copied],
            "synthesis.inputs.top_module": selected_top,
        }, indent=2))
        command = [executable]
        if hammer_env:
            environment = Path(hammer_env).resolve()
            if not environment.is_file():
                return finish("not_run", "Hammer environment configuration is missing")
            env_copy = run_dir / ("user-env" + environment.suffix)
            shutil.copy2(environment, env_copy)
            result["hammer_env"] = {"path": str(environment), "sha256": sha256(environment)}
            command += ["-e", str(env_copy)]
        command += ["-p", str(config_copy), "-p", str(override), "--obj_dir", str(run_dir), "syn"]
    result["command"] = command
    result["tool_executable"] = executable
    result["log"] = str(run_dir / "tool.log")
    started = time.monotonic()
    try:
        with (run_dir / "tool.log").open("w") as log:
            completed = subprocess.run(command, cwd=ROOT, stdout=log,
                                       stderr=subprocess.STDOUT, timeout=timeout, check=False)
        result["elapsed_seconds"] = time.monotonic() - started
        result["exit_code"] = completed.returncode
        if completed.returncode:
            return finish("failed", "Synthesis returned a nonzero exit status; inspect tool.log")
        if backend == "yosys":
            stats = json.loads(stats_path.read_text())
            design = stats.get("design") or stats["modules"].get("\\" + selected_top)
            if design is None:
                raise ValueError("Yosys stats contain no selected design")
            netlist = json.loads(netlist_path.read_text())
            if selected_top not in netlist.get("modules", {}):
                raise ValueError("Yosys did not produce the requested netlist")
            result["metrics"] = {"generic_cell_count": int(design["num_cells"]),
                                 "generic_cell_types": design.get("num_cells_by_type", {})}
            result["stats_sha256"] = sha256(stats_path)
            result["netlist_sha256"] = sha256(netlist_path)
            result["physical_metrics_available"] = False
        else:
            ir_path = run_dir / "syn-rundir" / "syn-output.json"
            if not ir_path.is_file():
                raise ValueError("Hammer produced no synthesis output IR")
            ir = json.loads(ir_path.read_text())
            outputs = ir.get("synthesis.outputs.output_files", [])
            mapped = [Path(path) if Path(path).is_absolute() else ROOT / path for path in outputs]
            mapped += list((run_dir / "syn-rundir").glob("*.mapped.v"))
            mapped = [path.resolve() for path in mapped if path.is_file() and path.stat().st_size]
            # Only netlists from the fresh synthesis directory can count. In
            # particular, a nop plugin returning our input snapshot is not a
            # completed synthesis result.
            mapped = [path for path in mapped
                      if path.is_relative_to(run_dir / "syn-rundir")
                      and path.suffix.lower() in {".v", ".sv"}]
            if not mapped:
                raise ValueError("Hammer produced no nonempty mapped netlist in this run")
            result["mapped_netlists"] = {str(path): sha256(path) for path in mapped}
            if metrics_spec:
                spec = metrics_spec if isinstance(metrics_spec, dict) else json.loads(Path(metrics_spec).read_text())
                result["metrics"], result["report_evidence"] = extract_report_metrics(run_dir, spec)
            result["physical_metrics_available"] = bool(result["metrics"])
            if not result["metrics"]:
                result["metric_note"] = "Synthesis completed; plugin reports require a verified extraction specification"
        return finish("completed")
    except subprocess.TimeoutExpired:
        result["elapsed_seconds"] = time.monotonic() - started
        return finish("failed", f"Synthesis exceeded {timeout} seconds")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["metrics"] = {}
        return finish("failed", str(exc))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True, choices=["yosys", "hammer"])
    parser.add_argument("--output", required=True)
    parser.add_argument("--rtl", nargs="+")
    parser.add_argument("--top", default="pivot_tile")
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--vectors", type=int, default=8)
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--hammer-config")
    parser.add_argument("--hammer-env")
    parser.add_argument("--corner", help="Provenance label; corner selection comes from the Hammer config")
    parser.add_argument("--metrics-spec", help="JSON mapping metrics to report paths and numeric regexes")
    parser.add_argument("--candidate", help="JSON provenance only; does not modify RTL")
    parser.add_argument("--timeout", type=int, default=1800)
    values = vars(parser.parse_args(argv))
    if values["candidate"]:
        values["candidate"] = json.loads(Path(values["candidate"]).read_text())
    result = run_synthesis(**values)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
