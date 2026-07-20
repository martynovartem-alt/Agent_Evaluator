"""Fake MCP generator — determinism and real-shape validity."""
import unittest

from gen_mcp import generate
from tools import rubles

REQUIRED = {"id", "operationId", "dateTime", "operationDate", "title", "amount",
            "direction", "operationType", "merchantDto"}


class TestGenMcp(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(generate(seed=7), generate(seed=7))

    def test_seed_changes_output(self):
        self.assertNotEqual(generate(seed=7), generate(seed=8))

    def test_shape_and_amounts(self):
        data = generate(seed=7, n_users=3)
        self.assertEqual(len(data), 3)
        for ops in data.values():
            self.assertTrue(ops)
            for op in ops:
                self.assertTrue(REQUIRED <= op.keys())
                self.assertEqual(op["amount"]["minorUnits"], 100)
                self.assertEqual(rubles(op["amount"]), int(rubles(op["amount"])))  # whole rubles
                self.assertIn(op["direction"], {"EXPENSE", "INCOME"})

    def test_operations_sorted_by_date(self):
        for ops in generate(seed=7).values():
            dates = [o["operationDate"] for o in ops]
            self.assertEqual(dates, sorted(dates))


if __name__ == "__main__":
    unittest.main()
