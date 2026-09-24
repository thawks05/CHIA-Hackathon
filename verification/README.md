# Pivot functional verification

This lane produces synthetic verification data. It does not fine-tune an LLM,
train a predictor, or establish application accuracy. The six combinational RTL
operations are XOR (0), rotate left (1), threshold vote (2), per-bit counts (3),
Hamming distances (4), and nearest K indices (5). Opcodes 6 and 7 return zero.

All fields use the least significant bits for field/index zero. Top-K sorts by
distance, then candidate index. `operand_a` supplies the low `ceil(log2(N+1))`
threshold bits for operation 2, zero-extended if WIDTH is smaller; a threshold
of zero produces all ones. Shift first takes the low `max(1,ceil(log2(WIDTH)))`
bits of `operand_b`, then wraps modulo WIDTH. WIDTH and N must be positive,
and `1 <= K <= N`. Auxiliary width is the maximum of packed counts and indices.
The tile is combinational: its clock and reset do not affect output values.

The additional Python HDC, global HTM spatial pooler, and saturating permanence
references in `model/pivot_golden.py` document their own contracts. They are not
additional tile opcodes. In particular, OP_COUNTER counts vector bits and does
not update permanence. The spatial pooler implements global inhibition over
supplied connected-synapse masks, without boosting or temporal memory.

Run from the repository root with Python 3.10 or later:

```bash
python -m unittest discover -s tests -p 'test_golden.py' -v
python -m unittest discover -s tests -p 'test_verification.py' -v
python -m verification.generate --output artifacts/dev-vectors \
  --count 1000 --seed 42 --split development --width 512 --n 8 --k 3
python -m verification.workloads --output artifacts/workloads \
  --samples 256 --seed 20260923 --split demonstration
python -m verification.run --rtl-dir rtl --output artifacts/rtl-smoke \
  --count 0 --seed 2026 --backend iverilog
python -m verification.run --rtl-dir rtl --output artifacts/rtl-heldout \
  --count 100000 --seed 90210 --split heldout --backend iverilog --timeout 1800
```

Use a fresh output directory on every run. `--count` specifies random operations;
directed edge cases are additional. All eight opcodes cycle across random inputs.
`--backend verilator` uses Verilator's `--binary --timing` SystemVerilog harness;
it also needs a C++ compiler and Make. `--backend auto` prefers Icarus when both
`iverilog` and `vvp` are installed. The exit codes are 0 for a passing simulation,
1 for failure, and 2 when the tool is unavailable (`status: not_run`). Missing
tools never count as a successful RTL test.

`verification.generate` streams hexadecimal JSONL records plus a manifest with
dimensions, public seed, split, derived seed, SHA-256 digest, and source hashes.
The development and heldout random streams are distinct; directed checks overlap
intentionally. The data is reproducible and stays bounded in memory. Full-width
100,000-record JSONL can occupy hundreds of megabytes; the RTL runner writes a
more compact stimulus file instead and retains expected answers only in memory.

`verification.run.run_verification(...)` returns `report.json`, including source
hashes, simulator/version, counts, timings, and failure reasons. The trusted test
bench prints observed outputs; the independent Python comparator detects wrong,
unknown, missing, repeated, reordered, or extra outputs. No PPA result is inferred
from a passing simulation. `--count 0` provides the elaboration and directed smoke
gate; `--count 100000` supplies the random differential gate.

The default heldout report redacts per-case expected/observed values. Keep the
whole evaluation directory and heldout seed private: `stimuli.hex` and
`simulation.log` still contain the exercised inputs and observations. Candidate
agents should receive only aggregate gate results, approved diagnostic summaries,
and development test inputs. They must not receive the evaluator source, frozen
manifest, heldout trace directory, or write access to any evaluator artifacts.

`frozen_contract.json` records reviewed reference, generator, runner, and harness
hashes. Evaluation refuses to run if those files differ. Update this manifest only
after an explicit reference change and independent review; an optimization agent
must never update it. The manifest is an integrity check, not an OS sandbox. The
runner invokes local tools with the caller's permissions: untrusted RTL needs
an isolated worker/container that cannot read or modify evaluator files. Mount
only candidate RTL, the capture harness, and input stimuli in the simulator.

The example heldout seed above is public documentation. Choose a new private seed
for a genuinely unseen acceptance run; keep it outside any candidate prompt.

The workloads command writes 256 HDC examples and 256 HTM examples by default,
with hexadecimal inputs, Python reference outputs, and a provenance manifest.
HDC examples bind and bundle supplied random vectors and rank supplied prototypes;
HTM examples run global inhibition over fixed synthetic connectivity and show a
separate single-step permanence update. They provide reproducible functional data
without claiming classification accuracy or that the current RTL implements the
whole application. `--split heldout` creates a distinct random workload stream.
