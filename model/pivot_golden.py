"""Frozen functional reference for the six-operation combinational Pivot tile.

Packing is little-field-first: candidate/index/count zero occupies the least
significant field. Inputs are unsigned, exactly WIDTH bits per vector. clk/rst
are deliberately absent because the RTL has no sequential state. This module
uses only Python's standard library and is independent of candidate RTL.
"""

from dataclasses import dataclass
from typing import Iterable, Sequence

OP_XOR, OP_SHIFT, OP_MAJORITY, OP_COUNTER, OP_HAMMING, OP_TOPK = range(6)
CONTRACT_VERSION = "pivot-combinational-v1"


def _positive(value: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _unsigned(value: int, bits: int, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must fit in {bits} unsigned bits")
    return value


def pack_fields(values: Iterable[int], field_width: int) -> int:
    """Pack unsigned fields, field zero least significant."""
    _positive(field_width, "field_width")
    result = 0
    for index, value in enumerate(values):
        result |= _unsigned(value, field_width, "field") << (index * field_width)
    return result


def unpack_fields(packed: int, field_width: int, count: int) -> list[int]:
    _positive(field_width, "field_width")
    _positive(count, "count")
    _unsigned(packed, field_width * count, "packed")
    mask = (1 << field_width) - 1
    return [(packed >> (index * field_width)) & mask for index in range(count)]


def rotate_left(value: int, amount: int, width: int) -> int:
    """Mathematical rotation; unlike OP_SHIFT, amount is not first truncated."""
    _positive(width, "width")
    _unsigned(value, width, "value")
    if not isinstance(amount, int) or amount < 0:
        raise ValueError("amount must be nonnegative")
    amount %= width
    return ((value << amount) | (value >> (width - amount))) & ((1 << width) - 1)


def _count_planes(vectors: Sequence[int], count_width: int) -> list[int]:
    # Bit-sliced addition counts all WIDTH lanes in parallel using big integers.
    planes = [0] * count_width
    for vector in vectors:
        carry = vector
        for plane in range(count_width):
            planes[plane], carry = planes[plane] ^ carry, planes[plane] & carry
    return planes


def _threshold_vote(vectors: Sequence[int], threshold: int, width: int) -> int:
    mask = (1 << width) - 1
    planes = _count_planes(vectors, max(1, len(vectors).bit_length()))
    if threshold > len(vectors):
        return 0
    equal, greater = mask, 0
    for index in reversed(range(len(planes))):
        if (threshold >> index) & 1:
            equal &= planes[index]
        else:
            greater |= equal & planes[index]
            equal &= ~planes[index]
    return (equal | greater) & mask


@dataclass(frozen=True)
class PivotGolden:
    width: int = 512
    n: int = 8
    k: int = 3

    def __post_init__(self) -> None:
        _positive(self.width, "width")
        _positive(self.n, "n")
        _positive(self.k, "k")
        if self.k > self.n:
            raise ValueError("k must not exceed n")

    @property
    def count_width(self) -> int:
        return self.n.bit_length()

    @property
    def distance_width(self) -> int:
        return self.width.bit_length()

    @property
    def index_width(self) -> int:
        return max(1, (self.n - 1).bit_length())

    @property
    def aux_width(self) -> int:
        return max(self.width * self.count_width, self.k * self.index_width)

    def evaluate(self, opcode: int, operand_a: int, operand_b: int,
                 vectors_in: int | Sequence[int]) -> dict[str, int]:
        _unsigned(opcode, 3, "opcode")
        _unsigned(operand_a, self.width, "operand_a")
        _unsigned(operand_b, self.width, "operand_b")
        if isinstance(vectors_in, int):
            vectors = unpack_fields(vectors_in, self.width, self.n)
        else:
            vectors = list(vectors_in)
            if len(vectors) != self.n:
                raise ValueError(f"vectors_in must have {self.n} vectors")
            for vector in vectors:
                _unsigned(vector, self.width, "vector")
        result = dict(result_vector=0, result_distance=0, result_aux=0)
        if opcode == OP_XOR:
            result["result_vector"] = operand_a ^ operand_b
        elif opcode == OP_SHIFT:
            shift_width = max(1, (self.width - 1).bit_length())
            amount = operand_b & ((1 << shift_width) - 1)
            result["result_vector"] = rotate_left(operand_a, amount, self.width)
        elif opcode == OP_MAJORITY:
            threshold = operand_a & ((1 << self.count_width) - 1)
            result["result_vector"] = _threshold_vote(vectors, threshold, self.width)
        elif opcode == OP_COUNTER:
            planes = _count_planes(vectors, self.count_width)
            counts = (sum(((plane >> bit) & 1) << index
                          for index, plane in enumerate(planes))
                      for bit in range(self.width))
            result["result_aux"] = pack_fields(counts, self.count_width)
        elif opcode in (OP_HAMMING, OP_TOPK):
            distances = [(operand_a ^ vector).bit_count() for vector in vectors]
            if opcode == OP_HAMMING:
                result["result_distance"] = pack_fields(distances, self.distance_width)
            else:
                indices = sorted(range(self.n), key=lambda index: (distances[index], index))[:self.k]
                result["result_aux"] = pack_fields(indices, self.index_width)
        return result

    def evaluate_case(self, case: dict) -> dict[str, int]:
        return self.evaluate(*(case[key] for key in
                               ("opcode", "operand_a", "operand_b", "vectors_in")))


def evaluate_case(case: dict, width: int = 512, n: int = 8, k: int = 3) -> dict[str, int]:
    return PivotGolden(width, n, k).evaluate_case(case)


def hdc_encode(feature_vectors: Sequence[int], value_vectors: Sequence[int],
               width: int = 512) -> int:
    """Bind each feature/value pair by XOR, bundle using strict majority.

    Vectors are supplied by the caller (no learned/random codebook is implied).
    An even bundle's tied bits become zero. This is a reference algorithm, not
    evidence of classification accuracy or implementation in the current RTL.
    """
    _positive(width, "width")
    if not feature_vectors or len(feature_vectors) != len(value_vectors):
        raise ValueError("feature_vectors and value_vectors must have equal nonzero length")
    bound = [_unsigned(feature, width, "feature") ^ _unsigned(value, width, "value")
             for feature, value in zip(feature_vectors, value_vectors)]
    return _threshold_vote(bound, len(bound) // 2 + 1, width)


def hdc_query(query: int, prototypes: Sequence[int], width: int = 512,
              k: int = 1) -> list[tuple[int, int]]:
    """Return (prototype_index, Hamming_distance), nearest first; index breaks ties."""
    _positive(width, "width")
    _unsigned(query, width, "query")
    _positive(k, "k")
    if k > len(prototypes):
        raise ValueError("k must not exceed prototype count")
    distances = [(_unsigned(value, width, "prototype") ^ query).bit_count()
                 for value in prototypes]
    return sorted(enumerate(distances), key=lambda pair: (pair[1], pair[0]))[:k]


def spatial_pooler(active_input: int, connected_synapses: Sequence[int],
                   input_width: int, k_winners: int, min_overlap: int = 1) -> dict:
    """HTM global inhibition reference with fixed, already-connected synapse masks.

    Overlap is popcount(active_input & mask). Select up to k_winners columns
    whose overlap >= min_overlap, descending overlap, ties lower column index.
    No boosting, local inhibition, temporal memory, or learning is implied.
    """
    _positive(input_width, "input_width")
    _unsigned(active_input, input_width, "active_input")
    _positive(k_winners, "k_winners")
    if not connected_synapses or k_winners > len(connected_synapses):
        raise ValueError("k_winners must not exceed nonempty column count")
    if not isinstance(min_overlap, int) or min_overlap < 0:
        raise ValueError("min_overlap must be nonnegative")
    overlaps = [(active_input & _unsigned(mask, input_width, "synapse mask")).bit_count()
                for mask in connected_synapses]
    winners = sorted((i for i, overlap in enumerate(overlaps) if overlap >= min_overlap),
                     key=lambda i: (-overlaps[i], i))[:k_winners]
    return dict(overlaps=overlaps, winners=winners,
                active_columns=sum(1 << index for index in winners))


htm_spatial_pooler = spatial_pooler


def permanence_update(permanences: Sequence[int], active: int | Sequence[bool],
                      increment: int = 1, decrement: int = 1,
                      counter_width: int = 8) -> list[int]:
    """Saturating synapse updates; this is distinct from OP_COUNTER's bit counts.

    Active lanes add increment, inactive lanes subtract decrement, clamped to
    [0, 2**counter_width-1]. Integer active masks use lane zero as the low bit.
    This does not choose which columns learn; caller supplies the updated lanes.
    """
    _positive(counter_width, "counter_width")
    if not permanences:
        raise ValueError("permanences must not be empty")
    _unsigned(increment, counter_width, "increment")
    _unsigned(decrement, counter_width, "decrement")
    maximum = (1 << counter_width) - 1
    if isinstance(active, int):
        _unsigned(active, len(permanences), "active")
        flags = [bool((active >> index) & 1) for index in range(len(permanences))]
    else:
        flags = list(active)
        if len(flags) != len(permanences) or any(flag not in (False, True) for flag in flags):
            raise ValueError("active must have one Boolean flag per permanence")
    return [min(maximum, value + increment) if flag else max(0, value - decrement)
            for value, flag in zip((_unsigned(value, counter_width, "permanence")
                                    for value in permanences), flags)]
