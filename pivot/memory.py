"""Cycle-approximate read-only memory model; never a measured HBM result."""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from pivot.program import ProgramError, compile_program

ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class MemoryConfig:
    pseudo_channels: int = 16
    banks_per_channel: int = 16
    burst_bytes: int = 32
    row_bytes: int = 1024
    tck_ns: float = 1.0
    t_rcd_cycles: int = 12
    t_cl_cycles: int = 18
    t_rp_cycles: int = 14
    t_ras_cycles: int = 28
    t_ccd_cycles: int = 3
    t_burst_cycles: int = 2

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for name, value in asdict(self).items() if name != "tck_ns"):
            raise ValueError("memory dimensions and timings must be positive integers")
        if not math.isfinite(self.tck_ns) or self.tck_ns <= 0:
            raise ValueError("clock period must be finite and positive")
        if self.row_bytes % self.burst_bytes:
            raise ValueError("row_bytes must be a multiple of burst_bytes")
        if self.pseudo_channels & (self.pseudo_channels - 1):
            raise ValueError("pseudo_channels must be a power of two for xor_swizzle")

    @classmethod
    def load(cls, path: str | Path | None = None):
        raw = json.loads(Path(path or ROOT / "configs/memory_hbm2.json").read_text())
        return cls(**{k: raw[k] for k in cls.__dataclass_fields__ if k in raw})


def frozen_workload(seed: int, *, width: int = 512, vectors: int = 256, queries: int = 8) -> dict:
    """Deterministic synthetic read traces. Candidate cannot change work volume.

    HDC scans a codebook for each query. The sparse-vector workload scans a
    shuffled subset of column masks. This is not a real-world task dataset.
    """
    if width <= 0 or vectors < 2 or queries <= 0:
        raise ValueError("invalid workload dimensions")
    rng = random.Random(seed)
    bursts = math.ceil(width / 256)
    groups = []
    for query in range(queries):
        groups.append({"workload": "hdc_query", "query": query, "operations": vectors,
                       "accesses": [(v, b) for v in range(vectors) for b in range(bursts)]})
        selected = rng.sample(range(vectors), vectors // 4)
        groups.append({"workload": "sparse_overlap", "query": query, "operations": len(selected),
                       "accesses": [(v, b) for v in selected for b in range(bursts)]})
    payload = {"schema_version": 1, "kind": "synthetic_evaluation_trace", "width": width,
               "seed": seed, "vectors": vectors, "queries": queries, "groups": groups}
    payload["sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload


def simulate(candidate: dict, workload: dict, config: MemoryConfig | None = None) -> dict:
    cfg = config or MemoryConfig.load()
    params = candidate.get("params", {})
    lane = params.get("lane_width", 128)
    depth = params.get("prefetch_depth", 8)
    layout = params.get("layout", "vector_major")
    issue = params.get("issue_policy", "fifo")
    if type(lane) is not int or lane not in (64, 128, 256, 512):
        raise ValueError("lane_width must be 64,128,256,512")
    if type(depth) is not int or depth not in (1, 2, 4, 8, 16, 32):
        raise ValueError("prefetch_depth must be 1,2,4,8,16,32")
    if layout not in ("vector_major", "bit_interleaved", "xor_swizzle"):
        raise ValueError("unknown layout")
    if issue not in ("fifo", "row_hit_first"):
        raise ValueError("unknown issue policy")
    program = compile_program(candidate.get("program"))
    channels, banks = cfg.pseudo_channels, cfg.banks_per_channel
    row_bursts = cfg.row_bytes // cfg.burst_bytes
    bursts_per_vector = math.ceil(workload["width"] / (cfg.burst_bytes * 8))
    if bursts_per_vector != math.ceil(workload["width"] / 256):
        raise ValueError("this frozen trace uses 32-byte bursts; regenerate for other burst sizes")
    mapping, reverse = {}, {}
    for v in range(workload["vectors"]):
        for b in range(bursts_per_vector):
            linear = v * bursts_per_vector + b
            ctx = {"v": v, "b": b, "linear": linear, "channels": channels, "banks": banks,
                   "row_bursts": row_bursts, "bursts_per_vector": bursts_per_vector}
            if "channel" in program:
                address = tuple(program[name](ctx) for name in ("channel", "bank", "row", "column"))
            elif layout == "vector_major":
                address = ((linear // row_bursts) % channels, (linear // (row_bursts * channels)) % banks,
                           linear // (row_bursts * channels * banks), linear % row_bursts)
            else:
                channel = linear % channels if layout == "bit_interleaved" else (linear ^ (linear // channels)) % channels
                address = (channel, (linear // channels) % banks,
                           linear // (channels * banks * row_bursts), (linear // (channels * banks)) % row_bursts)
            ch, bank, row, col = address
            if not (0 <= ch < channels and 0 <= bank < banks and 0 <= col < row_bursts and 0 <= row < 2**31):
                raise ProgramError("address coordinate outside physical bounds")
            if address in reverse:
                raise ProgramError("address alias: two logical bursts map to the same physical burst")
            reverse[address] = (v, b)
            mapping[(v, b)] = (address, ctx)

    total_memory = total_compute = total_bursts = row_hits = row_misses = row_conflicts = 0
    per_workload = {}
    for group in workload["groups"]:
        # Each group has a dependency barrier. Cold row buffers per group are
        # deliberately conservative and identical across every search arm.
        opened = [[None] * banks for _ in range(channels)]
        activated = [[-cfg.t_ras_cycles] * banks for _ in range(channels)]
        bank_free = [[0] * banks for _ in range(channels)]
        channel_free = [0] * channels
        channel_column_free = [0] * channels
        inflight = []
        issue_cycle = 0
        accesses = list(enumerate(group["accesses"]))
        while accesses:
            window = accesses[:depth]
            if issue == "row_hit_first" or "priority" in program:
                def priority(item):
                    ordinal, key = item
                    (ch, bank, row, col), ctx = mapping[tuple(key)]
                    hit = int(opened[ch][bank] == row)
                    if "priority" in program:
                        return (program["priority"]({**ctx, "channel": ch, "bank": bank,
                                "row": row, "column": col, "hit": hit, "ordinal": ordinal}), ordinal)
                    return (-hit, ordinal)
                chosen = min(window, key=priority)
            else:
                chosen = window[0]
            accesses.remove(chosen)
            ordinal, key = chosen
            if len(inflight) >= depth:
                issue_cycle = max(issue_cycle, heapq.heappop(inflight))
            while inflight and inflight[0] <= issue_cycle:
                heapq.heappop(inflight)
            (ch, bank, row, _), _ctx = mapping[tuple(key)]
            start = max(issue_cycle, bank_free[ch][bank])
            if opened[ch][bank] == row:
                row_hits += 1
                column_command = start
            else:
                row_misses += 1
                if opened[ch][bank] is not None:
                    row_conflicts += 1
                    start = max(start, activated[ch][bank] + cfg.t_ras_cycles) + cfg.t_rp_cycles
                activated[ch][bank] = start
                opened[ch][bank] = row
                column_command = start + cfg.t_rcd_cycles
            # One shared column-command interval per pseudo-channel. Bank
            # interleaving must not bypass this constraint. Bank groups are
            # not modeled, so the configured tCCD is applied conservatively.
            column_command = max(column_command, channel_column_free[ch],
                                 channel_free[ch] - cfg.t_cl_cycles)
            channel_column_free[ch] = column_command + cfg.t_ccd_cycles
            data_start = column_command + cfg.t_cl_cycles
            finish = data_start + cfg.t_burst_cycles
            channel_free[ch] = finish
            bank_free[ch][bank] = column_command + cfg.t_ccd_cycles
            heapq.heappush(inflight, finish)
            issue_cycle += 1
        memory_cycles = max(channel_free)
        compute_cycles = group["operations"] * math.ceil(workload["width"] / lane)
        total_memory += memory_cycles
        total_compute += compute_cycles
        total_bursts += len(group["accesses"])
        summary = per_workload.setdefault(group["workload"], {"model_cycles": 0, "operations": 0})
        summary["model_cycles"] += memory_cycles + compute_cycles
        summary["operations"] += group["operations"]
    operations = sum(g["operations"] for g in workload["groups"])
    cycles = total_memory + total_compute
    transferred_bytes = total_bursts * cfg.burst_bytes
    return {"model_cycles": cycles, "model_memory_cycles": total_memory,
            "model_compute_cycles": total_compute, "model_operations_per_cycle": operations / cycles,
            "model_bandwidth_utilization": transferred_bytes / (total_memory * channels * cfg.burst_bytes / cfg.t_burst_cycles),
            "operations": operations, "transferred_bytes": transferred_bytes, "bursts": total_bursts,
            "row_hits": row_hits, "row_misses": row_misses, "row_conflicts": row_conflicts,
            "queue_storage_bytes": depth * cfg.burst_bytes, "lane_width": lane,
            "per_workload": per_workload, "physical_area_um2": None, "fmax_hz": None,
            "throughput_per_area": None, "measurement_kind": "cycle_approximate_python_model",
            "calibration_status": "not_calibrated_against_gem5_runs"}
