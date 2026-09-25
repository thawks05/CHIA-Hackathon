# PIVOT: near-memory binary-vector design through CHIA

PIVOT provides a frozen functional evaluator, a programmable memory-layout
search space, matched grid/random controls, and an optional CHIA/Gemini design
loop. It follows the supplied plan's **no fine-tuning** scope. Generated data are
synthetic verification/workload examples; they are not learned model weights or
real-world classification evidence.

For a short compute window, start with [the 30-minute handoff](docs/30_MINUTES.md).
Full setup is in the [private GCP/Tailscale runbook](docs/COMPUTE_RUNBOOK.md). The
[implementation status](docs/IMPLEMENTATION_STATUS.md) lists which plan items
have executable code and which still need hardware/tool/cloud evidence.

## Fast local results: Python only

Run from this repository root with Python 3.10+ (no packages required):

```bash
python scripts/validate_local.py --output artifacts/local-check
python -m verification.workloads --output artifacts/workloads --samples 256 --seed 20260923
python -m pivot.search --output artifacts/controls --budget 100 --seed 20260923 --backend model
```

The controls make **100 grid + 100 random attempted evaluations**, plus a shared
baseline, against the same fixed workload. Results include `results.sqlite`,
`results.csv`, `summary.json`, `REPORT.md`, `model_pareto.json`, and the standalone
`control_progress.svg`. These are uncalibrated model estimates; no RTL pass,
measured frequency, physical area, or agent improvement is implied.

The data generator writes 256 HDC and 256 HTM demonstration examples with Python
reference outputs, SHA-256 provenance, and an explicit synthetic-data label.
Use `--split heldout` for independent evaluator-only data. The API-only design
agent receives neither file contents nor generator/golden source.

## Functional RTL verification

Install Icarus or Verilator on your Linux compute host, then:

```bash
python -m verification.run --output artifacts/rtl-100k \
  --count 100000 --seed 20260923 --backend verilator
```

Require `report.json` status `passed`, and the expected checked-case count.
The harness checks all output buses and case order. Missing tools, compile
failures, X/Z outputs, missing cases, and changed reference files cannot pass.
Simulator input and logs are evaluator-only artifacts. `--backend iverilog` is
also supported. Use a new output directory for each run.

The legacy `pivot_tile` opcodes remain XOR=0, rotate=1, threshold-vote=2,
per-bit-count=3, Hamming=4, nearest-K=5; 6/7 output zero. Count is not HTM
permanence learning. A separate `pivot_permanence` block and Chisel equivalent
implement saturation updates. See [hardware contracts](docs/HARDWARE.md).

## CHIA and a bounded compute window

With the project copied to your existing Linux head:

```bash
# Local fallback; archives real results and runs RTL checks if tools exist.
bash scripts/run_head.sh model artifacts/first-run

# Once your CHIA/Tailscale cluster is running with the supplied resource tags:
bash scripts/run_head.sh chia artifacts/distributed-run
```

Neither command creates VMs, trains a model, or enables paid LLM calls. Configure
the actual Gemini arm explicitly using the runbook. The Vertex provider shipped
by CHIA is experimental; failed/missing API access is recorded and never replaced
with fake agent data. See [CHIA workflow details](docs/CHIA_WORKFLOW.md).

Tailscale already running on your desktop is not enough for CHIA's userspace
relay mode: each Linux host needs the configured SOCKS proxy. Separate existing
userspace, existing kernel-Tailscale, and optional managed-GCP templates are
provided. Keep credentials outside the project. **Archive before `chia down`:
that command deletes CHIA-managed GCP instances and their auto-delete disks.**

## Evidence and submission

The [memory model notes](docs/MEMORY_MODEL.md) explain timing parameters,
approximation limits, address-program validation, and external gem5 observation
comparison. Synthesis adapters accept real installed tools; generic Yosys cell
counts are never called square micrometres. The shared-baseline synthesis node
does not supply candidate-specific area/frequency numbers.

Use the manuscript draft in `paper/` after reviewing the actual result archive.
Do not repeat the historical planning document's run counts or spend unless you
have their original records. Current local evidence is summarized in
[the local results](docs/LOCAL_RESULTS.md): 892 analytical evaluations and 512
synthetic examples have already been generated, with 53 Python tests passing.
The source ZIP and results ZIP include content-hash manifests. Runtime tools,
CHIA dependencies, credentials, and PDK files are not bundled.

References: [CHIA](https://github.com/ucb-bar/chia),
[tailnet configuration](https://docs.chialoops.ai/en/latest/user_guides/cluster_config_reference.html#tailnet-tailscale-clusters),
[hackathon submission](https://agentic-arch.org/hackathon.html).
