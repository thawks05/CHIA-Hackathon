"""Generate synthetic HDC/HTM demonstration inputs and reference outputs.

No fitting or fine-tuning occurs. Outputs demonstrate reproducible functional
contracts, not real-world accuracy and not measured RTL behavior.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random

from model.pivot_golden import hdc_encode, hdc_query, permanence_update, spatial_pooler

SCHEMA = "pivot-synthetic-workloads-v1"


def _rng(seed: int, split: str, workload: str) -> random.Random:
    digest = hashlib.sha256(f"{SCHEMA}:{split}:{workload}:{seed}".encode()).digest()
    return random.Random(int.from_bytes(digest, "big"))


def generate_hdc(samples: int, seed: int, split="demonstration", width=512):
    rng = _rng(seed, split, "hdc")
    for index in range(samples):
        features = [rng.getrandbits(width) for _ in range(8)]
        values = [rng.getrandbits(width) for _ in range(8)]
        prototypes = [rng.getrandbits(width) for _ in range(8)]
        encoded = hdc_encode(features, values, width)
        nearest = hdc_query(encoded, prototypes, width, k=3)
        yield dict(sample_id=index, workload="hdc_bind_bundle_query", width=width,
                   feature_vectors=[hex(value) for value in features],
                   value_vectors=[hex(value) for value in values],
                   prototype_vectors=[hex(value) for value in prototypes],
                   reference=dict(encoded=hex(encoded), nearest_indices=[item[0] for item in nearest],
                                  nearest_distances=[item[1] for item in nearest]),
                   provenance="synthetic random vectors; prototypes are supplied, not learned")


def generate_htm(samples: int, seed: int, split="demonstration", width=512):
    rng = _rng(seed, split, "htm")
    for index in range(samples):
        # Approximate independent densities: 1/8 active inputs, 1/4 synapses.
        active = rng.getrandbits(width) & rng.getrandbits(width) & rng.getrandbits(width)
        masks = [rng.getrandbits(width) & rng.getrandbits(width) for _ in range(16)]
        pooler = spatial_pooler(active, masks, width, k_winners=3, min_overlap=1)
        permanences = [rng.randrange(256) for _ in range(16)]
        update_active = rng.getrandbits(16)
        updated = permanence_update(permanences, update_active, 3, 2, 8)
        yield dict(sample_id=index, workload="htm_global_spatial_pooler_and_permanence", input_width=width,
                   active_input=hex(active), connected_synapses=[hex(value) for value in masks],
                   k_winners=3, min_overlap=1,
                   permanence_example=dict(before=permanences, active_mask=hex(update_active),
                                           increment=3, decrement=2, counter_width=8),
                   reference=dict(**pooler, permanence_after=updated),
                   provenance="synthetic independent masks; fixed connectivity; separate one-step permanence example")


def write_workloads(output, *, seed=20260923, samples=256, split="demonstration", width=512) -> dict:
    if not isinstance(samples, int) or samples < 1 or not isinstance(width, int) or width < 1:
        raise ValueError("samples and width must be positive integers")
    if split not in ("demonstration", "heldout"):
        raise ValueError("split must be demonstration or heldout")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("manifest.json", "hdc.jsonl", "htm.jsonl")):
        raise FileExistsError("workload data already exists; choose a fresh output directory")
    artifacts = {}
    for name, cases in (("hdc.jsonl", generate_hdc(samples, seed, split, width)),
                        ("htm.jsonl", generate_htm(samples, seed, split, width))):
        digest = hashlib.sha256()
        with (output / name).open("wb") as stream:
            for case in cases:
                line = (json.dumps(case, sort_keys=True, separators=(",", ":")) + "\n").encode()
                stream.write(line)
                digest.update(line)
        artifacts[name] = dict(samples=samples, sha256=digest.hexdigest())
    manifest = dict(schema=SCHEMA, split=split, seed=seed, width=width,
                    samples_per_workload=samples, total_samples=2 * samples, artifacts=artifacts,
                    provenance="generated synthetic demonstration data with Python reference outputs",
                    visibility="evaluator_only" if split == "heldout" else "demonstration",
                    fitting_performed=False, fine_tuning_performed=False,
                    rtl_validation_performed=False,
                    limitation="No real-world labels, prediction accuracy, trained model, or measured hardware result",
                    split_method="independent SHA-256-derived RNG seeds for each split and workload",
                    generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    golden_sha256=hashlib.sha256((Path(__file__).parents[1] / "model" / "pivot_golden.py").read_bytes()).hexdigest())
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seed", default=20260923, type=int)
    parser.add_argument("--samples", default=256, type=int)
    parser.add_argument("--width", default=512, type=int)
    parser.add_argument("--split", choices=("demonstration", "heldout"), default="demonstration")
    print(json.dumps(write_workloads(**vars(parser.parse_args(argv))), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
