"""Aggregator: policy, metric rollups (applicable-gating, verdict distribution), report shape."""
import tempfile
import unittest
from pathlib import Path

from aggregate import _pct, aggregate_results, format_report, is_solved, summarize_metrics


def _result(case_id, *, verdict="yes", grounded=True, tools=True, facts=True,
            instruction_ok=None, planted_ok=None):
    return {
        "case_id": case_id,
        "checks": {
            "tools_ok": tools,
            "all_must_facts_present": facts,
            "instruction_ok": instruction_ok,
            "planted_operation_ok": planted_ok,
        },
        "groundedness": {"has_unsupported_critical_claim": not grounded},
        "resolution": {"verdict": verdict, "resolution_yes": verdict == "yes"},
    }


class TestPolicy(unittest.TestCase):
    def test_solved_requires_all_three(self):
        self.assertTrue(is_solved(_result("a")))
        self.assertFalse(is_solved(_result("a", verdict="partial")))
        self.assertFalse(is_solved(_result("a", grounded=False)))
        self.assertFalse(is_solved(_result("a", tools=False)))


class TestPct(unittest.TestCase):
    def test_pct_and_na(self):
        self.assertEqual(_pct(1, 4), 25.0)
        self.assertIsNone(_pct(0, 0))


class TestSummarize(unittest.TestCase):
    def test_rollups_and_applicable_gating(self):
        results = [
            _result("a", verdict="yes", instruction_ok=True, planted_ok=True),
            _result("b", verdict="partial", instruction_ok=False),   # planted N/A
            _result("c", verdict="no", grounded=False),               # instr & planted N/A
        ]
        m = summarize_metrics(results)
        self.assertEqual(m["resolution_verdicts"], {"yes": 1, "partial": 1, "no": 1})
        self.assertEqual(m["pct_grounded"], round(2 / 3 * 100, 1))
        self.assertEqual(m["instruction_applicable"], 2)   # a,b
        self.assertEqual(m["pct_instruction_ok"], 50.0)    # a true, b false
        self.assertEqual(m["planted_applicable"], 1)       # only a
        self.assertEqual(m["pct_planted_operation_ok"], 100.0)


class TestAggregateResults(unittest.TestCase):
    def test_report_shape_and_no_diff_on_first_run(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / "20260721T000000"
            run_dir.mkdir()
            report = aggregate_results([_result("a"), _result("b", verdict="no")], run_dir)
            self.assertEqual(report["total"], 2)
            self.assertEqual(report["solved"], 1)
            self.assertEqual(report["pct_solved"], 50.0)
            self.assertNotIn("diff", report)  # no previous run
            self.assertEqual({c["case_id"] for c in report["cases"]}, {"a", "b"})
            self.assertIn("Solved: 1/2", format_report(report))


if __name__ == "__main__":
    unittest.main()
