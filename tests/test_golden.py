import random
import unittest

from model.pivot_golden import (PivotGolden, evaluate_case, hdc_encode, hdc_query,
                               pack_fields, permanence_update, rotate_left,
                               spatial_pooler, unpack_fields)


class GoldenTests(unittest.TestCase):
    def test_packing_literal(self):
        self.assertEqual(pack_fields([1, 2, 3], 4), 0x321)
        self.assertEqual(unpack_fields(0x321, 4, 3), [1, 2, 3])

    def test_known_six_operations_and_zeroed_other_buses(self):
        golden = PivotGolden(4, 3, 2)
        vectors = [0b0000, 0b1111, 0b0011]
        self.assertEqual(golden.evaluate(0, 0b1010, 0b0011, vectors),
                         dict(result_vector=9, result_distance=0, result_aux=0))
        self.assertEqual(golden.evaluate(1, 0b1001, 1, vectors)["result_vector"], 3)
        self.assertEqual(golden.evaluate(2, 2, 0, vectors)["result_vector"], 3)
        self.assertEqual(golden.evaluate(3, 0, 0, vectors)["result_aux"], 0b01011010)
        self.assertEqual(golden.evaluate(4, 0, 0, vectors)["result_distance"], 0b010100000)
        self.assertEqual(golden.evaluate(5, 0, 0, vectors)["result_aux"], 0b1000)
        for opcode in (6, 7):
            self.assertEqual(golden.evaluate(opcode, 15, 15, vectors),
                             dict(result_vector=0, result_distance=0, result_aux=0))

    def test_independent_naive_reference_random_parameters(self):
        rng = random.Random(88253)
        for width, n, k in ((1, 1, 1), (1, 9, 9), (3, 5, 2), (7, 3, 3), (16, 8, 1), (512, 8, 3)):
            golden = PivotGolden(width, n, k)
            mask = (1 << width) - 1
            for sample in range(60):
                a, b = rng.getrandbits(width), rng.getrandbits(width)
                vectors = [rng.getrandbits(width) for _ in range(n)]
                counts = [sum((value >> bit) & 1 for value in vectors) for bit in range(width)]
                distances = [sum(((a >> bit) & 1) != ((v >> bit) & 1) for bit in range(width)) for v in vectors]
                amount = (b % (1 << max(1, (width - 1).bit_length()))) % width
                threshold = a % (1 << n.bit_length())
                rotated = sum(((a >> bit) & 1) << ((bit + amount) % width) for bit in range(width))
                expected = (
                    {"result_vector": a ^ b},
                    {"result_vector": rotated},
                    {"result_vector": sum(int(count >= threshold) << bit for bit, count in enumerate(counts))},
                    {"result_aux": sum(count << (bit * n.bit_length()) for bit, count in enumerate(counts))},
                    {"result_distance": sum(d << (i * width.bit_length()) for i, d in enumerate(distances))},
                    {"result_aux": sum(i << (rank * max(1, (n - 1).bit_length())) for rank, i in
                                       enumerate(sorted(range(n), key=lambda i: (distances[i], i))[:k]))},
                )
                for opcode, values in enumerate(expected):
                    answer = dict(result_vector=0, result_distance=0, result_aux=0)
                    answer.update(values)
                    self.assertEqual(golden.evaluate(opcode, a, b, vectors), answer, (width, n, k, sample, opcode))
                self.assertLessEqual(golden.evaluate(2, a, b, vectors)["result_vector"], mask)

    def test_ties_and_threshold_boundaries(self):
        golden = PivotGolden(8, 4, 4)
        self.assertEqual(unpack_fields(golden.evaluate(5, 0, 0, [1, 2, 4, 8])["result_aux"], 2, 4), [0, 1, 2, 3])
        self.assertEqual(golden.evaluate(2, 0, 0, [0] * 4)["result_vector"], 255)
        self.assertEqual(golden.evaluate(2, 5, 0, [255] * 4)["result_vector"], 0)
        self.assertEqual(golden.evaluate(2, 4, 0, [255] * 4)["result_vector"], 255)

    def test_non_power_of_two_rotation_and_width_one(self):
        self.assertEqual(PivotGolden(5, 1, 1).evaluate(1, 1, 6, [0])["result_vector"], 2)
        self.assertEqual(PivotGolden(1, 1, 1).evaluate(1, 1, 1, [0])["result_vector"], 1)
        self.assertEqual(rotate_left(9, 4, 4), 9)

    def test_parameter_and_input_validation(self):
        for args in ((0, 1, 1), (4, 0, 1), (4, 1, 0), (4, 1, 2), (True, 1, 1)):
            with self.assertRaises(ValueError):
                PivotGolden(*args)
        golden = PivotGolden(4, 2, 1)
        for args in ((8, 0, 0, [0, 0]), (0, 16, 0, [0, 0]), (0, -1, 0, [0, 0]),
                     (0, 0, 0, [0]), (0, 0, 0, [16, 0]), (0, 0, 0, 256)):
            with self.assertRaises(ValueError):
                golden.evaluate(*args)
        self.assertEqual(evaluate_case(dict(opcode=0, operand_a=1, operand_b=3, vectors_in=0), 4, 2, 1)["result_vector"], 2)
        self.assertEqual(PivotGolden(1, 9, 9).aux_width, 36)

    def test_hdc_bind_bundle_query(self):
        self.assertEqual(hdc_encode([0, 0, 0], [0b1100, 0b1010, 0b1111], 4), 0b1110)
        self.assertEqual(hdc_encode([0, 0], [0b1111, 0], 4), 0)
        self.assertEqual(hdc_query(0, [3, 1, 2, 15], width=4, k=3), [(1, 1), (2, 1), (0, 2)])
        with self.assertRaises(ValueError):
            hdc_encode([], [], 4)

    def test_spatial_pooler_global_inhibition(self):
        self.assertEqual(spatial_pooler(0b1011, [0b1111, 0b0011, 0b1001, 0], 4, 2),
                         dict(overlaps=[3, 2, 2, 0], winners=[0, 1], active_columns=3))
        self.assertEqual(spatial_pooler(0, [15, 7], 4, 2)["winners"], [])
        self.assertEqual(spatial_pooler(0, [15, 7], 4, 2, min_overlap=0)["winners"], [0, 1])

    def test_permanence_saturation_and_lane_packing(self):
        self.assertEqual(permanence_update([0, 2, 14, 15], 0b1100, 3, 3, 4), [0, 0, 15, 15])
        self.assertEqual(permanence_update([4, 4], [True, False], 2, 1, 4), [6, 3])
        self.assertEqual(permanence_update([0, 15], 3, 0, 0, 4), [0, 15])
        with self.assertRaises(ValueError):
            permanence_update([0], 2)


if __name__ == "__main__":
    unittest.main()
