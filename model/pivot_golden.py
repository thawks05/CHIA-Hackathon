"""
pivot_model.py - the golden reference model ("answer key") for PIVOT.

Lives in model/. It never reads the Verilog. It does each operation the
slow, obvious way so a human can check it by eye. The testbench feeds the
same inputs to this model and to pivot_tile.v and compares every bit.

Conventions (these MUST match the RTL, or the diff will report false bugs):
  * A vector is a plain Python int. Bit 0 is the LSB, like [WIDTH-1:0].
  * Every result is masked back to its Verilog width, because Python ints
    never overflow or truncate on their own.
  * Flattened buses: item i sits at bits [i*W +: W], same as the
    indexed part-select in the RTL.

Decisions flagged ASSUMPTION below are ones we have not pinned down in the
RTL yet. Check each against the Verilog and change whichever side is wrong.
"""

import random


class PivotModel:
    # ASSUMPTION: opcode encoding. Must match the case statement in pivot_tile.v.
    OP_XOR = 0
    OP_MAJORITY = 1
    OP_SHIFT = 2
    OP_HAMMING = 3
    OP_TOPK = 4
    OP_COUNTER = 5

    def __init__(self, width=512, n=4, counter_width=8, k=4):
        self.width = width
        self.n = n
        self.counter_width = counter_width
        self.k = k

        self.mask = (1 << width) - 1
        self.counter_max = (1 << counter_width) - 1
        self.idx_bits = max(1, (width - 1).bit_length())   # $clog2(WIDTH)

        # pivot_counter is the only stateful module, so the model keeps state too.
        self.counters = [0] * width

    # ------------------------------------------------------------------
    # Helpers for flattened buses
    # ------------------------------------------------------------------
    @staticmethod
    def unpack(bus, count, item_width):
        """Split a flat bus into a list. Item 0 is the lowest bits."""
        item_mask = (1 << item_width) - 1
        return [(bus >> (i * item_width)) & item_mask for i in range(count)]

    @staticmethod
    def pack(items, item_width):
        """Inverse of unpack: item 0 goes in the lowest bits."""
        bus = 0
        for i, value in enumerate(items):
            bus |= (value & ((1 << item_width) - 1)) << (i * item_width)
        return bus

    # ------------------------------------------------------------------
    # The six primitive operations (combinational ones are pure functions)
    # ------------------------------------------------------------------
    def xor(self, operand_a, operand_b):
        """pivot_xor: bitwise XOR. In HDC this is 'binding'."""
        return (operand_a ^ operand_b) & self.mask

    def majority(self, vectors_in, threshold):
        """
        pivot_majority: for each bit position, count how many of the N vectors
        have a 1 there; output 1 if count >= threshold.
        vectors_in is the flattened [N*WIDTH-1:0] bus. Matches the RTL's '>='.
        threshold is [$clog2(N+1)-1:0], so it is masked to that width first.
        """
        threshold &= (1 << max(1, self.n.bit_length())) - 1
        vectors = self.unpack(vectors_in, self.n, self.width)
        result = 0
        for bit_pos in range(self.width):
            count = sum((v >> bit_pos) & 1 for v in vectors)
            if count >= threshold:
                result |= 1 << bit_pos
        return result

    def shift(self, operand, shift_amount):
        """
        pivot_shift: cyclic LEFT rotation. Bits falling off the top come back
        at the bottom. shift_amount is [$clog2(WIDTH)-1:0], so it is masked,
        which also makes a shift of WIDTH behave like a shift of 0.
        """
        s = shift_amount & ((1 << self.idx_bits) - 1)
        s %= self.width
        return ((operand << s) | (operand >> (self.width - s))) & self.mask

    def hamming(self, operand_a, operand_b):
        """pivot_hamming: number of bit positions where a and b differ (0..WIDTH)."""
        return bin((operand_a ^ operand_b) & self.mask).count("1")

    def topk(self, scores):
        """
        pivot_topk: return the indices of the k largest scores.
        ASSUMPTION: scores are the WIDTH counter values (HTM: pick the winning
        columns). Slot 0 holds the largest score. Ties: the LOWER index wins.
        Returns the packed [k*$clog2(WIDTH)-1:0] bus, slot 0 in the low bits.
        """
        order = sorted(range(len(scores)), key=lambda i: (-scores[i], i))
        winners = order[: self.k]
        return self.pack(winners, self.idx_bits)

    def counter_step(self, rst, enable, vector_in):
        """
        pivot_counter: one rising clock edge.
          rst=1             -> every counter goes to 0 (reset wins over enable)
          enable=1          -> counter i += 1 wherever vector_in bit i is 1,
                               saturating at 2**COUNTER_WIDTH - 1 (no wraparound)
          otherwise         -> counters hold their value
        Returns the packed [WIDTH*COUNTER_WIDTH-1:0] counters_out bus.
        """
        if rst:
            self.counters = [0] * self.width
        elif enable:
            for i in range(self.width):
                if (vector_in >> i) & 1:
                    self.counters[i] = min(self.counters[i] + 1, self.counter_max)
        return self.packed_counters()

    def packed_counters(self):
        return self.pack(self.counters, self.counter_width)

    def reset(self):
        self.counters = [0] * self.width

    # ------------------------------------------------------------------
    # pivot_tile: the opcode mux. This is what the testbench compares against.
    # ------------------------------------------------------------------
    def tile(self, opcode, operand_a=0, operand_b=0, vectors_in=0,
             threshold=0, shift_amount=0, rst=0):
        """
        One operation through the tile. Returns a dict with the output buses.
        Only compare the bus(es) listed in RELEVANT_OUTPUTS for this opcode;
        the others are whatever the RTL mux drives when unselected.

        ASSUMPTIONS:
          * the counter only updates when opcode == OP_COUNTER (enable = opcode match),
            and its input vector is operand_a
          * top-k ranks the CURRENT counter values
        """
        out = {
            "result_vector": 0,
            "result_distance": 0,
            "result_topk_idx": 0,
            "result_counters": self.packed_counters(),
        }

        if opcode == self.OP_XOR:
            out["result_vector"] = self.xor(operand_a, operand_b)
        elif opcode == self.OP_MAJORITY:
            out["result_vector"] = self.majority(vectors_in, threshold)
        elif opcode == self.OP_SHIFT:
            out["result_vector"] = self.shift(operand_a, shift_amount)
        elif opcode == self.OP_HAMMING:
            out["result_distance"] = self.hamming(operand_a, operand_b)
        elif opcode == self.OP_TOPK:
            out["result_topk_idx"] = self.topk(self.counters)
        elif opcode == self.OP_COUNTER:
            out["result_counters"] = self.counter_step(rst, 1, operand_a)
        else:
            raise ValueError(f"unknown opcode {opcode}")

        return out

    RELEVANT_OUTPUTS = {
        OP_XOR: ["result_vector"],
        OP_MAJORITY: ["result_vector"],
        OP_SHIFT: ["result_vector"],
        OP_HAMMING: ["result_distance"],
        OP_TOPK: ["result_topk_idx"],
        OP_COUNTER: ["result_counters"],
    }

    # ------------------------------------------------------------------
    # Random stimulus for the differential test (fixed seed = fixed trace)
    # ------------------------------------------------------------------
    def random_op(self, rng):
        """One random operation's inputs, as a dict you can pass to tile()."""
        return {
            "opcode": rng.randrange(6),
            "operand_a": rng.getrandbits(self.width),
            "operand_b": rng.getrandbits(self.width),
            "vectors_in": rng.getrandbits(self.n * self.width),
            "threshold": rng.randrange(self.n + 1),
            "shift_amount": rng.randrange(self.width),
        }

    def make_trace(self, num_ops, seed=0):
        """A reproducible list of random operations. Same seed -> same trace."""
        rng = random.Random(seed)
        return [self.random_op(rng) for _ in range(num_ops)]


# ----------------------------------------------------------------------
# Self-checks: tiny hand-verifiable cases. Run `python pivot_model.py`.
# If any of these fail, the model is wrong and nothing downstream counts.
# ----------------------------------------------------------------------
def _self_test():
    m = PivotModel(width=8, n=4, counter_width=2, k=2)

    # XOR
    assert m.xor(0b11001100, 0b10101010) == 0b01100110

    # Shift: cyclic left
    assert m.shift(0b10000001, 1) == 0b00000011
    assert m.shift(0b10000001, 0) == 0b10000001
    assert m.shift(0b00000001, 7) == 0b10000000

    # Hamming
    assert m.hamming(0b11110000, 0b11110000) == 0
    assert m.hamming(0b11111111, 0b00000000) == 8
    assert m.hamming(0b10100000, 0b00000101) == 4

    # Majority: bit 0 set in 3 of 4 vectors, bit 1 in 1 of 4, bit 2 in 4 of 4
    vs = [0b101, 0b101, 0b111, 0b100]
    bus = m.pack(vs, 8)
    assert m.majority(bus, 3) == 0b101
    assert m.majority(bus, 1) == 0b111
    assert m.majority(bus, 0) == 0b11111111   # count >= 0 is always true
    assert m.majority(bus, 4) == 0b100

    # Counter: saturates at 3 for counter_width=2
    m.reset()
    for _ in range(5):
        m.counter_step(rst=0, enable=1, vector_in=0b00000001)
    assert m.counters[0] == 3
    m.counter_step(rst=0, enable=0, vector_in=0b11111111)
    assert m.counters[1] == 0                  # enable=0 holds
    m.counter_step(rst=1, enable=1, vector_in=0b11111111)
    assert m.counters == [0] * 8              # reset wins

    # Top-k: largest first, lower index wins ties
    idx = m.unpack(m.topk([1, 3, 3, 0, 2, 0, 0, 0]), 2, m.idx_bits)
    assert idx == [1, 2]

    # Tile routes through the mux
    out = m.tile(PivotModel.OP_HAMMING, operand_a=0b1, operand_b=0b0)
    assert out["result_distance"] == 1

    # Traces are reproducible
    assert PivotModel().make_trace(5, seed=42) == PivotModel().make_trace(5, seed=42)

    print("pivot_model self-test: all checks passed")


if __name__ == "__main__":
    _self_test()