"""Export actual records as CSV, a model Pareto set, and a standalone SVG."""
from __future__ import annotations

import csv
import html
import json
from pathlib import Path


def model_pareto(rows):
    valid = [r for r in rows if r["result"].get("metrics")]
    def objectives(row):
        m = row["result"]["metrics"]
        return (m["model_cycles"], m["queue_storage_bytes"], m["lane_width"])
    return [row for row in valid if not any(
        all(a <= b for a, b in zip(objectives(other), objectives(row))) and
        any(a < b for a, b in zip(objectives(other), objectives(row))) for other in valid)]


def write_report(output, metadata, rows):
    output = Path(output)
    counts = {}
    for row in rows:
        arm = row["arm"]
        entry = counts.setdefault(arm, {"attempted": 0, "model_evaluated": 0, "rtl_verified": 0,
                                       "failed_or_not_run": 0, "best_model_operations_per_cycle": None,
                                       "evaluation_seconds": 0.0})
        entry["attempted"] += 1
        result = row["result"]
        entry["evaluation_seconds"] += result.get("duration_seconds", 0)
        m = result.get("metrics")
        if m:
            entry["model_evaluated"] += 1
            value = m["model_operations_per_cycle"]
            entry["best_model_operations_per_cycle"] = max(entry["best_model_operations_per_cycle"] or 0, value)
        else:
            entry["failed_or_not_run"] += 1
        if result.get("gate", {}).get("status") == "passed":
            entry["rtl_verified"] += 1
    summary = {"run_id": metadata["run_id"], "measurement_kind": "cycle_approximate_python_model",
               "calibration_status": "not_calibrated_against_gem5_runs", "arms": counts,
               "agent_status": metadata["agent_status"], "physical_synthesis_status": "not_run",
               "limitation": "A model comparison is not evidence of hardware speedup or LLM superiority."}
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    front = [{"candidate": r["candidate"], "metrics": r["result"]["metrics"]} for r in model_pareto(rows)]
    (output / "model_pareto.json").write_text(json.dumps({"objectives": ["model_cycles", "queue_storage_bytes", "lane_width"],
        "physical_ppa": False, "points": front}, indent=2), encoding="utf-8")
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["ordinal", "arm", "candidate_id", "status", "model_cycles", "model_ops_per_cycle", "queue_bytes", "lane_width", "workload_sha256", "evaluator_sha256"])
        for row in rows:
            r = row["result"]
            m = r.get("metrics") or {}
            p = r.get("provenance", {})
            writer.writerow([row["ordinal"], row["arm"], r["candidate_id"], r["status"], m.get("model_cycles"),
                             m.get("model_operations_per_cycle"), m.get("queue_storage_bytes"), m.get("lane_width"),
                             p.get("workload_sha256"), p.get("evaluator_sha256")])
    _progress_svg(output / "control_progress.svg", rows)
    report = ["# PIVOT control experiment", "", "These are model estimates on synthetic workloads; no physical PPA or calibrated gem5 measurements are included.", "",
              "| Arm | Attempts | Model evaluations | RTL verified | Best model ops/cycle |", "|---|---:|---:|---:|---:|"]
    for arm, count in counts.items():
        best = count["best_model_operations_per_cycle"]
        best_text = f"{best:.6f}" if best is not None else "not available"
        report.append(f"| {arm} | {count['attempted']} | {count['model_evaluated']} | {count['rtl_verified']} | {best_text} |")
    report += ["", f"Agent arm: **{metadata['agent_status']}**. Imported candidates, when present, are not evidence of an autonomous LLM loop.", "",
               "Matched control budgets count attempts, including rejected candidates. Wall time is recorded separately; no equal-dollar claim is made.", "",
               "The model Pareto set minimizes modeled cycles, queue storage bytes, and lane width. It is not a silicon area/frequency Pareto front."]
    (output / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return summary


def _progress_svg(path, rows):
    series = {}
    for row in rows:
        if row["arm"] == "baseline":
            continue
        values = series.setdefault(row["arm"], [])
        value = (row["result"].get("metrics") or {}).get("model_operations_per_cycle", 0)
        values.append(max(values[-1] if values else 0, value))
    max_x = max((len(v) for v in series.values()), default=1)
    max_y = max((max(v, default=0) for v in series.values()), default=1) or 1
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="470" viewBox="0 0 900 470">',
             '<rect width="900" height="470" fill="#f8fafc"/>',
             '<g font-family="sans-serif" fill="#102030">',
             '<text x="75" y="35" font-size="23">PIVOT: best model throughput by evaluation count</text>',
             '<text x="75" y="60" font-size="13">Synthetic trace · uncalibrated cycle model · no physical PPA measurements</text>',
             '<path d="M75 90 V390 H845" stroke="#8191a4" fill="none"/>']
    for tick in range(6):
        y = 390 - tick * 60
        parts += [f'<path d="M75 {y} H845" stroke="#dde4eb"/>',
                  f'<text x="10" y="{y + 5}" font-size="12">{tick * max_y / 5:.3f}</text>']
    colors = {"grid": "#2563eb", "random": "#db6b14", "agent": "#059669"}
    for i, (arm, values) in enumerate(series.items()):
        points = " ".join(f"{75 + 770 * (j + 1) / max_x:.2f},{390 - 300 * value / max_y:.2f}" for j, value in enumerate(values))
        color = colors.get(arm, "#64748b")
        parts.append(f'<polyline points="{points}" stroke="{color}" stroke-width="2.5" fill="none"/>')
        parts.append(f'<text x="{75 + i*200}" y="445" fill="{color}" font-size="16">{html.escape(arm)} ({len(values)} attempts)</text>')
    parts += [f'<text x="760" y="415" font-size="12">{max_x} evaluations</text>', '</g></svg>']
    path.write_text("\n".join(parts), encoding="utf-8")
