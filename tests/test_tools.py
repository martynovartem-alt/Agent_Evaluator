"""Tool layer — determinism, filtering, amount encoding. The harness's reproducibility rests here."""
import unittest

from tools import get_instruction, mcp_clear, rubles


class TestMcpClear(unittest.TestCase):
    def test_returns_ops_in_window(self):
        res = mcp_clear("user_alfa", "2026-07-01", "2026-07-31")
        ids = {o["id"] for o in res["operations"]}
        self.assertIn("op_smart", ids)
        self.assertEqual(res["summary"]["operationsCount"], len(res["operations"]))

    def test_date_window_excludes_out_of_range(self):
        res = mcp_clear("user_alfa", "2026-01-01", "2026-02-01")
        self.assertEqual(res["operations"], [])

    def test_to_date_is_exclusive(self):
        # op_smart is dated 2026-07-12; toDate == that day must exclude it.
        res = mcp_clear("user_alfa", "2026-07-01", "2026-07-12")
        self.assertNotIn("op_smart", {o["id"] for o in res["operations"]})

    def test_amount_filter(self):
        res = mcp_clear("user_alfa", "2026-07-01", "2026-07-31", operation_amount=299)
        self.assertEqual([o["id"] for o in res["operations"]], ["op_smart"])

    def test_summary_totals(self):
        res = mcp_clear("user_alfa", "2026-07-01", "2026-07-31")
        self.assertEqual(res["summary"]["totalExpense"], 299 + 340)
        self.assertEqual(res["summary"]["totalIncome"], 0)

    def test_unknown_user_is_empty(self):
        self.assertEqual(mcp_clear("nobody", "2026-01-01", "2027-01-01")["operations"], [])


class TestRubles(unittest.TestCase):
    def test_kopecks_to_rubles(self):
        self.assertEqual(rubles({"value": 29900, "minorUnits": 100}), 299)
        self.assertEqual(rubles({"value": 34000, "minorUnits": 100}), 340)


class TestGetInstruction(unittest.TestCase):
    def test_known_key(self):
        instr = get_instruction(["alfaSmart"])
        self.assertIn("alfaSmart", instr)
        self.assertIn("Альфа-Смарт", instr["alfaSmart"])

    def test_unknown_key_omitted(self):
        self.assertEqual(get_instruction(["does_not_exist"]), {})


if __name__ == "__main__":
    unittest.main()
