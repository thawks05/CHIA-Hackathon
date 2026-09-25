"""Stream reproducible directed and pseudorandom operation vectors.

--count counts random operations; directed checks are additional. Development
and heldout streams use distinct derived seeds. Heldout artifacts belong in an
evaluator-only directory, outside the candidate's filesystem and tool scope.
"""

import argparse
import hashlib
import json
from pathlib import Path
import random
from typing import Iterator

from model.pivot_golden import CONTRACT_VERSION, PivotGolden, pack_fields

SCHEMA_VERSION = "pivot-vectors-v1"
INPUT_KEYS = ("opcode", "operand_a", "operand_b", "vectors_in")
OUTPUT_KEYS = ("result_vector", "result_distance", "result_aux")


def split_seed(seed: int, split: str) -> int:
    if split not in ("development", "heldout"):
        raise ValueError("split must be development or heldout")
    digest = hashlib.sha256(f"{SCHEMA_VERSION}:{split}:{seed}".encode()).digest()
    return int.from_bytes(digest[:16], "big")


def generate_cases(width: int, n: int, k: int, count: int, seed: int,
                   split: str = "development", include_expected: bool = True) -> Iterator[dict]:
    """Yield native-int input dictionaries with expected output dictionary.

    The first records are directed. Then exactly count random records cycle
    through all eight opcodes, including reserved 6/7; inputs are random.
    Ordering and contents are deterministic for (dimensions,count,seed,split).
    This API does not retain the dataset in memory.
    """
    golden = PivotGolden(width, n, k)
    if not isinstance(count, int) or count < 0:
        raise ValueError("count must be nonnegative")
    rng = random.Random(split_seed(seed, split))
    mask = (1 << width) - 1
    alternating = sum(1 << bit for bit in range(0, width, 2))
    zero = [0] * n
    ones = [mask] * n
    striped = [alternating if index % 2 == 0 else mask ^ alternating for index in range(n)]
    onehots = [1 << (index % width) for index in range(n)]
    index = 0

    def record(opcode, a, b, vectors, kind, label):
        nonlocal index
        case = dict(case_id=index, kind=kind, label=label, opcode=opcode,
                    operand_a=a, operand_b=b,
                    vectors_in=pack_fields(vectors, width))
        if include_expected:
            case["expected"] = golden.evaluate_case(case)
        index += 1
        return case

    for label, a, b, vectors in (
        ("all_zero", 0, 0, zero),
        ("all_one", mask, mask, ones),
        ("opposites", 0, mask, ones),
        ("alternating", alternating, mask ^ alternating, striped),
        ("one_hot", 1, (1 << (width - 1)), onehots),
        ("duplicate_ties", alternating, 0, [alternating] * n),
    ):
        for opcode in range(8):
            yield record(opcode, a, b, vectors, "directed", label)
    for amount in sorted({0, 1, width - 1, width, width + 1, mask}):
        yield record(1, 1, amount & mask, striped, "directed", f"rotate_{amount}")
    thresholds = {0, 1, n // 2, n // 2 + 1, n, n + 1, (1 << golden.count_width) - 1}
    for threshold in sorted(thresholds):
        for label, vectors in (("zeros", zero), ("ones", ones), ("striped", striped)):
            yield record(2, threshold & mask, 0, vectors, "directed", f"threshold_{threshold}_{label}")
    reverse = [mask ^ ((1 << min(index, width)) - 1) for index in range(n)]
    yield record(4, 0, 0, reverse, "directed", "distance_packing")
    yield record(5, 0, 0, reverse, "directed", "reverse_distance_order")
    for sample in range(count):
        yield record(sample % 8, rng.getrandbits(width), rng.getrandbits(width),
                     [rng.getrandbits(width) for _ in range(n)], "random", f"random_{sample}")


def _hex_case(case: dict) -> dict:
    encoded = dict(case)
    for key in INPUT_KEYS[1:]:
        encoded[key] = hex(encoded[key])
    encoded["expected"] = {key: hex(value) for key, value in case["expected"].items()}
    return encoded


def write_dataset(output: Path | str, *, width=512, n=8, k=3, count=1000,
                  seed=0, split="development") -> dict:
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if (output / "cases.jsonl").exists() or (output / "manifest.json").exists():
        raise FileExistsError("dataset already exists; choose a fresh output directory")
    digest = hashlib.sha256()
    directed = total = 0
    with (output / "cases.jsonl").open("wb") as stream:
        for case in generate_cases(width, n, k, count, seed, split):
            line = (json.dumps(_hex_case(case), sort_keys=True, separators=(",", ":")) + "\n").encode()
            stream.write(line)
            digest.update(line)
            total += 1
            directed += case["kind"] == "directed"
    manifest = dict(schema=SCHEMA_VERSION, contract=CONTRACT_VERSION,
                    width=width, n=n, k=k, random_cases=count,
                    directed_cases=directed, total_cases=total, seed=seed,
                    split=split, derived_seed=str(split_seed(seed, split)),
                    data_sha256=digest.hexdigest(),
                    source="synthetic functional verification stimuli; no real-world training claims",
                    encoding="JSONL; packed inputs and expected outputs are hexadecimal strings",
                    visibility="evaluator_only" if split == "heldout" else "development",
                    generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    golden_sha256=hashlib.sha256((Path(__file__).parents[1] / "model" / "pivot_golden.py").read_bytes()).hexdigest())
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--n", type=int, default=8)
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--count", type=int, default=1000, help="random cases, in addition to directed cases")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--split", choices=("development", "heldout"), default="development")
    args = parser.parse_args(argv)
    print(json.dumps(write_dataset(**vars(args)), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
